"""Current-fence protected controller resolver for the explicit024 successor.

No caller UUID/boolean is authority. Host-issued socket peer is compared against
exact persisted launch, leader and child receipts on EVERY transaction/event.
"""

import hashlib
from uuid import UUID

from sqlalchemy import select

from backend.app.openevolve_operational_models import SearchControllerLaunch
from backend.app.openevolve_services import _locked_epoch, _owned_row
from backend.app.research_lifecycle import require_neutral_transaction
from backend.app.research_process_models import (
    ResearchProcessAdmission,
    ResearchProcessReceipt,
    ResearchProcessState,
)
from packages.research_runtime.process_contracts import ProcessError, canonical
from packages.research_runtime.process_persistence_codec import ROW_FIELDS, decode_row
from runtimes.openevolve_operational.peer import (
    ControllerBinding,
    Peer,
    WorkerBinding,
    require_peer,
)
from runtimes.openevolve_operational.process_journal import (
    ControllerChild,
    ControllerIdentity,
    ControllerIntent,
    ControllerLaunch,
    validate_records,
)


def typed_bootstrap(binding):
    from packages.openevolve_adapter.checkpoint_native import (
        canonical as document_bytes,
    )
    from runtimes.openevolve_operational.bootstrap import OperationalBootstrap

    if type(binding.bootstrap_canonical) is not str:
        raise ProcessError("oe_bootstrap_canonical_bytes")
    raw = binding.bootstrap_canonical.encode("utf-8")
    value = OperationalBootstrap.model_validate_json(raw)
    if (
        len(raw) > 262144
        or document_bytes(value.model_dump(mode="json")) != raw
        or value.model_dump(mode="json") != binding.bootstrap
        or hashlib.sha256(raw).hexdigest() != binding.bootstrap_sha256
    ):
        raise ProcessError("oe_bootstrap_canonical_bytes")
    return value, raw


def _require_search_unit(session, fence, peer, *, epoch_id, intent_id=None):
    require_neutral_transaction(session, fence)
    binding_type = ControllerBinding if intent_id is None else WorkerBinding
    if type(peer) is not Peer or type(peer.binding) is not binding_type:
        raise ProcessError("oe_controller_authority")
    epoch = _locked_epoch(session, fence, epoch_id)
    launch = session.scalar(select(SearchControllerLaunch).filter_by(epoch_id=epoch.id))
    if (
        launch is None
        or any(
            getattr(launch, k) != getattr(fence, k)
            for k in ("owner_id", "run_id", "attempt_id", "generation")
        )
        or launch.admission_id != epoch.admission_id
    ):
        raise ProcessError("oe_controller_launch")
    from runtimes.openevolve_operational.profile import profile_sha256

    bootstrap, bootstrap_raw = typed_bootstrap(launch)
    if (
        (bootstrap.admission_id, bootstrap.epoch_id, bootstrap.config_sha256)
        != (launch.admission_id, epoch.id, epoch.spec_sha256)
        or hashlib.sha256(bootstrap_raw).hexdigest() != launch.bootstrap_sha256
        or launch.physical_profile_sha256 != profile_sha256()
    ):
        raise ProcessError("oe_controller_bootstrap")
    row = _owned_row(session, ResearchProcessAdmission, launch.admission_id, fence)
    admission = decode_row({key: getattr(row, key) for key in ROW_FIELDS})
    state = session.get(ResearchProcessState, admission.id)
    receipts = session.scalars(
        select(ResearchProcessReceipt).filter_by(admission_id=admission.id)
    ).all()
    if (
        state is None
        or state.status != "running"
        or len(receipts) != 3
        or {r.kind for r in receipts}
        != {"launch_intent", "launch_identity", "controller_child"}
    ):
        raise ProcessError("oe_controller_live_receipts")
    receipts = [
        next(r for r in receipts if r.kind == kind)
        for kind in ("launch_intent", "launch_identity", "controller_child")
    ]
    for receipt in receipts:
        if (
            any(
                getattr(receipt, k) != getattr(fence, k)
                for k in ("owner_id", "run_id", "attempt_id", "generation")
            )
            or hashlib.sha256(canonical(receipt.payload)).hexdigest()
            != receipt.payload_sha256
        ):
            raise ProcessError("oe_controller_receipt_identity")
    intent = ControllerIntent.model_validate_json(canonical(receipts[0].payload))
    if (
        intent.bootstrap_sha256 != launch.bootstrap_sha256
        or intent.bootstrap_size_bytes != len(bootstrap_raw)
        or intent.seed_sha256 != bootstrap.spec.preparation.prepared.source_sha256
        or (intent.issued_at, intent.not_after)
        != (bootstrap.issued_at, bootstrap.not_after)
        or admission.logical_operation_id != launch.id
    ):
        raise ProcessError("oe_controller_intent_bootstrap")
    identity = ControllerIdentity.model_validate_json(canonical(receipts[1].payload))
    physical = ControllerLaunch(
        schema_version="mineagent.oe-controller-launch.v1",
        intent=intent,
        runtime_name="mineagent-oe-controller-" + str(admission.id),
        root_device=identity.root_device,
        root_inode=identity.root_inode,
    )
    child = ControllerChild.model_validate_json(canonical(receipts[2].payload))
    validate_records(physical, identity, child)
    if (
        physical.intent != intent
        or intent.admission != admission
        or peer.admission != admission
        or (peer.binding if intent_id is None else peer.binding.controller)
        != ControllerBinding(
            identity.container_id,
            identity.admission_sha256,
            identity.leader,
            child.controller,
        )
        or launch.input_inventory_sha256 != admission.input_sha256
    ):
        raise ProcessError("oe_controller_persisted_binding")
    require_peer(
        peer,
        fence,
        admission_id=admission.id,
        epoch_id=epoch_id if intent_id is not None else None,
        intent_id=intent_id,
    )
    return launch, epoch


