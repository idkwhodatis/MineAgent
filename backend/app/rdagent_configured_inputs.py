"""Fresh configured source→four exact numeric input files; no launcher authority."""

import hashlib
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select

from backend.app.factor_models import FactorVersion
from backend.app.rdagent_configured_source import _plan, resolve_configured_source
from backend.app.rdagent_controller_services import require_rd_controller
from backend.app.rdagent_models import (
    FactorCutoffExecution,
    FactorEvaluationStart,
    FactorGenerationSource,
)
from backend.app.rdagent_services import _scope, _scope_row, require_research_spec
from backend.app.rdagent_source_resolution import require_source_code
from packages.factors.contracts import SnapshotInput, canonical_bytes, digest
from packages.rdagent_adapter.host_evidence import research_snapshot_input
from packages.rdagent_adapter.numeric_profile import candidate_inputs
from packages.rdagent_adapter.prefix import project_prefix
from packages.research_runtime.numeric_process_journal import (
    NumericInput,
    NumericInputFile,
)
from packages.research_runtime.process_contracts import ProcessError
from packages.research_runtime.process_declarations_v2 import (
    NoIPC,
    ProcessDeclarationV2,
)


@dataclass(frozen=True)
class ConfiguredCandidateInputs:
    evaluation_id: object
    source: object
    prefix: object
    binding: NumericInput
    files: tuple[tuple[str, bytes], ...]
    witness: dict


def resolve_candidate_inputs(
    session, fence, *, evaluation_id, cutoff_ordinal, settings
):
    """Data only, regenerated from exact persisted source/dataset every call."""
    _, config = require_research_spec(session, fence)
    market = resolve_configured_source(session, fence, settings)
    plan, _ = _plan(session, fence)
    evaluation = _scope_row(
        session, FactorEvaluationStart, evaluation_id, fence, lock=True
    )
    source = _scope_row(
        session, FactorGenerationSource, evaluation.evaluated_source_id, fence
    )
    version = session.scalar(
        select(FactorVersion).filter_by(
            owner_id=fence.owner_id, id=source.factor_version_id
        )
    )
    if version is None:
        raise ProcessError("configured_source_missing")
    code = require_source_code(
        session,
        fence,
        source_id=source.id,
        expected_factor_version_id=version.id,
        expected_code_object_id=version.code_object_id,
        expected_sha256=source.source_sha256,
        settings=settings,
    )
    snapshot = SnapshotInput.model_validate(evaluation.snapshot_input)
    expected = research_snapshot_input(
        config,
        factor_version_id=version.id,
        code_sha256=code.code_sha256,
        exposure_history=snapshot.exposure_history,
    )
    sessions = tuple(
        day
        for day in market.calendar.sessions
        if snapshot.window.start <= day.session_date < snapshot.window.end
    )
    if (
        snapshot != expected
        or evaluation.input_sha256 != snapshot.sha256
        or (evaluation.start_id, evaluation.factor_version_id)
        != (source.start_id, version.id)
        or evaluation.expected_cutoffs != len(sessions)
        or type(cutoff_ordinal) is not int
        or not 0 <= cutoff_ordinal < len(sessions)
    ):
        raise ProcessError("configured_evaluation_drift")
    prefix, files, binding = build_candidate_projection(
        config, snapshot, market, code, cutoff_ordinal
    )
    witness = {
        "schema_version": "mineagent.configured-factor-input-witness.v1",
        **{
            key: str(value) if key != "generation" else value
            for key, value in _scope(fence).items()
        },
        "evaluation_id": str(evaluation.id),
        "source_id": str(source.id),
        "plan_id": str(plan.id),
        "source_read_witness_id": str(code.read_witness_id),
        "source_read_witness_sha256": code.read_witness_sha256,
        "dataset_id": str(market.dataset.dataset_version_id),
        "dataset_manifest_sha256": market.dataset.content_sha256,
        "availability_revision_sha256": market.dataset.availability_revision_sha256,
        "calendar_sha256": market.calendar.sha256,
        "input_sha256": digest(binding.model_dump(mode="json")),
        "projection_sha256": prefix.sha256,
        "source_artifacts": dict(market.artifact_sha256),
        "files": [item.model_dump(mode="json") for item in binding.files],
    }
    if len(canonical_bytes(witness)) > 4096:
        raise ProcessError("configured_witness_bound")
    return ConfiguredCandidateInputs(
        evaluation.id, code, prefix, binding, files, witness
    )


