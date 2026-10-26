"""Disabled RD evaluation persistence and conservative host-read exposure mapping."""

from uuid import UUID

from fastapi.encoders import jsonable_encoder

from backend.app.factor_services import inherited_exposure_claims, public
from backend.app.models import DatasetVersion, DataUsageExposure
from backend.app.services import DomainError
from packages.factors.contracts import (
    DateWindow,
    ExposureClaim,
    ExposureHistory,
    canonical_bytes,
    digest,
)


def local_exposure_history(session, owner_id, version_id):
    """Read persisted owner-bound evidence; never accept an incoming claim envelope.

    Unknown imported formats fail closed. Even verified local bytes are retained
    as unresolved identity mappings, not a clean/OOS/PIT or computation claim.
    Original purpose/window and persisted evidence IDs/hashes survive mapping.
    """
    raw = inherited_exposure_claims(session, owner_id, [version_id])
    if len(raw) > 256 or len(canonical_bytes(raw)) > 262144:
        raise DomainError(
            "factor_exposure_capacity", "Exposure history exceeds profile", 409
        )
    claims = []
    seen = set()
    for envelope in raw:
        if (
            type(envelope) is not dict
            or set(envelope)
            != {"source", "evidence_sha256", "dataset_content_sha256", "evidence"}
            or envelope["source"] != "locally_recorded_claim"
            or type(envelope["evidence"]) is not dict
        ):
            raise DomainError(
                "factor_exposure_mapping_unsupported",
                "Exposure envelope requires a registered mapping",
                409,
            )
        evidence = envelope["evidence"]
        try:
            identity = UUID(evidence["id"])
        except (ValueError, KeyError, TypeError):
            raise DomainError(
                "factor_exposure_identity_invalid", "Exposure identity is invalid", 409
            ) from None
        row = session.get(DataUsageExposure, identity)
        if row is None or row.owner_id != owner_id or row.id in seen:
            raise DomainError(
                "factor_exposure_scope",
                "Exposure evidence must be unique and owned",
                409,
            )
        current = jsonable_encoder(public(row))
        dataset = (
            session.get(DatasetVersion, row.dataset_version_id)
            if row.dataset_version_id is not None
            else None
        )
        if (
            dataset is None
            or dataset.owner_id != owner_id
            or dataset.content_sha256 != envelope["dataset_content_sha256"]
            or current != evidence
            or digest(current) != envelope["evidence_sha256"]
        ):
            raise DomainError(
                "factor_exposure_evidence_drift",
                "Persisted exposure bytes or dataset identity differ",
                409,
            )
        claims.append(
            ExposureClaim(
                origin_namespace="mineagent.local.exposure",
                evidence_id=str(row.id),
                evidence_sha256=envelope["evidence_sha256"],
                window=DateWindow(start=row.start_date, end=row.end_date),
                purpose=row.purpose,
                identity_mapping="unresolved",
                mapping_sha256=None,
            )
        )
        seen.add(row.id)
    return ExposureHistory(
        claims=tuple(claims),
        completeness="partial" if claims else "unknown",
        unresolved_reasons=(
            "source_history_incomplete",
            "local_identity_mapping_unverified",
        )
        if claims
        else ("source_history_incomplete",),
    )


