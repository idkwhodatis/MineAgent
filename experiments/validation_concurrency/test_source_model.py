"""Reconstructed offline source/adapter tests, not SQL/concurrency evidence."""
import ast
from contextlib import contextmanager
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

import run_two_sessions as runner

SCHEMA = "rd_coord_model_" + "a" * 32


class SourceTests(unittest.TestCase):
    def test_render_owned_schema(self):
        sql = runner.render(SCHEMA, 123)
        self.assertNotIn("__SCHEMA__", sql)
        self.assertNotIn("__KEY__", sql)
        self.assertIn("pg_try_advisory_xact_lock(123::bigint)", sql)

    def test_reject_arbitrary_schema(self):
        for value in ("public", SCHEMA + "; DROP SCHEMA public", '"' + SCHEMA):
            with self.assertRaises(ValueError):
                runner.render(value, 123)

    def test_reject_invalid_key(self):
        for value in (True, 0, -1, 2**63, "1"):
            with self.assertRaises(ValueError):
                runner.render(SCHEMA, value)

    def test_all_tables_statement_admitted(self):
        sql = runner.render(SCHEMA, 123)
        for table in ("parent", "workflow", "receipt"):
            self.assertIn(f"ON {SCHEMA}.{table} FOR EACH STATEMENT EXECUTE FUNCTION {SCHEMA}.writer_gate()", sql)
        self.assertEqual(sql.count("BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE"), 3)

    def test_all_tables_deferred_validation(self):
        sql = runner.render(SCHEMA, 123)
        self.assertEqual(sql.count("DEFERRABLE INITIALLY DEFERRED"), 3)
        self.assertEqual(sql.count("EXECUTE FUNCTION " + SCHEMA + ".validate_graph()"), 3)

    def test_truncate_rejected_not_silently_unchecked(self):
        sql = runner.render(SCHEMA, 123)
        self.assertEqual(sql.count("CREATE TRIGGER no_truncate"), 3)
        self.assertIn("ERRCODE='0A000'", sql)

    def test_no_authoritative_validated_flag_or_dedup(self):
        sql = runner.render(SCHEMA, 123)
        for token in ("current_setting", "SECURITY DEFINER", "CREATE ROLE", "ALTER ROLE"):
            self.assertNotIn(token, sql)

    def test_runner_parses_without_execution(self):
        ast.parse(Path(runner.__file__).read_text())
        self.assertIn("--execute-permitted-local", Path(runner.__file__).read_text())

    def test_no_execution_without_opt_in(self):
        with patch.object(sys, "argv", ["run_two_sessions.py"]), patch.object(runner, "run") as execute:
            with self.assertRaises(SystemExit):
                runner.main()
            execute.assert_not_called()

    def test_no_default_dsn(self):
        with patch.object(sys, "argv", ["run_two_sessions.py", "--execute-permitted-local"]), patch.dict("os.environ", {}, clear=True), patch.object(runner, "run") as execute:
            with self.assertRaises(SystemExit):
                runner.main()
            execute.assert_not_called()

    def test_runner_has_no_optimizable_assertions(self):
        tree = ast.parse(Path(runner.__file__).read_text())
        self.assertFalse(any(isinstance(node, ast.Assert) for node in ast.walk(tree)))

    def parse_double(self, params, environment=None):
        module = types.SimpleNamespace(conninfo_to_dict=lambda value: params)
        with patch.dict(sys.modules, {"psycopg.conninfo": module}), patch.dict("os.environ", environment or {}, clear=True):
            return runner.local_connection_parameters("synthetic")

    def test_local_parameters_require_explicit_unambiguous_destination(self):
        valid = {"host": "/permitted/socket", "port": "5432", "dbname": "model", "user": "model"}
        variants = [
            {**valid, "host": "remote.example"},
            {**valid, "host": "/a,/b"},
            {**valid, "hostaddr": "192.0.2.1"},
            {**valid, "service": "remote"},
            {**valid, "options": "-c search_path=other"},
            {**valid, "port": "5432,5433"},
            {**valid, "port": "0"},
            {**valid, "port": "65536"},
            {**valid, "user": ""},
            {**valid, "dbname": ""},
            {**valid, "connect_timeout": "0"},
            {**valid, "connect_timeout": "60"},
        ]
        for params in variants:
            with self.subTest(params=params), self.assertRaises(ValueError):
                self.parse_double(params)

    def test_local_parameters_reject_ambient_pg_settings(self):
        valid = {"host": "/permitted/socket", "port": "5432", "dbname": "model", "user": "model"}
        for key in ("PGSERVICE", "PGSERVICEFILE", "PGHOSTADDR", "PGHOST", "PGOPTIONS"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.parse_double(valid, {key: "synthetic"})

    def test_local_parameters_set_bounded_timeout(self):
        valid = {"host": "/permitted/socket", "port": "5432", "dbname": "model", "user": "model"}
        self.assertEqual(self.parse_double(valid), {**valid, "connect_timeout": "3"})
        self.assertEqual(valid, {"host": "/permitted/socket", "port": "5432", "dbname": "model", "user": "model"})


class FakeError(Exception):
    def __init__(self, state):
        super().__init__("private payload must not escape")
        self.sqlstate = state


class FakeConnection:
    def __init__(self, state):
        self.state = state
        self.exited = False

    @contextmanager
    def transaction(self):
        try:
            yield
        finally:
            self.exited = True

    def execute(self, statement):
        if self.state:
            raise FakeError(self.state)


class ErrorAdapterTests(unittest.TestCase):
    def test_expected_category_and_transaction_exit(self):
        conn = FakeConnection("55P03")
        with patch.dict(sys.modules, {"psycopg": types.SimpleNamespace(Error=FakeError)}):
            runner.expected_error(conn, "synthetic", "55P03")
        self.assertTrue(conn.exited)

    def test_wrong_category_sanitized(self):
        with patch.dict(sys.modules, {"psycopg": types.SimpleNamespace(Error=FakeError)}):
            with self.assertRaisesRegex(AssertionError, "unexpected database category") as caught:
                runner.expected_error(FakeConnection("XX000"), "synthetic", "55P03")
        self.assertNotIn("private payload", str(caught.exception))

    def test_missing_rejection_fails(self):
        with patch.dict(sys.modules, {"psycopg": types.SimpleNamespace(Error=FakeError)}):
            with self.assertRaisesRegex(AssertionError, "expected rejection missing"):
                runner.expected_error(FakeConnection(None), "synthetic", "55P03")


if __name__ == "__main__":
    unittest.main()
