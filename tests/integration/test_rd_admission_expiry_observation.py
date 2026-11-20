"""Real metadata-only observation proof; not native admission or lease authority.

Original031/032g genuine API/claim fixture, no queue/SDK execution. Adversarial
lease/token/cancel changes retain original locks and all deferred SQL guards.
"""

from uuid import uuid4

import pytest
from rd_completed_graph_probe import _require_unchanged, _snapshot
from sqlalchemy import event, text
from test_backup_restore import drill_databases as drill_databases  # noqa: PLC0414
from test_workflow_heartbeat_admission_candidate import (
    candidate_workflow as candidate_workflow,  # noqa: PLC0414
)
from test_workflow_heartbeat_lock_order import _run_attempt

from backend.app.research_lifecycle import _try_neutral_lock
from backend.app.services import heartbeat
from tests.sql_probes.rd_admission_expiry_observation import CommittedExpiryObserver
from tests.sql_probes.rd_attempt_admission_candidate import AdmissionRejected

pytestmark = pytest.mark.postgres


def _lock(session, state):
    _try_neutral_lock(session, f"submit:{state.fence.owner_id}")
    _run_attempt(session, state.fence)


def _observe(state):
    return CommittedExpiryObserver(state.env.factory, state.fence).observe(
        state.env.factory, state.fence
    )


def test_observation_uses_only_committed_renewals_and_old_ticket_never_refreshes(
    candidate_workflow,
):
    state = candidate_workflow
    # Shorten this disposable fixture's lease, never increase runtime timing.
    # A real original heartbeat restores the configured120s in a later tx.
    with state.env.factory.begin() as session:
        _lock(session, state)
        session.execute(
            text(
                "UPDATE execution_attempts SET lease_expires_at=clock_timestamp()+interval '60 seconds' WHERE id=:id"
            ),
            {"id": state.fence.attempt_id},
        )
    old = _observe(state)
    cutoff = old.cutoff
    before = _snapshot(state.source)
    with state.env.factory() as writer:
        transaction = writer.begin()
        try:
            _lock(writer, state)
            assert (
                heartbeat(
                    writer,
                    state.fence.attempt_id,
                    state.fence.generation,
                    state.fence.lease_token,
                    state.env.settings,
                )
                is False
            )
            writer.flush()
            # Another real connection reads old committed tuple without waiting
            # for the writer's held Run/Attempt rows or original owner lock.
            during = _observe(state)
            assert abs(during.cutoff - cutoff) < 5
        finally:
            transaction.rollback()
    _require_unchanged(state.source, before)
    after_rollback = _observe(state)
    assert abs(after_rollback.cutoff - cutoff) < 5
    with state.env.factory.begin() as writer:
        _lock(writer, state)
        assert (
            heartbeat(
                writer,
                state.fence.attempt_id,
                state.fence.generation,
                state.fence.lease_token,
                state.env.settings,
            )
            is False
        )
    fresh = _observe(state)
    assert fresh.cutoff > cutoff + 30
    assert old.cutoff == cutoff
    # Model a queued consumer reaching its old cutoff. No sleep, worker clock,
    # lease mutation, or future observation can refill that already-issued ticket.
    with pytest.raises(AdmissionRejected, match="expired"):
        old.remaining(state.env.factory, state.fence, clock=lambda: cutoff + 1)
    assert fresh.remaining(state.env.factory, state.fence, clock=lambda: cutoff + 1) > 0


@pytest.mark.parametrize("change", ["token", "cancel"])
def test_observation_rejects_committed_fence_or_cancellation_change(
    candidate_workflow,
    change,
):
    state = candidate_workflow
    old = _observe(state)
    with state.env.factory.begin() as writer:
        _lock(writer, state)
        if change == "token":
            writer.execute(
                text("UPDATE execution_attempts SET lease_token=:token WHERE id=:id"),
                {"token": uuid4(), "id": state.fence.attempt_id},
            )
        else:
            writer.execute(
                text("UPDATE research_runs SET status='cancel_requested' WHERE id=:id"),
                {"id": state.fence.run_id},
            )
    before = _snapshot(state.source)
    with pytest.raises(AdmissionRejected) as caught:
        _observe(state)
    if str(caught.value) != "observation_rejected":
        raise AssertionError("Unexpected observation rejection") from None
    _require_unchanged(state.source, before)
    # An old ticket is explicitly NOT live fencing or cancellation authority.
    assert old.remaining(state.env.factory, state.fence) > 0


def test_observation_readonly_isolation_settings_apply_then_restore(candidate_workflow):
    state = candidate_workflow
    observations = []
    observer_connections = []

    def before_cursor_execute(
        connection, _cursor, statement, _parameters, _context, _many
    ):
        if "SELECT a.lease_expires_at, clock_timestamp()" not in statement:
            return
        observer_connections.append(connection.connection.driver_connection)
        # Capture only closed settings, never SQL text, tokens or parameters.
        observations.append(
            (
                connection.exec_driver_sql("SHOW transaction_read_only").scalar_one(),
                connection.exec_driver_sql("SHOW transaction_isolation").scalar_one(),
            )
        )

    with state.env.factory() as session:
        original = (
            session.execute(text("SHOW transaction_read_only")).scalar_one(),
            session.execute(text("SHOW transaction_isolation")).scalar_one(),
        )
    before = _snapshot(state.source)
    event.listen(state.source, "before_cursor_execute", before_cursor_execute)
    try:
        _observe(state)
    finally:
        event.remove(state.source, "before_cursor_execute", before_cursor_execute)
    assert observations == [("on", "read committed")]
    # The original fixture uses NullPool, not a simulated pooled connection.
    # Require its observed physical connection closed and a fresh original
    # factory transaction writable. This is not a pooled-reset claim.
    assert len(observer_connections) == 1
    if not observer_connections[0].closed:
        raise AssertionError("Observer physical connection remained open")
    with state.env.factory() as session:
        restored = (
            session.execute(text("SHOW transaction_read_only")).scalar_one(),
            session.execute(text("SHOW transaction_isolation")).scalar_one(),
        )
    assert restored == original and restored[0] == "off"
    _require_unchanged(state.source, before)