def open_generation_evaluation(
    session, fence, *, start_id, source_id, calendar, settings
):
    """Flush one logical ticket for current generated source; repeated reads reuse it.

    Historical/RAG source reuse is rejected until verified adoption/resume lineage
    is implemented. This opens no process and publishes no snapshot or feedback.
    """
    from uuid import uuid4

    from sqlalchemy import select

    from backend.app.factor_models import FactorVersion
    from backend.app.factor_services import verify_version
    from backend.app.rdagent_models import (
        FactorEvaluationStart,
        FactorGenerationOutcome,
        FactorGenerationSource,
        FactorGenerationStart,
    )
    from backend.app.rdagent_services import _scope, _scope_row, require_research_spec
    from packages.factors.contracts import SnapshotInput
    from packages.factors.fixtures import MarketCalendar
    from packages.rdagent_adapter.host_evidence import (
        research_definition,
        research_snapshot_input,
    )

    _, config = require_research_spec(session, fence)
    dataset = session.get(DatasetVersion, config.input.dataset.dataset_version_id)
    if (
        dataset is None
        or dataset.owner_id != fence.owner_id
        or dataset.content_sha256 != config.input.dataset.content_sha256
        or dataset.start_date > config.input.window.start
        or dataset.end_date < config.input.window.end
    ):
        raise DomainError(
            "factor_evaluation_dataset_drift",
            "Owned dataset identity/window differs from frozen input",
            409,
        )
    start = _scope_row(session, FactorGenerationStart, start_id, fence, lock=True)
    source = _scope_row(session, FactorGenerationSource, source_id, fence)
    if source.start_id != start.id:
        raise DomainError(
            "factor_evaluation_lineage_unsupported",
            "Historical source reuse needs verified continuation lineage",
            409,
        )
    calendar = MarketCalendar.model_validate_json(calendar.model_dump_json())
    if (
        calendar.sha256 != config.input.calendar_sha256
        or calendar.timezone != config.input.market_timezone
    ):
        raise DomainError(
            "factor_evaluation_calendar_drift",
            "Calendar differs from frozen input",
            409,
        )
    count = sum(
        config.input.window.start <= day.session_date < config.input.window.end
        for day in calendar.sessions
    )
    if (
        not 1 <= count <= 4096
        or count * len(config.input.calculation_universe.listing_ids) > 1000000
    ):
        raise DomainError(
            "factor_evaluation_capacity",
            "Stitched snapshot exceeds fixed row profile",
            409,
        )
    version = session.scalar(
        select(FactorVersion).filter_by(
            owner_id=fence.owner_id, id=source.factor_version_id
        )
    )
    definition = research_definition(config, source.source_sha256)
    if (
        version is None
        or version.code_sha256 != source.source_sha256
        or version.definition != definition.model_dump(mode="json")
        or version.definition_sha256 != definition.sha256
    ):
        raise DomainError(
            "factor_evaluation_source_drift",
            "Source definition differs from frozen evaluation",
            409,
        )
    verify_version(session, fence.owner_id, version, settings)
    current = session.scalar(
        select(FactorEvaluationStart).filter_by(**_scope(fence), start_id=start.id)
    )
    if current is not None:
        stored = SnapshotInput.model_validate(current.snapshot_input)
        expected = research_snapshot_input(
            config,
            factor_version_id=version.id,
            code_sha256=source.source_sha256,
            exposure_history=stored.exposure_history,
        )
        if (
            current.evaluated_source_id,
            current.factor_version_id,
            current.expected_cutoffs,
            current.input_sha256,
            current.snapshot_input,
        ) != (
            source.id,
            version.id,
            count,
            expected.sha256,
            expected.model_dump(mode="json"),
        ):
            raise DomainError(
                "factor_evaluation_ticket_drift",
                "Logical evaluation ticket is immutable",
                409,
            )
        return current
    if (
        session.scalar(
            select(FactorGenerationOutcome).filter_by(
                **_scope(fence), start_id=start.id
            )
        )
        is not None
    ):
        raise DomainError(
            "factor_generation_closed",
            "Terminal generation cannot open evaluation",
            409,
        )
    from backend.app.rdagent_boundary_services import require_unobserved_generation

    require_unobserved_generation(session, fence, start.id)
    snapshot = research_snapshot_input(
        config,
        factor_version_id=version.id,
        code_sha256=source.source_sha256,
        exposure_history=local_exposure_history(session, fence.owner_id, version.id),
    )
    ticket = FactorEvaluationStart(
        id=uuid4(),
        **_scope(fence),
        start_id=start.id,
        evaluated_source_id=source.id,
        factor_version_id=version.id,
        snapshot_input=snapshot.model_dump(mode="json"),
        input_sha256=snapshot.sha256,
        expected_cutoffs=count,
    )
    session.add(ticket)
    session.flush()
    return ticket
