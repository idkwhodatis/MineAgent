"""Contain one existing027 engine unit; no launch, result import or settlement."""

from sqlalchemy import select, text

from backend.app.core.maintenance import shared_lifecycle
from backend.app.factor_native_io import native_storage_guard
from backend.app.models import ExecutionAttempt, ResearchRun
from backend.app.oe_structured_identity import decode_candidate, decode_spec
from backend.app.oe_structured_models import (
    OEStructuredCandidate,
    OEStructuredExecution,
)
from backend.app.research_lifecycle import _try_neutral_lock
from backend.app.research_process_models import (
    ResearchProcessAdmission,
    ResearchProcessState,
)
from backend.app.structured_launch_services import record_structured_containment
from backend.app.structured_process_services import _persisted
from packages.research_runtime.process_contracts import ProcessError, canonical
from runtimes.research_supervisor.structured_factor_supervisor import (
    StructuredSupervisor,
)
from runtimes.structured_factor_engine.journal import (
    StructuredInputBinding,
    StructuredIntent,
)


def _binding(session, admission_id):
    if session.scalars(text("SELECT version_num FROM alembic_version")).all() not in (
        ["028_oe_structured_search"],
        ["029_research_workflow"],
        ["030_declared_upload_basket"],
        ["031_workflow_structured_stage"],
        ["032_normal_oe_note_pins"],
        ["032a_rd_terminal_results"],
        ["032b_oe_append_run_state"],
    ):
        raise ProcessError("oe_structured_recovery_schema")
    row = session.get(ResearchProcessAdmission, admission_id)
    if row is None:
        raise ProcessError("oe_structured_recovery_missing")
    _try_neutral_lock(session, f"submit:{row.owner_id}")
    run = session.scalar(
        select(ResearchRun)
        .filter_by(id=row.run_id, owner_id=row.owner_id)
        .with_for_update(nowait=True)
    )
    attempt = session.scalar(
        select(ExecutionAttempt)
        .filter_by(id=row.attempt_id, run_id=row.run_id, generation=row.generation)
        .with_for_update(nowait=True)
    )
    if run is None or attempt is None:
        raise ProcessError("oe_structured_recovery_scope")
    # Acquire the exact rows NOWAIT before the unchanged generic reader takes
    # its reentrant row locks; do not introduce a blocking recovery path.
    if (
        session.get(
            ResearchProcessAdmission, admission_id, with_for_update={"nowait": True}
        )
        is None
        or session.get(
            ResearchProcessState, admission_id, with_for_update={"nowait": True}
        )
        is None
    ):
        raise ProcessError("oe_structured_recovery_missing")
    admission, _state, receipts = _persisted(session, admission_id)
    intent = StructuredIntent.model_validate_json(
        canonical(receipts["launch_intent"].payload)
    )
    execution = session.get(OEStructuredExecution, admission.logical_operation_id)
    candidate = (
        session.get(OEStructuredCandidate, execution.candidate_id)
        if execution is not None
        else None
    )
    if execution is None or candidate is None:
        raise ProcessError("oe_structured_recovery_execution")
    _spec, config = decode_spec(session, run)
    decode_candidate(session, run, candidate)
    scope = ("owner_id", "run_id", "attempt_id", "generation")
    expected = StructuredInputBinding(
        execution_id=execution.id,
        structured_strategy_version_id=execution.program_version_id,
        snapshot_id=execution.snapshot_id,
        strategy_input_sha256=execution.strategy_input_sha256,
        strategy_input_size_bytes=execution.strategy_input_size_bytes,
        market_sha256=execution.market_sha256,
        market_size_bytes=execution.market_size_bytes,
    )
    if (
        intent.admission != admission
        or intent.inputs != expected
        or any(
            getattr(execution, key) != getattr(admission, key)
            or getattr(candidate, key) != getattr(admission, key)
            for key in scope
        )
        or execution.program_version_id != candidate.program_version_id
        or execution.snapshot_id != config.fixed_inputs.snapshot_id
        or (execution.market_sha256, execution.market_size_bytes)
        != (config.fixed_inputs.market_sha256, config.fixed_inputs.market_size_bytes)
        or admission.image_id != config.runtime.engine_image_id
    ):
        raise ProcessError("oe_structured_recovery_binding")
    return admission, config.runtime.engine_installed_parent_image_id


def recover_engine_unit(factory, settings, admission_id):
    """Revoke the attempt separately before calling; a stop is not a result.

    Missing observed identity remains unknown. Uses immutable original runtime
    pins, never operator enablement or a fabricated current fence/Peer. Existing
    fixed supervisor verifies actual journal/container/kernel before recording.
    """
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        native_storage_guard(settings.storage_root),
    ):
        with factory() as session:
            admission, parent_image = _binding(session, admission_id)
        StructuredSupervisor(admission.image_id, parent_image).stop_and_verify(
            settings.storage_root, admission
        )
        with factory() as session:
            if _binding(session, admission_id) != (admission, parent_image):
                raise ProcessError("oe_structured_recovery_binding")
            record_structured_containment(
                session, admission_id, storage_root=settings.storage_root
            )
            session.commit()
        return True
