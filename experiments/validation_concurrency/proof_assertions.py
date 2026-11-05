"""Phase-specific database evidence helpers; importing performs no DB work."""
from dataclasses import dataclass
import re


def safe_state(error):
    value = getattr(error, "sqlstate", None)
    return value if isinstance(value, str) and re.fullmatch(r"[0-9A-Z]{5}", value) else "unknown"


@dataclass
class ProofFailure(AssertionError):
    reason: str
    stage: str
    phase: str
    expected_state: str | None = None
    observed_state: str | None = None

    def __str__(self):
        return self.reason

    def record(self):
        return {"reason": self.reason, "stage": self.stage, "phase": self.phase,
                "expected_state": self.expected_state, "observed_state": self.observed_state}


class _ExpectedRollback(Exception):
    pass


def require_rejection(error, state, marker, *, stage, phase):
    """Compare a known synthetic marker internally; never return raw payloads."""
    observed = safe_state(error)
    if observed != state:
        raise ProofFailure("unexpected database category", stage, phase, state, observed) from None
    if getattr(getattr(error, "diag", None), "message_primary", None) != marker:
        raise ProofFailure("unexpected database marker", stage, phase, state, observed) from None


def expect_statement_error(connection, statement, state, marker, *, stage):
    """Only an execute-time error satisfies this assertion, never COMMIT failure.

    transaction() is a savepoint when an outer transaction is already active.
    A matching rejection is deliberately rolled back before returning success.
    Rollback failure is not swallowed or mistaken for successful observation.
    """
    import psycopg
    try:
        with connection.transaction():
            try:
                connection.execute(statement)
            except psycopg.Error as error:
                require_rejection(error, state, marker, stage=stage, phase="statement")
                raise _ExpectedRollback() from None
            raise ProofFailure("expected rejection missing", stage, "statement", state)
    except _ExpectedRollback:
        return {"phase": "statement", "sqlstate": state, "synthetic_marker_matched": True}
    raise ProofFailure("transaction suppressed rejection", stage, "rollback", state)


def expect_commit_error(connection, statements, state, marker, *, stage):
    """Every body statement must succeed; only transaction-exit rejection counts."""
    import psycopg
    status = getattr(getattr(connection, "info", None), "transaction_status", None)
    if getattr(status, "name", None) != "IDLE":
        raise ProofFailure("commit probe requires idle connection", stage, "precondition")
    body_started = False
    body_finished = False
    try:
        with connection.transaction():
            body_started = True
            for statement in statements:
                try:
                    connection.execute(statement)
                except psycopg.Error as error:
                    raise ProofFailure("unexpected statement rejection", stage, "statement",
                                       state, safe_state(error)) from None
            body_finished = True
    except psycopg.Error as error:
        if not body_finished:
            phase = "rollback" if body_started else "begin"
            raise ProofFailure("unexpected transaction boundary rejection", stage, phase,
                               state, safe_state(error)) from None
        require_rejection(error, state, marker, stage=stage, phase="commit")
        after = getattr(getattr(connection, "info", None), "transaction_status", None)
        if getattr(after, "name", None) != "IDLE":
            raise ProofFailure("transaction exit did not restore idle connection", stage,
                               "rollback", state, safe_state(error)) from None
        return {"phase": "commit", "sqlstate": state, "synthetic_marker_matched": True}
    raise ProofFailure("expected rejection missing", stage, "commit", state)
