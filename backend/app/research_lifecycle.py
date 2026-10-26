"""Control-plane adapter for pure immutable run identity checks."""

from packages.research_runtime.identity import RunIdentityError, require_legacy_identity


def require_legacy_run(run) -> str | None:
    try:
        return require_legacy_identity(run)
    except RunIdentityError:
        # Lazy import keeps services -> lifecycle -> pure contracts acyclic.
        from backend.app.services import DomainError

        raise DomainError(
            "unsupported_run_identity",
            "Run purpose, engine or immutable configuration is unsupported",
            409,
        ) from None


# Neutral helpers never own commit/rollback. This boundary is deliberately
# separate from legacy factor publication, whose entry rollback would erase a
# durable reservation and whose internal commit would release execution fences.
import hashlib
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import event, select, text

from packages.research_runtime.fences import AttemptFence
from packages.research_runtime.resource_scopes import (
    SCOPES,
    advisory_lock_plan,
    scope_supplies,
)

_neutral_scope = ContextVar("mineagent_neutral_transaction", default=None)
_NEUTRAL_KEY = "mineagent_neutral_transaction"
_RESOURCE_SCOPES = SCOPES


def _neutral_error(code="neutral_transaction_lost"):
    from backend.app.services import DomainError

    return DomainError(code, "Neutral execution authority is unavailable", 409)


def _try_neutral_lock(session, scope):
    key = int.from_bytes(
        hashlib.sha256(scope.encode()).digest()[:8], "big", signed=True
    )
    if (
        session.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key})
        is not True
    ):
        raise _neutral_error("neutral_busy")


def _check_neutral_fence(run, attempt, fence):
    from backend.app.models import utcnow

    if (
        run is None
        or attempt is None
        or run.owner_id != fence.owner_id
        or run.id != fence.run_id
        or run.active_attempt_id != fence.attempt_id
        or run.generation != fence.generation
        or attempt.run_id != fence.run_id
        or attempt.id != fence.attempt_id
        or attempt.generation != fence.generation
        or attempt.lease_token != fence.lease_token
        or attempt.lease_expires_at <= utcnow()
        or run.status not in {"preparing", "running", "finalizing"}
        or attempt.status not in {"preparing", "running", "finalizing"}
        or attempt.finished_at is not None
    ):
        raise _neutral_error("attempt_fenced")
    # Revision018 permits exact RD persistence, never dispatcher/launcher entry.
    # Legacy public services still call require_legacy_run directly.
    if (run.purpose, run.engine) == ("factor_research", "rdagent"):
        from packages.rdagent_adapter.persisted_identity import require_source_identity

        try:
            require_source_identity(run)
        except ValueError:
            raise _neutral_error("unsupported_run_identity") from None
    else:
        require_legacy_run(run)


def require_neutral_transaction(session, fence: AttemptFence, *, resource_scope=None):
    """Require the same active, locked transaction; never reconnect lost authority."""
    state = session.info.get(_NEUTRAL_KEY)
    if (
        type(fence) is not AttemptFence
        or state is None
        or _neutral_scope.get() is not state
        or state["fence"] != fence
        or (
            resource_scope is not None
            and not scope_supplies(state["resource_scope"], resource_scope)
        )
        or session.get_transaction() is not state["transaction"]
        or not state["transaction"].is_active
        or not session.is_active
        or state["connection"].closed
        or state["connection"].invalidated
    ):
        raise _neutral_error()
    _check_neutral_fence(state["run"], state["attempt"], fence)
    return state["run"], state["attempt"]


@contextmanager
def neutral_transaction(factory, fence: AttemptFence, *, resource_scope="none"):
    """Own one short fenced transaction, never a transport/container lifetime.

    Session's global maintenance admission occurs before the first SQL. Existing
    owner submission locks precede Run/Attempt locks, avoiding resume inversion.
    Physical quota locks are declared up front, never acquired by nested helpers.
    A supervisor must also retain shared_lifecycle around external I/O and stop.
    """
    from sqlalchemy.orm import sessionmaker

    from backend.app.models import ExecutionAttempt, ResearchRun

    if (
        type(fence) is not AttemptFence
        or type(resource_scope) is not str
        or resource_scope not in _RESOURCE_SCOPES
    ):
        raise _neutral_error("neutral_contract_invalid")
    if _neutral_scope.get() is not None:
        raise _neutral_error("neutral_transaction_nested")
    # Never accept a caller-owned Session disguised as a factory: closing that
    # session on rejection would silently roll back unrelated pending work.
    if not isinstance(factory, sessionmaker):
        raise _neutral_error("neutral_factory_required")
    with factory() as session:
        if session.in_transaction():
            raise _neutral_error("neutral_transaction_nested")
        with session.begin() as transaction:
            for lock_scope in advisory_lock_plan(fence.owner_id, resource_scope):
                _try_neutral_lock(session, lock_scope)
            run = session.scalar(
                select(ResearchRun)
                .where(
                    ResearchRun.id == fence.run_id,
                    ResearchRun.owner_id == fence.owner_id,
                )
                .with_for_update()
            )
            attempt = session.scalar(
                select(ExecutionAttempt)
                .where(
                    ExecutionAttempt.id == fence.attempt_id,
                    ExecutionAttempt.run_id == fence.run_id,
                )
                .with_for_update()
            )
            _check_neutral_fence(run, attempt, fence)
            state = {
                "fence": fence,
                "run": run,
                "attempt": attempt,
                "transaction": transaction,
                "connection": session.connection(),
                "allow_commit": False,
                "resource_scope": resource_scope,
            }
            session.info[_NEUTRAL_KEY] = state
            token = _neutral_scope.set(state)

            def before_commit(_session):
                if not state["allow_commit"]:
                    raise _neutral_error("neutral_helper_commit_forbidden")
                require_neutral_transaction(session, fence)

            event.listen(session, "before_commit", before_commit)
            try:
                yield session
                require_neutral_transaction(session, fence)
                session.flush()
                require_neutral_transaction(session, fence)
                state["allow_commit"] = True
                # Commit while the authority context and veto remain installed.
                transaction.commit()
            finally:
                event.remove(session, "before_commit", before_commit)
                session.info.pop(_NEUTRAL_KEY, None)
                _neutral_scope.reset(token)
