"""Runner orchestration doubles, explicitly not PostgreSQL execution evidence."""
from contextlib import contextmanager
import json
import sys
import types
import unittest
from unittest.mock import Mock, patch

import run_two_sessions as runner
from proof_assertions import ProofFailure


PRIVATE = "synthetic private DSN/database payload must never be reported"


class FakeError(Exception):
    sqlstate = "XX000"

    def __init__(self):
        super().__init__(PRIVATE)
        self.diag = types.SimpleNamespace(message_primary=PRIVATE)


class Row:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return (self.value,)


class Connection:
    def __init__(self, pid, *, fail_install=False, fail_drop=False,
                 schema_remains=False, fail_close=False, fail_first_statement=False,
                 isolation="read committed"):
        self.pid = pid
        self.fail_install = fail_install
        self.fail_drop = fail_drop
        self.schema_remains = schema_remains
        self.fail_close = fail_close
        self.fail_first_statement = fail_first_statement
        self.isolation = isolation
        self.executed = []
        self.boundaries = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if self.fail_close:
            raise FakeError()
        return False

    @contextmanager
    def transaction(self):
        self.boundaries.append("begin")
        try:
            yield
        except Exception:
            self.boundaries.append("rollback")
            raise
        else:
            self.boundaries.append("commit")

    def execute(self, sql, parameters=None, **kwargs):
        self.executed.append(sql)
        if len(self.executed) == 1 and self.fail_first_statement:
            raise FakeError()
        if "CREATE SCHEMA" in sql and self.fail_install:
            raise FakeError()
        if sql.startswith("DROP SCHEMA") and self.fail_drop:
            raise FakeError()
        if sql == "SHOW transaction_isolation":
            return Row(self.isolation)
        if sql == "SELECT pg_backend_pid()":
            return Row(self.pid)
        if sql.startswith("SELECT to_regnamespace"):
            return Row("owned_schema_still_present" if self.schema_remains else None)
        return Row(None)


