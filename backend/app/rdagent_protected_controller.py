"""Current-transaction protected RD child authority, never direct-leader fallback."""

import json
import time

from sqlalchemy import select

from backend.app.rdagent_controller_services import expected_operational_bootstrap
from backend.app.rdagent_models import FactorControllerLaunch
from backend.app.rdagent_services import _scope
from backend.app.research_lifecycle import require_neutral_transaction
from backend.app.research_process_models import (
    ResearchProcessAdmission,
    ResearchProcessReceipt,
    ResearchProcessState,
)
from packages.rdagent_adapter.controller_profile import (
    controller_wall_seconds,
)
from packages.research_runtime.process_contracts import ProcessError, digest
from packages.research_runtime.process_persistence_codec import ROW_FIELDS, decode_row
from runtimes.rdagent_operational.peer import (
    TrustedControllerBinding,
    require_controller_peer,
)
from runtimes.rdagent_operational.process_journal import (
    ControllerChild,
    ControllerIdentity,
    ControllerIntent,
    ControllerLaunch,
    controller_admission,
    validate_records,
)


def _controller_records(session, fence):
    require_neutral_transaction(session, fence)
    attempt_binding, bootstrap = expected_operational_bootstrap(session, fence)
    launch = session.scalar(
        select(FactorControllerLaunch).filter_by(**_scope(fence)).with_for_update()
    )
    if launch is None or (
        launch.attempt_binding_id,
        launch.bootstrap_sha256,
        launch.bootstrap,
    ) != (attempt_binding.id, bootstrap.sha256, bootstrap.model_dump(mode="json")):
        raise ProcessError("rd_controller_launch")
    row = session.get(
        ResearchProcessAdmission, launch.admission_id, with_for_update=True
    )
    if row is None:
        raise ProcessError("rd_controller_launch")
    admission = controller_admission(
        decode_row({key: getattr(row, key) for key in ROW_FIELDS})
    )
    if (
        admission.owner_id,
        admission.run_id,
        admission.attempt_id,
        admission.generation,
        admission.logical_operation_id,
        admission.input_sha256,
        admission.image_id,
    ) != (
        fence.owner_id,
        fence.run_id,
        fence.attempt_id,
        fence.generation,
        attempt_binding.id,
        bootstrap.sha256,
        bootstrap.config.runtime.orchestrator_image,
    ):
        raise ProcessError("rd_controller_launch")
    state = session.get(ResearchProcessState, admission.id, with_for_update=True)
    if state is None or state.status != "running":
        raise ProcessError("rd_controller_not_running")
    records = list(
        session.scalars(
            select(ResearchProcessReceipt).filter_by(admission_id=admission.id).limit(5)
        )
    )
    if len(records) != 3 or {record.kind for record in records} != {
        "launch_intent",
        "launch_identity",
        "controller_child",
    }:
        raise ProcessError("rd_controller_receipts")
    for record in records:
        if (
            any(getattr(record, key) != getattr(fence, key) for key in _scope(fence))
            or digest(record.payload) != record.payload_sha256
        ):
            raise ProcessError("rd_controller_receipts")
    payloads = {record.kind: record.payload for record in records}
    intent = ControllerIntent.model_validate_json(json.dumps(payloads["launch_intent"]))
    identity = ControllerIdentity.model_validate_json(
        json.dumps(payloads["launch_identity"])
    )
    child = ControllerChild.model_validate_json(
        json.dumps(payloads["controller_child"])
    )
    physical_launch = ControllerLaunch(
        schema_version="mineagent.rd-controller-launch.v1",
        intent=intent,
        runtime_name="mineagent-rd-controller-" + str(admission.id),
        root_device=identity.root_device,
        root_inode=identity.root_inode,
    )
    validate_records(physical_launch, identity, child)
    if intent.admission != admission:
        raise ProcessError("rd_controller_receipts")
    expected = TrustedControllerBinding(
        identity.admission_sha256, identity.leader, child.controller
    )
    return launch, admission, expected, identity


def _require_context(context, identity):
    if context is not None:
        from runtimes.rdagent_operational.daemon_identity import DaemonIdentityContext

        if (
            type(context) is not DaemonIdentityContext
            or context.container_id != identity.container_id
            or context.image_id != identity.image_id
            or context.leader_pid != identity.leader.host_pid
        ):
            raise ProcessError("rd_controller_observer_context_drift")


def require_protected_controller(session, fence, authorization):
    launch, admission, expected, identity = _controller_records(session, fence)
    _require_context(getattr(authorization, "_observer_context", None), identity)
    current = require_controller_peer(
        authorization, fence, logical_operation_id=admission.logical_operation_id
    )
    if current.admission != admission or current.binding != expected:
        raise ProcessError("rd_controller_peer_drift")
    return launch


def authenticate_current_controller(
    factory, fence, connection, admission, *, binding, supplied_ticket, observer_context
):
    """One fresh kernel observation AFTER all persisted checks in this callback.

    The pre-transaction claim is plain validated host data, never ControllerPeer.
    A bad ticket still cannot enter the transaction. Nothing is returned before
    the unchanged transaction commit checks succeed. Later handler boundaries
    retain their independent fresh peer checks; no observations are cached.
    """
    from backend.app.research_lifecycle import neutral_transaction
    from runtimes.rdagent_operational.peer import (
        _preflight_controller_connection,
        _validated_controller_claim,
        authenticate_controller,
    )

    admission = _validated_controller_claim(
        admission, binding=binding, supplied_ticket=supplied_ticket
    )
    _preflight_controller_connection(connection, binding)
    if observer_context is not None:
        from runtimes.rdagent_operational.daemon_identity import DaemonIdentityContext

        if (
            type(observer_context) is not DaemonIdentityContext
            or observer_context.profile != admission.profile
            or observer_context.image_id != admission.image_id
            or observer_context.leader_pid != binding.leader.host_pid
        ):
            raise ProcessError("rd_controller_peer")
        # Preserve early original-deadline rejection without observing/caching
        # kernel authority. Full observe_pair checks this again after DB locks.
        if (
            not 0
            < observer_context.deadline - time.monotonic()
            <= controller_wall_seconds(admission.profile)
        ):
            raise ProcessError("rd_controller_daemon_identity")
    with neutral_transaction(factory, fence, resource_scope="processes") as session:
        _launch, current, expected, identity = _controller_records(session, fence)
        if (
            type(fence.generation) is not int
            or admission != current
            or binding != expected
        ):
            raise ProcessError("rd_controller_peer_drift")
        _require_context(observer_context, identity)
        peer = authenticate_controller(
            connection,
            admission,
            binding=binding,
            supplied_ticket=supplied_ticket,
            observer_context=observer_context,
        )
    return peer
