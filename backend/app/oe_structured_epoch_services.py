from packages.openevolve_structured.workflow_host_binding import (
    require_bootstrap_context,
    runtime_profile_for,
)

"""One normal controller epoch from the persisted user declaration, no fake fence."""

import hashlib
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select, text

from backend.app.factor_native_io import require_native_storage_guard
from backend.app.oe_structured_config_manifest import expected_config_manifest
from backend.app.oe_structured_controller_context import fail, scoped
from backend.app.oe_structured_identity import decode_spec
from backend.app.oe_structured_operational_models import OEStructuredEpoch
from backend.app.oe_structured_server_profile import (
    resolve_operator_profile,
    resolve_runtime,
)
from backend.app.research_broker_services import reserve_budget
from backend.app.research_lifecycle import require_neutral_transaction
from packages.factors.contracts import canonical_bytes, digest
from packages.openevolve_structured.candidate import CandidateDocument
from packages.openevolve_structured.provider_bootstrap import (
    ProviderStructuredBootstrap,
)
from packages.openevolve_structured.workflow_bootstrap import (
    WorkflowStructuredBootstrap,
    parse_provider_bootstrap,
)
from packages.openevolve_structured.workflow_run import WorkflowStructuredRunConfig
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.process_declarations_v2 import (
    HostTicketIPC,
    ProcessDeclarationV2,
)
from runtimes.openevolve_structured.profile import (
    MAX_WALL_MS,
    PROFILE,
    STORAGE_FLOOR,
    profile_sha256,
)
from runtimes.openevolve_structured_host.process_journal import ControllerIntent

HOOK_MANIFEST_SHA256 = (
    "e0d993f19b165e4f79bee5f7ef2035b761d8de2b6f50e8f5e5a1bcac2340f979"
)


def _current(session, fence, settings):
    run, _ = require_neutral_transaction(session, fence, resource_scope="processes")
    if session.scalars(text("SELECT version_num FROM alembic_version")).all() not in (
        ["028_oe_structured_search"],
        ["029_research_workflow"],
        ["030_declared_upload_basket"],
        ["031_workflow_structured_stage"],
        ["032_normal_oe_note_pins"],
        ["032a_rd_terminal_results"],
    ):
        fail()
    require_native_storage_guard(settings.storage_root)
    spec, config = decode_spec(session, run)
    return spec, config


def expected_controller_bootstrap(session, fence, epoch_id, settings):
    """Fresh immutable declaration reconstruction, NOT live peer/launch authority."""
    _, config = _current(session, fence, settings)
    epoch = scoped(session.get(OEStructuredEpoch, epoch_id), fence)
    try:
        value = parse_provider_bootstrap(epoch.bootstrap_canonical.encode("ascii"))
        require_bootstrap_context(config, value)
    except (TypeError, ValueError):
        fail()
    raw = canonical_bytes(value)
    seed = canonical_bytes(CandidateDocument(program=config.search.domain.seed_program))
    if (
        raw.decode("ascii") != epoch.bootstrap_canonical
        or hashlib.sha256(raw).hexdigest() != epoch.bootstrap_sha256
        or value.epoch_id != epoch.id
        or value.admission_id != epoch.controller_admission_id
        or value.spec != config.search
        or value.model_binding != config.model_binding
        or value.model_binding_sha256 != config.model_binding.sha256
        or value.adapter_patch_sha256 != HOOK_MANIFEST_SHA256
        or config.runtime.controller_inventory_sha256 != HOOK_MANIFEST_SHA256
        or value.physical_profile_sha256 != profile_sha256()
        or value.runtime_profile_sha256 != runtime_profile_for(config)
        or value.config_sha256
        != digest(expected_config_manifest(config.search, seed, config.model_binding))
        or hashlib.sha256(seed).hexdigest() != config.search.seed_candidate_sha256
    ):
        fail()
    return epoch, value, seed


