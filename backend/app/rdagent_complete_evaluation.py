"""Fresh complete cutoff-inventory resolver for the maintained RD controller path.

This returns data, not publication authority or successful evaluation. The snapshot
publisher MUST independently read every actual stopped normalizer/report and bind
its primitive rows and output hashes. No Parquet/native parsing occurs here.
Only the frozen maintained synthetic provider is supported until a separately
accepted immutable dataset/calendar loader exists.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from backend.app.factor_models import FactorVersion
from backend.app.rdagent_models import (
    FactorCutoffExecution,
    FactorEvaluationStart,
    FactorGenerationSource,
)
from backend.app.rdagent_services import _scope, _scope_row, require_research_spec
from backend.app.rdagent_source_resolution import require_source_code
from backend.app.research_lifecycle import require_neutral_transaction
from backend.app.services import DomainError
from packages.factors.contracts import SnapshotInput
from packages.factors.fixtures import demo_fixture, demo_inputs
from packages.rdagent_adapter.host_evidence import research_snapshot_input
from packages.rdagent_adapter.prefix import PrefixManifest


def _error():
    return DomainError(
        "factor_evaluation_incomplete",
        "Exact complete maintained cutoff inventory required",
        409,
    )


@dataclass(frozen=True)
class EvaluationCutoff:
    execution_id: UUID
    candidate_admission_id: UUID
    normalizer_admission_id: UUID
    native_publication_id: UUID
    ordinal: int
    prefix: PrefixManifest


@dataclass(frozen=True)
class CompleteEvaluationInventory:
    evaluation_id: UUID
    generation_start_id: UUID
    source_id: UUID
    factor_version_id: UUID
    source_object_id: UUID
    snapshot_input: SnapshotInput
    cutoffs: tuple[EvaluationCutoff, ...]
    qualification: str = "complete_input_inventory_not_evaluation_success"


def maintained_calendar(config):
    maintained = demo_fixture(
        factor_version_id=UUID(int=1),
        dataset_version_id=config.input.dataset.dataset_version_id,
    ).manifest.input
    calendar, _ = demo_inputs()
    if (
        config.input.dataset != maintained.dataset
        or config.input.calendar_sha256 != maintained.calendar_sha256
        or config.input.market_timezone != maintained.market_timezone
        or config.input.calculation_universe != maintained.calculation_universe
        or config.input.input_fields != maintained.definition.input_fields
        or not maintained.window.start
        <= config.input.window.start
        < config.input.window.end
        <= maintained.window.end
    ):
        raise _error()
    return calendar


def resolve_complete_evaluation(session, fence, evaluation_id, settings):
    return _resolve_complete_evaluation(session, fence, evaluation_id, settings)


def resolve_configured_complete_evaluation(session, fence, evaluation_id, settings):
    from backend.app.rdagent_configured_inputs import require_configured_input_witness
    from backend.app.rdagent_configured_source import resolve_configured_source

    source = resolve_configured_source(session, fence, settings)
    return _resolve_complete_evaluation(
        session,
        fence,
        evaluation_id,
        settings,
        configured_calendar=source.calendar,
        configured_validator=require_configured_input_witness,
    )


def _resolve_complete_evaluation(
    session,
    fence,
    evaluation_id,
    settings,
    *,
    configured_calendar=None,
    configured_validator=None,
):
    """Must run inside the CURRENT combined factor_numeric transaction.

    Native019 modules are imported only when composed; no fake fallback is used
    if their actual persistence/claim verifier is unavailable. All returned IDs
    are read from scoped rows rather than supplied by an RPC message.
    """
    from backend.app.factor_native_models import (
        NativeHDFPublication,
        NativeNormalizerInput,
    )
    from backend.app.factor_native_services import read_completed_native

    require_neutral_transaction(session, fence, resource_scope="factor_numeric")
    _, config = require_research_spec(session, fence)
    evaluation = _scope_row(
        session, FactorEvaluationStart, evaluation_id, fence, lock=True
    )
    if evaluation.evaluated_source_id is None:
        raise _error()
    source = _scope_row(
        session, FactorGenerationSource, evaluation.evaluated_source_id, fence
    )
    version = session.scalar(
        select(FactorVersion).filter_by(
            owner_id=fence.owner_id, id=source.factor_version_id
        )
    )
    if (
        version is None
        or source.start_id != evaluation.start_id
        or evaluation.factor_version_id != version.id
    ):
        raise _error()
    require_source_code(
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
        code_sha256=source.source_sha256,
        exposure_history=snapshot.exposure_history,
    )
    if snapshot != expected or evaluation.input_sha256 != expected.sha256:
        raise _error()
    calendar = configured_calendar or maintained_calendar(config)
    days = tuple(
        day
        for day in calendar.sessions
        if snapshot.window.start <= day.session_date < snapshot.window.end
    )
    if not days or evaluation.expected_cutoffs != len(days):
        raise _error()
    executions = list(
        session.scalars(
            select(FactorCutoffExecution)
            .filter_by(**_scope(fence), evaluation_id=evaluation.id)
            .order_by(FactorCutoffExecution.cutoff_ordinal)
            .limit(4097)
        )
    )
    if len(executions) != len(days):
        raise _error()
    result = []
    for ordinal, (execution, day) in enumerate(zip(executions, days, strict=True)):
        prefix = PrefixManifest.model_validate(execution.projection)
        if configured_validator is not None:
            _, checked = configured_validator(
                session, fence, execution_id=execution.id, settings=settings
            )
            if checked.prefix != prefix:
                raise _error()
        if (
            execution.cutoff_ordinal != ordinal
            or execution.start_id != evaluation.start_id
            or execution.evaluated_source_id != source.id
            or execution.factor_version_id != version.id
            or prefix.sha256 != execution.projection_sha256
            or prefix.snapshot_input_sha256 != snapshot.sha256
            or prefix.output_session != day.session_date
            or prefix.output_event_time != day.close_time
            or prefix.host_cutoff != day.close_time
            or prefix.candidate_image != config.runtime.candidate_image
            or prefix.normalizer_image != config.runtime.normalizer_image
        ):
            raise _error()
        publications = list(
            session.scalars(
                select(NativeHDFPublication)
                .filter_by(**_scope(fence), cutoff_id=execution.id)
                .limit(2)
            )
        )
        if len(publications) != 1:
            raise _error()
        publication = publications[0]
        claims = list(
            session.scalars(
                select(NativeNormalizerInput)
                .filter_by(**_scope(fence), publication_id=publication.id)
                .limit(2)
            )
        )
        if (
            len(claims) != 1
            or publication.source_id != source.id
            or publication.admission_id != execution.candidate_admission_id
        ):
            raise _error()
        claim = claims[0]
        # The pre-launch claim verifier intentionally rejects stopped units.
        # Here re-read completed native bytes; the snapshot publisher invokes
        # read_normalized_cutoff for the stopped normalizer/report closure.
        read_completed_native(session, fence, publication.id, settings)
        if (claim.object_id, claim.sha256, claim.size_bytes, claim.storage_key) != (
            publication.object_id,
            publication.sha256,
            publication.size_bytes,
            publication.storage_key,
        ):
            raise _error()
        result.append(
            EvaluationCutoff(
                execution.id,
                execution.candidate_admission_id,
                claim.admission_id,
                publication.id,
                ordinal,
                prefix,
            )
        )
    return CompleteEvaluationInventory(
        evaluation.id,
        evaluation.start_id,
        source.id,
        version.id,
        version.code_object_id,
        snapshot,
        tuple(result),
    )
