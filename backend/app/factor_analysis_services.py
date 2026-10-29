"""Fresh022 snapshot/label/plan binding; no host Qlib or detached result grants."""

import hashlib
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select, text

from backend.app.execution_snapshot_models import ExecutionSnapshotPublication
from backend.app.execution_snapshot_services import (
    decode_publication,
    read_execution_snapshot,
)
from backend.app.factor_analysis_models import (
    FactorAnalysisPlan,
    FactorDirectAnalysisIntent,
)
from backend.app.factor_native_io import require_native_storage_guard
from backend.app.models import DatasetVersion
from backend.app.rdagent_services import require_research_spec
from backend.app.research_lifecycle import require_neutral_transaction
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.qlib_adapter.direct_wire import DirectInput, write_input
from packages.qlib_adapter.execution_binding import bind_completed_snapshot
from packages.qlib_adapter.objective import DirectObjectiveDeclaration

REVISION = "022_factor_analysis"
SCOPE = ("owner_id", "run_id", "attempt_id", "generation")


def _error():
    return DomainError(
        "factor_analysis_unverified",
        "Exact declared analysis authority is unavailable",
        409,
    )


def _require(session, fence):
    require_neutral_transaction(session, fence, resource_scope="factor_numeric")
    revisions = session.scalars(text("SELECT version_num FROM alembic_version")).all()
    if revisions not in (
        [REVISION],
        ["023_rd_submissions"],
        ["024_openevolve_initial"],
        ["025_structured_factor_strategy"],
        ["026_rd_seeded_submissions"],
        ["027_rd_controller_v3"],
        ["027a_contract_repairs"],
        ["027b_provider_material"],
        ["027c_seeded_controller_v4"],
    ):
        raise _error()


def _plan(session, fence):
    from packages.rdagent_adapter.configured_source import ConfiguredSourcePolicy

    _require(session, fence)
    plan = session.scalar(
        select(FactorAnalysisPlan).filter_by(
            owner_id=fence.owner_id, run_id=fence.run_id
        )
    )
    if plan is None:
        raise _error()
    spec, config = require_research_spec(session, fence)
    from backend.app.factor_analysis_canonical import read_objective

    objective = read_objective(plan)
    policy = ConfiguredSourcePolicy.model_validate(plan.source_policy)
    locks = {item.role: item.sha256 for item in config.runtime.dependency_locks}
    if (
        plan.spec_id,
        plan.config_sha256,
        plan.input_sha256,
        plan.objective_sha256,
        plan.source_policy_sha256,
        plan.label_source_dataset_id,
        plan.label_source_manifest_sha256,
        plan.qlib_image_id,
        plan.analysis_dependency_lock_sha256,
        objective.window,
    ) != (
        spec.id,
        config.sha256,
        config.input.sha256,
        objective.sha256,
        digest(policy),
        config.input.dataset.dataset_version_id,
        config.input.dataset.content_sha256,
        config.runtime.analysis_image,
        locks["analysis"],
        config.input.window,
    ):
        raise _error()
    source = session.get(DatasetVersion, plan.label_source_dataset_id)
    if source is None or (source.owner_id, source.content_sha256) != (
        fence.owner_id,
        plan.label_source_manifest_sha256,
    ):
        raise _error()
    return plan, objective


@dataclass(frozen=True)
class ResolvedAnalysisBinding:
    analysis_id: UUID
    plan_id: UUID
    snapshot_id: UUID
    label_source_id: UUID
    qlib_image_id: str
    qlib_installed_parent_image_id: str
    analysis_dependency_lock_sha256: str
    protocol_sha256: str
    analysis_input_sha256: str
    analysis_input_size_bytes: int
    qualification: str = "current_metadata_not_physical_or_launch_authority"


def require_analysis_plan(session, fence, analysis_id):
    """Current transaction metadata only; input reader separately rereads files."""
    plan, _ = _plan(session, fence)
    row = session.get(FactorDirectAnalysisIntent, analysis_id)
    if (
        row is None
        or any(getattr(row, k) != getattr(fence, k) for k in SCOPE)
        or row.plan_id != plan.id
        or row.label_source_id != plan.label_source_dataset_id
    ):
        raise _error()
    return ResolvedAnalysisBinding(
        row.id,
        plan.id,
        row.snapshot_id,
        row.label_source_id,
        plan.qlib_image_id,
        plan.qlib_installed_parent_image_id,
        plan.analysis_dependency_lock_sha256,
        row.protocol_sha256,
        row.analysis_input_sha256,
        row.analysis_input_size_bytes,
    )


def _materialize_with_snapshot(session, fence, snapshot_id, settings):
    from backend.app.rdagent_configured_source import resolve_configured_labels

    plan, objective = _plan(session, fence)
    require_native_storage_guard(settings.storage_root)
    verified = read_execution_snapshot(session, fence, snapshot_id, settings)
    publication = session.get(ExecutionSnapshotPublication, snapshot_id)
    if publication is None:
        raise _error()
    journal = decode_publication(publication)
    labels = resolve_configured_labels(session, fence, settings)
    if (
        labels.dataset_id,
        labels.source_manifest_sha256,
        labels.source_policy_sha256,
    ) != (
        plan.label_source_dataset_id,
        plan.label_source_manifest_sha256,
        plan.source_policy_sha256,
    ):
        raise _error()
    projection = bind_completed_snapshot(
        snapshot_id=snapshot_id,
        snapshot_input=journal.manifest.input,
        output_sha256=verified.output_sha256,
        rows=verified.rows,
        label_manifest=labels.manifest,
        labels=labels.rows,
        calendar_sessions=tuple(day.session_date for day in labels.calendar.sessions),
    )
    if (
        objective.window != projection.protocol.window
        or objective.label_horizon_sessions != labels.manifest.horizon_sessions
    ):
        raise _error()
    value = DirectInput(
        protocol=projection.protocol,
        factors=projection.factors,
        labels=projection.labels,
        calendar_sessions=projection.calendar_sessions,
    )
    raw = write_input(value)
    return plan, labels, value, raw, verified


