"""Offline phase/error doubles only: no PostgreSQL or concurrency evidence."""
import json
import sys
import types
import unittest
from unittest.mock import patch

import proof_assertions as proof


PRIVATE = "private database payload / marker must not escape"
STATE = "55P03"
MARKER = "model_writer_busy"


class FakeError(Exception):
    def __init__(self, state=STATE, marker=MARKER):
        super().__init__(PRIVATE)
        self.sqlstate = state
        self.diag = types.SimpleNamespace(message_primary=marker)


class FakeTransaction:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        self.connection.events.append("begin")
        if self.connection.begin_error is not None:
            raise self.connection.begin_error
        return self

    def __exit__(self, kind, value, traceback):
        action = "rollback" if kind is not None else "commit"
        self.connection.events.append(action)
        self.connection.exit_exception = value
        error = (self.connection.rollback_error if kind is not None
                 else self.connection.commit_error)
        if self.connection.exit_status is not None:
            self.connection.info.transaction_status.name = self.connection.exit_status
        if error is not None:
            raise error
        return self.connection.suppress


class FakeConnection:
    def __init__(self, *, execute_error=None, begin_error=None,
                 commit_error=None, rollback_error=None, fail_at=1, suppress=False, exit_status=None):
        self.info = types.SimpleNamespace(transaction_status=types.SimpleNamespace(name="IDLE"))
        self.execute_error = execute_error
        self.begin_error = begin_error
        self.commit_error = commit_error
        self.rollback_error = rollback_error
        self.fail_at = fail_at
        self.suppress = suppress
        self.exit_status = exit_status
        self.events = []
        self.statements = []
        self.exit_exception = None

    def transaction(self):
        return FakeTransaction(self)

    def execute(self, statement):
        self.events.append("execute")
        self.statements.append(statement)
        if self.execute_error is not None and len(self.statements) == self.fail_at:
            raise self.execute_error


