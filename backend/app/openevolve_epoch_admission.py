"""Atomic initial epoch/controller obligation, before any external creation.

Runs/specs/owned source/profile must already exist. No public submission, legacy
strategy fabrication, resume, placeholder source, or caller-issued process grant.
"""

import hashlib
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select

from backend.app.models import StrategyVersion, utcnow
from backend.app.openevolve_controller_process import persist_controller_intent
from backend.app.openevolve_evaluation_execution import verify_native_runtime_binding
from backend.app.openevolve_models import (
    SearchResolvedExecutionProfile,
    StrategySearchEpoch,
    StrategySearchSpec,
)
from backend.app.openevolve_operational_models import SearchControllerLaunch
from backend.app.openevolve_services import (
    _current_search,
    _scope,
    bind_resolved_execution_profile,
    bind_search_spec,
)
from backend.app.research_broker_services import initialize_budget, reserve_budget
from backend.app.research_lifecycle import require_neutral_transaction
from packages.openevolve_adapter.checkpoint_native import canonical
from packages.openevolve_adapter.contracts import SearchSpec
from packages.openevolve_adapter.execution_inputs import ResolvedNativeInputs
from packages.openevolve_adapter.operational_profile import require_initial_profile
from packages.openevolve_adapter.runtime_profile import PROFILE as SEARCH_PROFILE
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.process_declarations_v2 import (
    HostTicketIPC,
    ProcessDeclarationV2,
)
from runtimes.openevolve_operational.bootstrap import (
    OperationalBootstrap,
    input_inventory,
)
from runtimes.openevolve_operational.process_journal import ControllerIntent
from runtimes.openevolve_operational.profile import (
    PROFILE,
    STORAGE_FLOOR,
    profile_sha256,
)
from runtimes.openevolve_operational.recipe_pins import verify_recipe_pins
from runtimes.openevolve_operational.runtime_binding import NativeRuntimeBinding


def prepare_initial_epoch(session, fence, *, runtime, process_ticket, settings):
    require_neutral_transaction(session, fence, resource_scope="processes")
    _current_search(session, fence)
    if (
        type(runtime) is not NativeRuntimeBinding
        or type(process_ticket) is not str
        or len(process_ticket) != 64
        or set(process_ticket) - set("0123456789abcdef")
    ):
        raise ValueError("Host-measured runtime and fixed random ticket required")
    runtime = NativeRuntimeBinding.model_validate_json(runtime.model_dump_json())
    verify_native_runtime_binding(runtime)
    if (
        session.scalar(select(StrategySearchEpoch).filter_by(run_id=fence.run_id))
        is not None
    ):
        raise ValueError("Only first epoch; never replace or replay external launch")
    spec_row = session.get(StrategySearchSpec, fence.run_id)
    profile_row = session.get(SearchResolvedExecutionProfile, fence.run_id)
    if spec_row is None or profile_row is None:
        raise ValueError("Persisted exact search spec/profile required")
    spec = SearchSpec.model_validate_json(canonical(spec_row.spec))
    verify_recipe_pins(spec)
    native = ResolvedNativeInputs.model_validate_json(profile_row.profile_canonical)
    require_initial_profile(spec, native)
    bind_search_spec(session, fence, spec)
    bind_resolved_execution_profile(session, fence, native)
    initialize_budget(session, fence, spec.budget)
    source = session.get(StrategyVersion, spec.preparation.prepared.strategy_version_id)
    if source is None or source.owner_id != fence.owner_id:
        raise ValueError("Actual owned seed source required")
    seed = source.code.encode("utf-8")
    if hashlib.sha256(seed).hexdigest() != spec.preparation.prepared.source_sha256:
        raise ValueError("Persisted actual seed bytes changed")
    admission_id, epoch_id, launch_id = uuid4(), uuid4(), uuid4()
    now = utcnow()
    bootstrap = OperationalBootstrap(
        admission_id=admission_id,
        epoch_id=epoch_id,
        spec=spec,
        native_inputs=native,
        native_runtime=runtime,
        issued_at=now,
        not_after=now + timedelta(seconds=600),
        physical_profile_sha256=profile_sha256(),
        runtime_profile_sha256=SEARCH_PROFILE.sha256,
        config_sha256=spec.sha256,
        adapter_patch_sha256=spec.adapter_patch_sha256,
    )
    raw = canonical(bootstrap.model_dump(mode="json"))
    inventory_sha, _ = input_inventory(raw, seed)
    reservation = reserve_budget(
        session,
        fence,
        purpose="process_unit",
        logical_key=f"oe-controller:{launch_id}",
        amounts=BudgetVector(
            process_slots=1, wall_milliseconds=600000, storage_bytes=STORAGE_FLOOR
        ),
        settings=settings,
    )
    admission = ProcessDeclarationV2(
        schema_version="mineagent.process-admission.v2",
        id=admission_id,
        **_scope(fence),
        role="openevolve_orchestrator",
        logical_operation_id=launch_id,
        launch_ordinal=1,
        profile=PROFILE,
        input_sha256=inventory_sha,
        reservation_id=reservation.id,
        image_id=spec.runtime_image,
        ipc=HostTicketIPC(
            ipc_policy="host_ticket",
            ticket_sha256=hashlib.sha256(process_ticket.encode()).hexdigest(),
        ),
    )
    intent = ControllerIntent(
        schema_version="mineagent.oe-controller-intent.v1",
        id=uuid4(),
        admission=admission,
        bootstrap_sha256=hashlib.sha256(raw).hexdigest(),
        bootstrap_size_bytes=len(raw),
        seed_sha256=hashlib.sha256(seed).hexdigest(),
        seed_size_bytes=len(seed),
        input_layout="bootstrap_json_and_seed_py_only",
        issued_at=now,
        not_after=bootstrap.not_after,
    )
    # Circular epoch/admission references are validated together by deferred024
    # constraints; no weak projection table or transient missing commit is used.
    session.add(
        StrategySearchEpoch(
            id=epoch_id,
            **_scope(fence),
            admission_id=admission_id,
            bootstrap_sha256=inventory_sha,
            runtime_profile_sha256=SEARCH_PROFILE.sha256,
            ordinal=1,
            spec_sha256=spec.sha256,
            first_native_iteration=1,
            reason="initial",
        )
    )
    session.add(
        SearchControllerLaunch(
            id=launch_id,
            **_scope(fence),
            epoch_id=epoch_id,
            admission_id=admission_id,
            bootstrap=bootstrap.model_dump(mode="json"),
            bootstrap_canonical=raw.decode(),
            bootstrap_sha256=intent.bootstrap_sha256,
            input_inventory_sha256=inventory_sha,
            physical_profile_sha256=profile_sha256(),
        )
    )
    session.flush()
    persist_controller_intent(session, fence, intent)
    return intent, bootstrap, raw, seed
