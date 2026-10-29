from packages.openevolve_structured.workflow_host_binding import (
    require_bootstrap_context,
    runtime_profile_for,
)

"""Fresh persisted controller evidence plus independently live socket authority.

Stored records never manufacture a Peer. The caller supplies only the original
nonserializable host-authenticated socket peer, reobserved before and after IO.
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, text

from backend.app.factor_native_io import require_native_storage_guard
from backend.app.oe_structured_identity import decode_spec
from backend.app.oe_structured_operational_models import OEStructuredEpoch
from backend.app.research_broker_models import (
    ResearchBudgetReservation,
    ResearchBudgetSettlement,
)
from backend.app.research_lifecycle import require_neutral_transaction
from backend.app.research_process_models import (
    ResearchProcessAdmission,
    ResearchProcessReceipt,
    ResearchProcessState,
)
from packages.factors.contracts import canonical_bytes
from packages.openevolve_structured.provider_bootstrap import (
    ProviderStructuredBootstrap,
)
from packages.openevolve_structured.workflow_bootstrap import parse_provider_bootstrap
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.process_contracts import ProcessError, digest
from runtimes.openevolve_structured.profile import (
    MAX_WALL_MS,
    STORAGE_FLOOR,
    profile_sha256,
)
from runtimes.openevolve_structured_host.peer import (
    ControllerBinding,
    Peer,
    WorkerBinding,
    require_peer,
)
from runtimes.openevolve_structured_host.process_journal import read_controller_journal

SCOPE = ("owner_id", "run_id", "attempt_id", "generation")


def fail():
    raise ProcessError("oe_structured_controller_context")


def scoped(row, fence):
    if row is None or any(getattr(row, k) != getattr(fence, k) for k in SCOPE):
        fail()
    return row


@dataclass(frozen=True)
class ControllerContext:
    epoch: object
    config: object
    bootstrap: ProviderStructuredBootstrap
    launch: object
    identity: object
    child: object


def require_controller_context(
    session, fence, epoch_id, settings, *, peer, intent_id=None
):
    run, _ = require_neutral_transaction(
        session, fence, resource_scope="factor_numeric"
    )
    if (
        session.scalars(text("SELECT version_num FROM alembic_version")).all()
        not in (
            ["028_oe_structured_search"],
            ["029_research_workflow"],
            ["030_declared_upload_basket"],
            ["031_workflow_structured_stage"],
            ["032_normal_oe_note_pins"],
        )
        or type(peer) is not Peer
    ):
        fail()
    require_native_storage_guard(settings.storage_root)
    epoch = scoped(session.get(OEStructuredEpoch, epoch_id), fence)
    require_peer(
        peer,
        fence,
        admission_id=epoch.controller_admission_id,
        epoch_id=epoch.id,
        intent_id=intent_id,
    )
    _, config = decode_spec(session, run)
    bootstrap = parse_provider_bootstrap(epoch.bootstrap_canonical.encode("ascii"))
    require_bootstrap_context(config, bootstrap)
    raw = canonical_bytes(bootstrap)
    if (
        raw.decode("ascii") != epoch.bootstrap_canonical
        or hashlib.sha256(raw).hexdigest() != epoch.bootstrap_sha256
        or bootstrap.spec != config.search
        or bootstrap.model_binding != config.model_binding
        or bootstrap.admission_id != epoch.controller_admission_id
        or bootstrap.epoch_id != epoch.id
        or bootstrap.runtime_profile_sha256 != runtime_profile_for(config)
        or bootstrap.physical_profile_sha256 != profile_sha256()
        or datetime.now(UTC) >= bootstrap.not_after
    ):
        fail()
    admission = scoped(
        session.get(ResearchProcessAdmission, epoch.controller_admission_id), fence
    )
    declaration = peer.admission
    if any(
        getattr(admission, k) != getattr(declaration, k)
        for k in (
            "id",
            *SCOPE,
            "role",
            "logical_operation_id",
            "launch_ordinal",
            "profile",
            "input_sha256",
            "reservation_id",
            "image_id",
            "schema_version",
        )
    ) or (
        admission.logical_operation_id != epoch.id
        or admission.launch_ordinal != 1
        or admission.image_id != config.runtime.controller_image_id
        or admission.ipc_policy != "host_ticket"
        or admission.ticket_sha256 != declaration.ipc.ticket_sha256
    ):
        fail()
    reservation = scoped(
        session.get(ResearchBudgetReservation, admission.reservation_id), fence
    )
    amounts = BudgetVector.model_validate(reservation.amounts)
    if (
        reservation.state != "reserved"
        or reservation.purpose != "process_unit"
        or reservation.logical_key != "oe-structured-controller:" + str(epoch.id)
        or amounts.process_slots < 1
        or amounts.wall_milliseconds < MAX_WALL_MS
        or amounts.storage_bytes < STORAGE_FLOOR
    ):
        fail()
    if (
        session.scalar(
            select(ResearchBudgetSettlement).filter_by(reservation_id=reservation.id)
        )
        is not None
    ):
        fail()
    state = scoped(session.get(ResearchProcessState, admission.id), fence)
    if state.status != "running":
        fail()
    launch, identity, child, _ = read_controller_journal(
        settings.storage_root, declaration, with_child=True
    )
    if (
        launch.intent.bootstrap_sha256 != epoch.bootstrap_sha256
        or launch.intent.bootstrap_size_bytes != len(raw)
        or launch.intent.seed_sha256 != config.search.seed_candidate_sha256
        or launch.intent.issued_at != bootstrap.issued_at
        or launch.intent.not_after != bootstrap.not_after
    ):
        fail()
    rows = session.scalars(
        select(ResearchProcessReceipt).filter_by(admission_id=admission.id)
    ).all()
    expected = {
        "launch_intent": launch.intent,
        "launch_identity": identity,
        "controller_child": child,
    }
    if len(rows) != len(expected) or {r.kind for r in rows} != set(expected):
        fail()
    for row in rows:
        scoped(row, fence)
        payload = expected[row.kind].model_dump(mode="json")
        if row.payload != payload or row.payload_sha256 != digest(payload):
            fail()
    observed = ControllerBinding(
        identity.container_id,
        identity.admission_sha256,
        identity.leader,
        child.controller,
    )
    live = (
        peer.binding.controller if type(peer.binding) is WorkerBinding else peer.binding
    )
    if live != observed:
        fail()
    # Ownership and actual kernel/socket identity are rechecked after physical IO.
    require_neutral_transaction(session, fence, resource_scope="factor_numeric")
    require_peer(
        peer, fence, admission_id=admission.id, epoch_id=epoch.id, intent_id=intent_id
    )
    return ControllerContext(epoch, config, bootstrap, launch, identity, child)