def prepare_controller_epoch(session, fence, settings, *, process_ticket):
    """Flush declaration/hold only; caller MUST persist admission in SAME txn.

    The single deferred epoch→admission FK cannot commit without the real
    admission. No external create/start can occur until both are committed.
    Existing epoch is an uncertain/replay obligation, never a second launch.
    """
    _, config = _current(session, fence, settings)
    if (
        type(process_ticket) is not str
        or re.fullmatch("[a-f0-9]{64}", process_ticket) is None
    ):
        fail()
    if (
        session.scalar(select(OEStructuredEpoch).filter_by(run_id=fence.run_id))
        is not None
    ):
        fail()
    _, binding = resolve_operator_profile(
        settings, fence.owner_id, config.model_binding.profile_id
    )
    if (
        resolve_runtime(settings).runtime != config.runtime
        or binding != config.model_binding
    ):
        fail()
    epoch_id, admission_id = uuid4(), uuid4()
    hold = reserve_budget(
        session,
        fence,
        purpose="process_unit",
        logical_key=f"oe-structured-controller:{epoch_id}",
        amounts=BudgetVector(
            process_slots=1, wall_milliseconds=MAX_WALL_MS, storage_bytes=STORAGE_FLOOR
        ),
        settings=settings,
    )
    if hold.state != "reserved":
        fail()
    seed = canonical_bytes(CandidateDocument(program=config.search.domain.seed_program))
    now = datetime.now(UTC)
    cls = (
        WorkflowStructuredBootstrap
        if type(config) is WorkflowStructuredRunConfig
        else ProviderStructuredBootstrap
    )
    extra = (
        {
            "workflow_context": config.workflow_context,
            "workflow_context_sha256": digest(config.workflow_context),
        }
        if type(config) is WorkflowStructuredRunConfig
        else {}
    )
    bootstrap = cls(
        admission_id=admission_id,
        epoch_id=epoch_id,
        spec=config.search,
        issued_at=now,
        not_after=now + timedelta(milliseconds=MAX_WALL_MS),
        physical_profile_sha256=profile_sha256(),
        runtime_profile_sha256=runtime_profile_for(config),
        config_sha256=digest(
            expected_config_manifest(config.search, seed, config.model_binding)
        ),
        adapter_patch_sha256=HOOK_MANIFEST_SHA256,
        model_binding=binding,
        model_binding_sha256=binding.sha256,
        **extra,
    )
    raw = canonical_bytes(bootstrap)
    scope = {
        key: getattr(fence, key)
        for key in ("owner_id", "run_id", "attempt_id", "generation")
    }
    epoch = OEStructuredEpoch(
        id=epoch_id,
        **scope,
        controller_admission_id=admission_id,
        bootstrap_canonical=raw.decode("ascii"),
        bootstrap_sha256=hashlib.sha256(raw).hexdigest(),
    )
    session.add(epoch)
    session.flush()
    expected_controller_bootstrap(session, fence, epoch.id, settings)
    inventory = {
        "schema_version": "mineagent.oe-structured-bootstrap-inventory.v1",
        "files": [
            {
                "destination": "/bootstrap/bootstrap.json",
                "sha256": epoch.bootstrap_sha256,
                "size_bytes": len(raw),
            },
            {
                "destination": "/bootstrap/seed.json",
                "sha256": hashlib.sha256(seed).hexdigest(),
                "size_bytes": len(seed),
            },
        ],
    }
    declaration = ProcessDeclarationV2(
        schema_version="mineagent.process-admission.v2",
        id=admission_id,
        **scope,
        role="openevolve_orchestrator",
        logical_operation_id=epoch.id,
        launch_ordinal=1,
        profile=PROFILE,
        input_sha256=digest(inventory),
        reservation_id=hold.id,
        image_id=config.runtime.controller_image_id,
        ipc=HostTicketIPC(
            ipc_policy="host_ticket",
            ticket_sha256=hashlib.sha256(process_ticket.encode()).hexdigest(),
        ),
    )
    intent = ControllerIntent(
        schema_version="mineagent.oe-structured-controller-intent.v1",
        id=uuid4(),
        admission=declaration,
        bootstrap_sha256=epoch.bootstrap_sha256,
        bootstrap_size_bytes=len(raw),
        seed_sha256=hashlib.sha256(seed).hexdigest(),
        seed_size_bytes=len(seed),
        input_layout="bootstrap_json_and_seed_json_only",
        issued_at=now,
        not_after=bootstrap.not_after,
    )
    return epoch, intent, raw, seed