def build_candidate_projection(config, snapshot, market, code, cutoff_ordinal):
    """Pure reconstruction shared with cold verification; data, not authority."""
    sessions = tuple(
        day
        for day in market.calendar.sessions
        if snapshot.window.start <= day.session_date < snapshot.window.end
    )
    if type(cutoff_ordinal) is not int or not 0 <= cutoff_ordinal < len(sessions):
        raise ProcessError("configured_cutoff_ordinal")
    day = sessions[cutoff_ordinal]
    calendar_days = tuple(item.session_date for item in market.calendar.sessions)
    position = calendar_days.index(day.session_date)
    lag_day = market.calendar.sessions[position + config.input.lag_sessions]
    prefix, projected = project_prefix(
        snapshot,
        host_cutoff=day.close_time,
        output_event_time=day.close_time,
        output_session=day.session_date,
        calendar_sessions=calendar_days,
        observations=market.observations,
        candidate_image=config.runtime.candidate_image,
        normalizer_image=config.runtime.normalizer_image,
        lag_available_at=lag_day.close_time,
        max_output_rows=config.max_output_rows_per_cutoff,
        max_output_bytes=config.max_output_bytes_per_cutoff,
    )
    _, files, _ = candidate_inputs(
        prefix,
        snapshot_input=snapshot,
        source=code.code_utf8,
        expected_source_sha256=code.code_sha256,
        projected_files=projected,
    )
    files = tuple(sorted(files.items()))
    binding = NumericInput(
        schema_version="mineagent.numeric-process-input.v2",
        source_object_id=code.code_object_id,
        source_sha256=code.code_sha256,
        prefix_sha256=prefix.sha256,
        cutoff=prefix.host_cutoff,
        files=tuple(
            NumericInputFile(
                destination=name,
                sha256=hashlib.sha256(raw).hexdigest(),
                size_bytes=len(raw),
            )
            for name, raw in files
        ),
    )
    return prefix, files, binding


def persist_configured_input_witness(
    session, fence, authorization, *, execution_id, settings
):
    """Exact current live controller required for first append; data never a grant."""
    require_rd_controller(session, fence, authorization)
    return _require_configured_input_witness(
        session, fence, execution_id=execution_id, settings=settings, allow_create=True
    )


def require_configured_input_witness(session, fence, *, execution_id, settings):
    return _require_configured_input_witness(
        session, fence, execution_id=execution_id, settings=settings, allow_create=False
    )


def _require_configured_input_witness(
    session, fence, *, execution_id, settings, allow_create
):
    from backend.app.rdagent_configured_input_models import FactorConfiguredInputWitness
    from backend.app.research_process_models import ResearchProcessAdmission
    from packages.research_runtime.process_persistence_codec import (
        ROW_FIELDS,
        decode_row,
    )

    execution = _scope_row(
        session, FactorCutoffExecution, execution_id, fence, lock=True
    )
    resolved = resolve_candidate_inputs(
        session,
        fence,
        evaluation_id=execution.evaluation_id,
        cutoff_ordinal=execution.cutoff_ordinal,
        settings=settings,
    )
    admission_row = _scope_row(
        session, ResearchProcessAdmission, execution.candidate_admission_id, fence
    )
    admission = decode_row({key: getattr(admission_row, key) for key in ROW_FIELDS})
    document = {
        **resolved.witness,
        "execution_id": str(execution.id),
        "admission_id": str(admission.id),
    }
    if (
        not isinstance(admission, ProcessDeclarationV2)
        or not isinstance(admission.ipc, NoIPC)
        or admission.launch_ordinal != 1
        or len(canonical_bytes(document)) > 4096
        or admission.input_sha256 != document["input_sha256"]
        or admission.logical_operation_id != execution.id
        or admission.role != "factor_cutoff_candidate"
        or admission.profile != resolved.prefix.candidate_profile
        or admission.image_id != resolved.prefix.candidate_image
        or execution.projection != resolved.prefix.model_dump(mode="json")
        or execution.projection_sha256 != resolved.prefix.sha256
        or (
            execution.start_id,
            execution.evaluated_source_id,
            execution.factor_version_id,
        )
        != (
            resolved.source.start_id,
            resolved.source.source_id,
            resolved.source.factor_version_id,
        )
    ):
        raise ProcessError("configured_cutoff_input_drift")
    values = dict(
        **_scope(fence),
        execution_id=execution.id,
        admission_id=admission.id,
        plan_id=UUID(document["plan_id"]),
        source_read_witness_id=resolved.source.read_witness_id,
        dataset_id=UUID(document["dataset_id"]),
        **{
            key: document[key]
            for key in (
                "dataset_manifest_sha256",
                "availability_revision_sha256",
                "calendar_sha256",
                "input_sha256",
                "projection_sha256",
            )
        },
        document=document,
        witness_sha256=digest(document),
    )
    existing = session.scalar(
        select(FactorConfiguredInputWitness).filter_by(execution_id=execution.id)
    )
    if existing is not None:
        if any(getattr(existing, key) != value for key, value in values.items()):
            raise ProcessError("configured_witness_replay_drift")
        return existing, resolved
    if not allow_create:
        raise ProcessError("configured_witness_missing")
    existing = FactorConfiguredInputWitness(id=uuid4(), **values)
    session.add(existing)
    session.flush()
    return existing, resolved
