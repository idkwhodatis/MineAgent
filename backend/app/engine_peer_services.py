"""Disabled engine controller resolver; no launcher, listener or registry change.

Expected values must come from the locked engine launch-binding row and frozen
host configuration, never from an incoming event. They are data, not authority.
Every event rechecks current transaction, durable launch and live kernel peer.
An engine controller is the actual direct-Python leader; a future protected PID1
wrapper requires a distinct committed controller identity, not a relaxed check.
"""

from dataclasses import dataclass
from uuid import UUID

from backend.app.research_lifecycle import require_neutral_transaction
from backend.app.research_process_models import ResearchProcessAdmission
from backend.app.research_process_services import require_running_peer
from packages.research_runtime.process_contracts import ProcessError, sha256
from runtimes.research_supervisor.ipc import require_authenticated_peer


@dataclass(frozen=True)
class ControllerBinding:
    admission_id: UUID
    role: str
    profile: str
    image_id: str
    bootstrap_sha256: str

    def __post_init__(self):
        if (
            type(self.admission_id) is not UUID
            or type(self.role) is not str
            or self.role not in {"rd_orchestrator", "openevolve_orchestrator"}
            or type(self.profile) is not str
            or not 1 <= len(self.profile) <= 60
            or type(self.image_id) is not str
            or not self.image_id.startswith("sha256:")
        ):
            raise ProcessError("engine_controller_binding")
        sha256(self.image_id[7:])
        sha256(self.bootstrap_sha256)


def require_controller_peer(session, fence, authorization, *, binding):
    """Return the checked row for this transaction only, never a reusable permit.

    The domain service must additionally resolve and validate the event's generation,
    intent, context, phase and replay identity under its locked launch-binding row.
    No successful call grants descendant workers or another same-attempt unit access.
    """
    require_neutral_transaction(session, fence)
    if type(binding) is not ControllerBinding:
        raise ProcessError("engine_controller_binding")
    # Reconstruct so object.__setattr__ or deserialization shortcuts cannot evade
    # the declaration grammar; live authorization is still checked separately.
    binding = ControllerBinding(**binding.__dict__)
    row = session.get(ResearchProcessAdmission, binding.admission_id)
    if (
        row is None
        or row.id != binding.admission_id
        or (row.owner_id, row.run_id, row.attempt_id, row.generation)
        != (fence.owner_id, fence.run_id, fence.attempt_id, fence.generation)
        or (row.role, row.profile, row.image_id, row.input_sha256)
        != (binding.role, binding.profile, binding.image_id, binding.bootstrap_sha256)
    ):
        raise ProcessError("engine_controller_binding")
    running = require_running_peer(
        session, fence, authorization, row.logical_operation_id
    )
    peer = require_authenticated_peer(
        authorization, fence, logical_operation_id=row.logical_operation_id
    )
    if (
        running is not row
        or peer.admission_id != row.id
        or dict(peer.peer) != dict(peer.leader)
    ):
        raise ProcessError("engine_controller_peer")
    return row