class RunnerLifecycleTests(unittest.TestCase):
    def invoke(self, a=None, b=None, *, connect_error=None, params_error=None,
               case_error=None, case_records=None):
        a, b = a or Connection(1), b or Connection(2)
        connect = Mock(side_effect=connect_error or [a, b])
        module = types.SimpleNamespace(Error=FakeError, connect=connect)
        params = Mock(side_effect=params_error, return_value={"host": "/synthetic"})

        def cases(_a, _b, _schema, report):
            report["stage"] = "synthetic_case"
            if case_error:
                raise case_error
            report["cases_passed"].extend(
                [{"case": name} for name in runner.EXPECTED_CASES]
                if case_records is None else case_records)

        with patch.dict(sys.modules, {"psycopg": module}), \
                patch.object(runner, "local_connection_parameters", params), \
                patch.object(runner, "exercise_cases", cases):
            result = runner.run(PRIVATE)
        self.assertNotIn(PRIVATE, json.dumps(result))
        return result, a, b, connect

    def test_invalid_parameters_never_connect_or_claim_cleanup_uncertainty(self):
        result, _, _, connect = self.invoke(params_error=ValueError(PRIVATE))
        connect.assert_not_called()
        self.assertFalse(result["passed"])
        self.assertFalse(result["sql_attempted"])
        self.assertFalse(result["sql_execution_confirmed"])
        self.assertEqual(result["cleanup"]["status"], "not_started")
        self.assertFalse(result["cleanup_requires_verification"])
        self.assertEqual(result["failure"]["stage"], "connection_parameters")

    def test_connection_failure_is_before_model_installation(self):
        result, _, _, connect = self.invoke(connect_error=FakeError())
        self.assertEqual(connect.call_count, 1)
        self.assertEqual(result["failure"]["stage"], "connect")
        self.assertFalse(result["cleanup_requires_verification"])

    def test_same_backend_cannot_count_as_two_sessions(self):
        result, a, _, _ = self.invoke(a=Connection(1), b=Connection(1))
        self.assertFalse(result["independent_backends"])
        self.assertFalse(any("CREATE SCHEMA" in sql for sql in a.executed))
        self.assertEqual(result["failure"]["reason"], "independent backends required")

    def test_unknown_first_statement_outcome_is_not_reported_unattempted(self):
        result, _, _, _ = self.invoke(a=Connection(1, fail_first_statement=True))
        self.assertTrue(result["sql_attempted"])
        self.assertFalse(result["sql_execution_confirmed"])
        self.assertEqual(result["cleanup"]["status"], "not_started")

    def test_configuration_failure_retains_confirmed_sql_execution(self):
        result, _, _, _ = self.invoke(a=Connection(1, isolation="repeatable read"))
        self.assertTrue(result["sql_attempted"])
        self.assertTrue(result["sql_execution_confirmed"])
        self.assertFalse(result["independent_backends"])
        self.assertEqual(result["cleanup"]["status"], "not_started")

    def test_unknown_install_outcome_does_not_drop_unconfirmed_schema(self):
        result, a, _, _ = self.invoke(a=Connection(1, fail_install=True))
        self.assertEqual(result["failure"]["stage"], "install_model")
        self.assertEqual(result["cleanup"]["status"], "creation_unconfirmed")
        self.assertTrue(result["cleanup_requires_verification"])
        self.assertFalse(any(sql.startswith("DROP SCHEMA") for sql in a.executed))

    def test_case_failure_stays_failed_after_successful_cleanup(self):
        result, a, _, _ = self.invoke(case_error=ProofFailure("expected rejection missing", "synthetic_case", "statement"))
        self.assertFalse(result["passed"])
        self.assertEqual(result["failure"]["stage"], "synthetic_case")
        self.assertEqual(result["cleanup"]["status"], "verified_absent")
        self.assertTrue(result["cleanup_completed"])
        self.assertFalse(result["cleanup_requires_verification"])
        self.assertTrue(any(sql.startswith("DROP SCHEMA") for sql in a.executed))

    def test_cleanup_failure_cannot_report_success(self):
        result, _, _, _ = self.invoke(a=Connection(1, fail_drop=True))
        self.assertFalse(result["passed"])
        self.assertEqual(result["cleanup"]["status"], "needs_verification")
        self.assertTrue(result["cleanup_requires_verification"])
        self.assertEqual(result["cleanup_failure"]["stage"], "cleanup")

    def test_schema_absence_must_be_observed_after_drop(self):
        result, _, _, _ = self.invoke(a=Connection(1, schema_remains=True))
        self.assertFalse(result["passed"])
        self.assertEqual(result["cleanup_failure"]["reason"], "owned schema remains")

    def test_mock_success_requires_cases_cleanup_and_distinct_backends(self):
        result, _, _, connect = self.invoke()
        self.assertTrue(result["passed"])
        self.assertTrue(result["independent_backends"])
        self.assertTrue(result["cleanup_completed"])
        self.assertEqual(len(result["cases_passed"]), 18)
        self.assertTrue(result["case_manifest_verified"])
        self.assertEqual(connect.call_count, 2)
        self.assertFalse(result["production_acceptance"])

    def test_close_failure_is_not_hidden_by_successful_cleanup(self):
        result, _, _, _ = self.invoke(b=Connection(2, fail_close=True))
        self.assertFalse(result["passed"])
        self.assertEqual(result["failure"]["stage"], "connection_close")
        self.assertEqual(result["cleanup"]["status"], "verified_absent")

    def assert_incomplete(self, records):
        result, a, _, _ = self.invoke(case_records=records)
        self.assertFalse(result["passed"])
        self.assertFalse(result["case_manifest_verified"])
        self.assertEqual(result["failure"]["reason"], "case manifest incomplete or unexpected")
        self.assertEqual(result["failure"]["stage"], "verify_case_completion")
        self.assertEqual(result["cleanup"]["status"], "verified_absent")
        self.assertTrue(any(sql.startswith("DROP SCHEMA") for sql in a.executed))

    def test_no_cases_cannot_report_success(self):
        self.assert_incomplete([])

    def test_partial_case_run_cannot_report_success(self):
        self.assert_incomplete([{"case": name} for name in runner.EXPECTED_CASES[:-1]])

    def test_unknown_case_cannot_report_success(self):
        self.assert_incomplete([{"case": "unknown_case"}])

    def test_duplicate_case_cannot_substitute_for_missing_case(self):
        self.assert_incomplete([{"case": runner.EXPECTED_CASES[0]}] * 18)

    def test_reordered_cases_do_not_match_deterministic_schedule(self):
        self.assert_incomplete([{"case": name} for name in reversed(runner.EXPECTED_CASES)])

    def test_malformed_case_record_cannot_report_success(self):
        for record in (None, "malformed_case_record", 18, {}, {"case": None}):
            with self.subTest(record=record):
                self.assert_incomplete([record])

    def test_manifest_matches_published_eighteen_case_plan(self):
        from pathlib import Path
        plan = json.loads(Path(runner.__file__).with_name("RESULTS.json").read_text())
        self.assertEqual(tuple(plan["planned_postgres_case_groups"]), runner.EXPECTED_CASES)
        self.assertEqual(len(set(runner.EXPECTED_CASES)), 18)


if __name__ == "__main__":
    unittest.main()