def require_search_controller(session, fence, peer, *, epoch_id):
    return _require_search_unit(session, fence, peer, epoch_id=epoch_id)


def require_search_worker_unit(session, fence, peer, *, epoch_id, intent_id):
    if type(intent_id) is not UUID:
        raise ProcessError("oe_worker_intent")
    return _require_search_unit(
        session, fence, peer, epoch_id=epoch_id, intent_id=intent_id
    )


def expected_operational_bootstrap(session, fence, launch_id):
    """Resolve full host-created bootstrap and original retained strategy bytes."""
    from backend.app.models import StrategyVersion
    from backend.app.openevolve_models import (
        SearchResolvedExecutionProfile,
        StrategySearchSpec,
    )
    from packages.openevolve_adapter.contracts import SearchSpec
    from packages.openevolve_adapter.execution_inputs import ResolvedNativeInputs
    from runtimes.openevolve_operational.bootstrap import (
        OperationalBootstrap,
        input_inventory,
    )
    from runtimes.openevolve_operational.profile import profile_sha256

    require_neutral_transaction(session, fence)
    binding = _owned_row(session, SearchControllerLaunch, launch_id, fence)
    spec_row = session.get(StrategySearchSpec, fence.run_id)
    native_row = session.get(SearchResolvedExecutionProfile, fence.run_id)
    if (
        spec_row is None
        or native_row is None
        or spec_row.owner_id != fence.owner_id
        or native_row.owner_id != fence.owner_id
    ):
        raise ProcessError("oe_bootstrap_spec")
    spec = SearchSpec.model_validate_json(canonical(spec_row.spec))
    from packages.openevolve_adapter.checkpoint_native import (
        canonical as document_bytes,
    )

    native_raw = native_row.profile_canonical.encode("utf-8")
    native = ResolvedNativeInputs.model_validate_json(native_raw)
    raw = binding.bootstrap_canonical.encode("utf-8")
    bootstrap = OperationalBootstrap.model_validate_json(raw)
    if (
        document_bytes(native.model_dump(mode="json")) != native_raw
        or native.model_dump(mode="json") != native_row.profile
        or document_bytes(bootstrap.model_dump(mode="json")) != raw
        or bootstrap.model_dump(mode="json") != binding.bootstrap
    ):
        raise ProcessError("oe_bootstrap_canonical_bytes")
    if (
        bootstrap.spec != spec
        or bootstrap.native_inputs != native
        or (bootstrap.epoch_id, bootstrap.admission_id)
        != (binding.epoch_id, binding.admission_id)
        or hashlib.sha256(raw).hexdigest() != binding.bootstrap_sha256
        or binding.physical_profile_sha256 != profile_sha256()
    ):
        raise ProcessError("oe_bootstrap_binding")
    source = session.get(StrategyVersion, spec.preparation.prepared.strategy_version_id)
    if (
        source is None
        or source.owner_id != fence.owner_id
        or source.content_sha256 != spec.preparation.prepared.source_sha256
    ):
        raise ProcessError("oe_bootstrap_source")
    seed = source.code.encode("utf-8")
    digest, _ = input_inventory(raw, seed)
    if digest != binding.input_inventory_sha256:
        raise ProcessError("oe_bootstrap_inventory")
    return binding, bootstrap, seed