class ProofAssertionTests(unittest.TestCase):
    def setUp(self):
        replacement = types.SimpleNamespace(Error=FakeError)
        self.patcher = patch.dict(sys.modules, {"psycopg": replacement})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def statement(self, conn):
        return proof.expect_statement_error(conn, "synthetic", STATE, MARKER,
                                            stage="statement_probe")

    def commit(self, conn):
        return proof.expect_commit_error(conn, ["first", "second"], STATE, MARKER,
                                        stage="commit_probe")

    def test_matching_statement_rejection_rolls_back_before_success(self):
        conn = FakeConnection(execute_error=FakeError())
        result = self.statement(conn)
        self.assertEqual(conn.events, ["begin", "execute", "rollback"])
        self.assertIsInstance(conn.exit_exception, proof._ExpectedRollback)
        self.assertEqual(result, {"phase": "statement", "sqlstate": STATE,
                                  "synthetic_marker_matched": True})

    def test_wrong_marker_same_sqlstate_is_rejected(self):
        conn = FakeConnection(execute_error=FakeError(marker="lock timeout"))
        with self.assertRaises(proof.ProofFailure) as caught:
            self.statement(conn)
        self.assertEqual(caught.exception.reason, "unexpected database marker")
        self.assertEqual(conn.events[-1], "rollback")

    def test_wrong_category_is_rejected(self):
        with self.assertRaises(proof.ProofFailure) as caught:
            self.statement(FakeConnection(execute_error=FakeError("23514")))
        self.assertEqual(caught.exception.observed_state, "23514")

    def test_statement_without_error_fails_and_rolls_back(self):
        conn = FakeConnection()
        with self.assertRaises(proof.ProofFailure) as caught:
            self.statement(conn)
        self.assertEqual(caught.exception.reason, "expected rejection missing")
        self.assertEqual(conn.events, ["begin", "execute", "rollback"])

    def test_late_commit_error_cannot_satisfy_statement_probe(self):
        conn = FakeConnection(commit_error=FakeError())
        with self.assertRaises(proof.ProofFailure):
            self.statement(conn)
        self.assertNotIn("commit", conn.events)
        self.assertEqual(conn.events[-1], "rollback")

    def test_statement_context_suppression_cannot_report_success(self):
        for error in (FakeError(), FakeError(marker=PRIVATE), None):
            conn = FakeConnection(execute_error=error, suppress=True)
            with self.subTest(error=error), self.assertRaises(proof.ProofFailure) as caught:
                self.statement(conn)
            self.assertEqual(caught.exception.reason, "transaction suppressed rejection")
            self.assertEqual(caught.exception.phase, "rollback")
            self.assertEqual(conn.events[-1], "rollback")

    def test_expected_commit_error_requires_idle_after_exit(self):
        for status in ("INTRANS", "INERROR", "ACTIVE", "UNKNOWN", PRIVATE):
            conn = FakeConnection(commit_error=FakeError(), exit_status=status)
            with self.subTest(status=status), self.assertRaises(proof.ProofFailure) as caught:
                self.commit(conn)
            self.assertEqual(caught.exception.reason, "transaction exit did not restore idle connection")
            self.assertEqual(caught.exception.phase, "rollback")
            self.assertEqual(conn.events[-1], "commit")
            self.assertNotIn(PRIVATE, json.dumps(caught.exception.record()))

    def test_statement_begin_failure_is_not_accepted(self):
        error = FakeError()
        conn = FakeConnection(begin_error=error)
        with self.assertRaises(FakeError) as caught:
            self.statement(conn)
        self.assertIs(caught.exception, error)
        self.assertEqual(conn.events, ["begin"])

    def test_statement_rollback_failure_is_not_accepted(self):
        error = FakeError()
        conn = FakeConnection(execute_error=FakeError(), rollback_error=error)
        with self.assertRaises(FakeError) as caught:
            self.statement(conn)
        self.assertIs(caught.exception, error)
        self.assertEqual(conn.events[-1], "rollback")

    def test_commit_accepts_expected_exit_rejection(self):
        conn = FakeConnection(commit_error=FakeError())
        self.assertEqual(self.commit(conn), {"phase": "commit", "sqlstate": STATE,
                                            "synthetic_marker_matched": True})
        self.assertEqual(conn.events, ["begin", "execute", "execute", "commit"])
        self.assertEqual(conn.statements, ["first", "second"])

    def test_commit_rejects_matching_execute_error(self):
        conn = FakeConnection(execute_error=FakeError())
        with self.assertRaises(proof.ProofFailure) as caught:
            self.commit(conn)
        self.assertEqual(caught.exception.reason, "unexpected statement rejection")
        self.assertEqual(caught.exception.phase, "statement")
        self.assertEqual(conn.statements, ["first"])
        self.assertEqual(conn.events[-1], "rollback")

    def test_later_execute_error_still_cannot_satisfy_commit_probe(self):
        conn = FakeConnection(execute_error=FakeError(), fail_at=2)
        with self.assertRaises(proof.ProofFailure) as caught:
            self.commit(conn)
        self.assertEqual(caught.exception.phase, "statement")
        self.assertEqual(conn.statements, ["first", "second"])
        self.assertNotIn("commit", conn.events)

    def test_commit_without_error_fails(self):
        conn = FakeConnection()
        with self.assertRaises(proof.ProofFailure) as caught:
            self.commit(conn)
        self.assertEqual(caught.exception.reason, "expected rejection missing")
        self.assertEqual(caught.exception.phase, "commit")
        self.assertEqual(conn.events[-1], "commit")

    def test_commit_missing_connection_status_fails_before_begin(self):
        for missing in ("info", "transaction_status", "name"):
            conn = FakeConnection()
            if missing == "info":
                del conn.info
            elif missing == "transaction_status":
                del conn.info.transaction_status
            else:
                del conn.info.transaction_status.name
            with self.subTest(missing=missing), self.assertRaises(proof.ProofFailure) as caught:
                self.commit(conn)
            self.assertEqual(caught.exception.phase, "precondition")
            self.assertEqual(conn.events, [])

    def test_commit_nonidle_status_fails_before_begin(self):
        for status in ("INTRANS", "INERROR", "ACTIVE", "UNKNOWN", None, PRIVATE):
            conn = FakeConnection()
            conn.info.transaction_status.name = status
            with self.subTest(status=status), self.assertRaises(proof.ProofFailure) as caught:
                self.commit(conn)
            self.assertEqual(caught.exception.phase, "precondition")
            self.assertEqual(conn.events, [])
            self.assertNotIn(PRIVATE, json.dumps(caught.exception.record()))

    def test_commit_begin_failure_is_not_accepted(self):
        conn = FakeConnection(begin_error=FakeError())
        with self.assertRaises(proof.ProofFailure) as caught:
            self.commit(conn)
        self.assertEqual(caught.exception.phase, "begin")
        self.assertEqual(conn.events, ["begin"])

    def test_commit_rollback_failure_is_not_accepted(self):
        conn = FakeConnection(execute_error=FakeError(), rollback_error=FakeError())
        with self.assertRaises(proof.ProofFailure) as caught:
            self.commit(conn)
        self.assertEqual(caught.exception.phase, "rollback")
        self.assertEqual(conn.events[-1], "rollback")
        self.assertNotIn("commit", conn.events)

    def test_commit_wrong_marker_is_rejected(self):
        with self.assertRaises(proof.ProofFailure) as caught:
            self.commit(FakeConnection(commit_error=FakeError(marker=PRIVATE)))
        self.assertEqual(caught.exception.phase, "commit")
        self.assertEqual(caught.exception.reason, "unexpected database marker")

    def test_commit_wrong_category_is_rejected(self):
        with self.assertRaises(proof.ProofFailure) as caught:
            self.commit(FakeConnection(commit_error=FakeError("57014")))
        self.assertEqual(caught.exception.observed_state, "57014")

    def test_missing_diagnostics_cannot_count_as_expected_marker(self):
        error = FakeError()
        del error.diag
        with self.assertRaises(proof.ProofFailure):
            self.statement(FakeConnection(execute_error=error))

    def test_safe_state_allows_only_five_uppercase_alphanumeric_chars(self):
        for state in (None, 55, [], {}, "", "55p03", "55P03\n", "123456", PRIVATE):
            with self.subTest(state=state):
                self.assertEqual(proof.safe_state(FakeError(state)), "unknown")
        for state in ("55P03", "23514", "XX000"):
            self.assertEqual(proof.safe_state(FakeError(state)), state)
        self.assertEqual(proof.safe_state(Exception(PRIVATE)), "unknown")

    def test_failure_records_never_contain_raw_error_or_marker(self):
        factories = (
            lambda: self.statement(FakeConnection(execute_error=FakeError(marker=PRIVATE))),
            lambda: self.statement(FakeConnection(execute_error=FakeError(PRIVATE))),
            lambda: self.commit(FakeConnection(commit_error=FakeError(marker=PRIVATE))),
            lambda: self.commit(FakeConnection(commit_error=FakeError(PRIVATE))),
            lambda: self.commit(FakeConnection(begin_error=FakeError(PRIVATE))),
            lambda: self.commit(FakeConnection(execute_error=FakeError(PRIVATE))),
        )
        for invoke in factories:
            with self.subTest(invoke=invoke), self.assertRaises(proof.ProofFailure) as caught:
                invoke()
            encoded = json.dumps(caught.exception.record())
            self.assertNotIn(PRIVATE, encoded)
            self.assertNotIn(PRIVATE, str(caught.exception))
            self.assertIn(caught.exception.observed_state, (STATE, "unknown"))

    def test_non_database_execute_exception_is_not_accepted(self):
        for invoke in (self.statement, self.commit):
            error = RuntimeError(PRIVATE)
            conn = FakeConnection(execute_error=error)
            with self.subTest(invoke=invoke), self.assertRaises(RuntimeError) as caught:
                invoke(conn)
            self.assertIs(caught.exception, error)
            self.assertEqual(conn.events[-1], "rollback")


if __name__ == "__main__":
    unittest.main()
