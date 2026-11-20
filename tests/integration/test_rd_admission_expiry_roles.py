"""Original heartbeat cancellation/pause semantics; no native admission wiring."""

import pytest
from sqlalchemy import text
from test_backup_restore import drill_databases as drill_databases  # noqa: PLC0414
from test_workflow_heartbeat_admission_candidate import (
    candidate_workflow as candidate_workflow,  # noqa: PLC0414
)
from test_workflow_heartbeat_lock_order import _run_attempt

from backend.app.research_lifecycle import _try_neutral_lock
from tests.sql_probes.rd_admission_expiry_observation import CommittedExpiryObserver
from tests.sql_probes.rd_attempt_admission_candidate import AdmissionRejected

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("status", ["cancel_requested", "pause_requested"])
def test_observation_role_preserves_original_pulse_cancel_and_pause(
    candidate_workflow, status
):
    state = candidate_workflow
    with state.env.factory.begin() as writer:
        _try_neutral_lock(writer, f"submit:{state.fence.owner_id}")
        _run_attempt(writer, state.fence)
        writer.execute(
            text("UPDATE research_runs SET status=:status WHERE id=:id"),
            {"status": status, "id": state.fence.run_id},
        )
    observer = CommittedExpiryObserver(state.env.factory, state.fence)
    with pytest.raises(AdmissionRejected, match="observation_rejected"):
        observer.observe(state.env.factory, state.fence)
    ticket = observer.observe(state.env.factory, state.fence, role="heartbeat")
    assert ticket.remaining(state.env.factory, state.fence, role="heartbeat") > 0
    # No maintenance thread, wrapper, retry, or replaced pulse. All original
    # fenced_attempt checks and commit/deferred triggers still execute.
    state.lease.pulse(force=True)
    assert state.lease.last_pulse > 0
    assert not state.lease.lost.is_set()
    assert state.lease.cancelled.is_set() is (status == "cancel_requested")