def _materialize(session, fence, snapshot_id, settings):
    # Preserve the existing input-only API. No caller can supply a snapshot DTO.
    return _materialize_with_snapshot(session, fence, snapshot_id, settings)[:4]


def prepare_direct_analysis_intent(session, fence, snapshot_id, settings):
    """Flush-only exact intent; no process reservation, launch or implicit commit."""
    plan, labels, value, raw = _materialize(session, fence, snapshot_id, settings)
    row = session.scalar(
        select(FactorDirectAnalysisIntent).filter_by(
            plan_id=plan.id, snapshot_id=snapshot_id
        )
    )
    if row is not None:
        resolve_direct_analysis_input(session, fence, row.id, settings)
        return row.id
    row = FactorDirectAnalysisIntent(
        id=uuid4(),
        **{k: getattr(fence, k) for k in SCOPE},
        plan_id=plan.id,
        snapshot_id=snapshot_id,
        label_source_id=labels.dataset_id,
        label_manifest=labels.manifest.model_dump(mode="json"),
        label_manifest_sha256=labels.manifest.sha256,
        label_recipe_sha256=labels.label_recipe_sha256,
        protocol=value.protocol.model_dump(mode="json"),
        protocol_sha256=value.protocol.sha256,
        analysis_input_sha256=hashlib.sha256(raw).hexdigest(),
        analysis_input_size_bytes=len(raw),
    )
    session.add(row)
    session.flush()
    return row.id


def resolve_direct_analysis_input(session, fence, analysis_id, settings):
    """Rebuild actual snapshot+independent label bytes every fresh transaction."""
    binding = require_analysis_plan(session, fence, analysis_id)
    _, labels, value, raw = _materialize(session, fence, binding.snapshot_id, settings)
    row = session.get(FactorDirectAnalysisIntent, analysis_id)
    if (
        row.label_manifest,
        row.label_manifest_sha256,
        row.label_recipe_sha256,
        row.protocol,
        row.protocol_sha256,
        row.analysis_input_sha256,
        row.analysis_input_size_bytes,
    ) != (
        labels.manifest.model_dump(mode="json"),
        labels.manifest.sha256,
        labels.label_recipe_sha256,
        value.protocol.model_dump(mode="json"),
        value.protocol.sha256,
        hashlib.sha256(raw).hexdigest(),
        len(raw),
    ):
        raise _error()
    return value


def prepare_analysis_plan(
    session,
    fence,
    *,
    objective,
    source_policy,
    qlib_installed_parent_image_id,
    settings,
):
    """Declare before generation, flush only; a pinned image is NOT launch approval."""
    import re

    from backend.app.models import ResearchRun
    from backend.app.rdagent_configured_source import (
        resolve_configured_labels,
        resolve_configured_source,
    )
    from backend.app.rdagent_models import FactorGenerationStart
    from packages.rdagent_adapter.configured_source import ConfiguredSourcePolicy

    _require(session, fence)
    objective = DirectObjectiveDeclaration.model_validate(objective)
    policy = ConfiguredSourcePolicy.model_validate(source_policy)
    if (
        type(qlib_installed_parent_image_id) is not str
        or re.fullmatch(r"sha256:[a-f0-9]{64}", qlib_installed_parent_image_id) is None
    ):
        raise _error()
    run = session.scalar(
        select(ResearchRun)
        .filter_by(id=fence.run_id, owner_id=fence.owner_id)
        .with_for_update(nowait=True)
    )
    if run is None:
        raise _error()
    spec, config = require_research_spec(session, fence)
    locks = {item.role: item.sha256 for item in config.runtime.dependency_locks}
    expected = {
        "owner_id": fence.owner_id,
        "run_id": fence.run_id,
        "spec_id": spec.id,
        "config_sha256": config.sha256,
        "input_sha256": config.input.sha256,
        "objective": objective.model_dump(mode="json"),
        "objective_canonical": canonical_bytes(objective).decode("ascii"),
        "objective_sha256": objective.sha256,
        "source_policy": policy.model_dump(mode="json"),
        "source_policy_sha256": digest(policy),
        "label_source_dataset_id": config.input.dataset.dataset_version_id,
        "label_source_manifest_sha256": config.input.dataset.content_sha256,
        "qlib_image_id": config.runtime.analysis_image,
        "qlib_installed_parent_image_id": qlib_installed_parent_image_id,
        "analysis_dependency_lock_sha256": locks["analysis"],
    }
    if objective.window != config.input.window:
        raise _error()
    row = session.scalar(
        select(FactorAnalysisPlan).filter_by(
            owner_id=fence.owner_id,
            run_id=fence.run_id,
        )
    )
    if row is not None:
        if any(getattr(row, key) != value for key, value in expected.items()):
            raise _error()
    else:
        started = session.scalar(
            select(FactorGenerationStart.id)
            .filter_by(
                owner_id=fence.owner_id,
                run_id=fence.run_id,
            )
            .limit(1)
        )
        if started is not None:
            raise _error()
        row = FactorAnalysisPlan(id=uuid4(), **expected)
        session.add(row)
        session.flush()
    # These readers derive source and forward-label coverage from THIS persisted
    # declaration, never a caller-supplied label recipe or future-data mount.
    resolve_configured_source(session, fence, settings)
    resolve_configured_labels(session, fence, settings)
    from backend.app.rdagent_configured_exposure import (
        record_configured_source_exposure,
    )

    record_configured_source_exposure(session, fence, settings)
    return row.id
