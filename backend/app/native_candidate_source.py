"""Resolve pre-execution cutoff/source lineage; no successful evaluation needed."""

from backend.app.research_lifecycle import require_neutral_transaction
from backend.app.services import DomainError
from packages.factors.contracts import SnapshotInput, digest
from packages.rdagent_adapter.assembly import validate_projection_binding
from packages.rdagent_adapter.prefix import PrefixManifest


def require_candidate_source(
    session,
    fence,
    *,
    cutoff_id,
    source_id,
    expected_factor_version_id,
    expected_code_object_id,
    expected_sha256,
    expected_start_id,
    expected_prefix_sha256,
    expected_snapshot_input_sha256,
    expected_cutoff,
    settings,
):
    from backend.app.rdagent_models import FactorCutoffExecution, FactorEvaluationStart
    from backend.app.rdagent_services import _scope_row, require_research_spec
    from backend.app.rdagent_source_resolution import require_source_code
    from packages.rdagent_adapter.host_evidence import research_snapshot_input

    require_neutral_transaction(session, fence, resource_scope="factor_objects")
    source = require_source_code(
        session,
        fence,
        source_id=source_id,
        expected_factor_version_id=expected_factor_version_id,
        expected_code_object_id=expected_code_object_id,
        expected_sha256=expected_sha256,
        settings=settings,
    )
    cutoff = _scope_row(session, FactorCutoffExecution, cutoff_id, fence, lock=True)
    evaluation = _scope_row(
        session, FactorEvaluationStart, cutoff.evaluation_id, fence, lock=True
    )
    _, config = require_research_spec(session, fence)
    snapshot = SnapshotInput.model_validate(evaluation.snapshot_input)
    expected = research_snapshot_input(
        config,
        factor_version_id=source.factor_version_id,
        code_sha256=source.code_sha256,
        exposure_history=snapshot.exposure_history,
    )
    prefix = PrefixManifest.model_validate(cutoff.projection)
    validate_projection_binding(snapshot, prefix)
    if not (0 <= cutoff.cutoff_ordinal < evaluation.expected_cutoffs) or not (
        snapshot.window.start <= prefix.output_session < snapshot.window.end
    ):
        raise DomainError(
            "native_candidate_cutoff_drift",
            "Cutoff lies outside frozen evaluation",
            409,
        )
    if (
        source.start_id,
        cutoff.start_id,
        evaluation.start_id,
        cutoff.evaluated_source_id,
        evaluation.evaluated_source_id,
        cutoff.factor_version_id,
        evaluation.factor_version_id,
        evaluation.input_sha256,
        snapshot.sha256,
        prefix.snapshot_input_sha256,
        cutoff.projection_sha256,
        digest(prefix),
        prefix.host_cutoff,
        prefix.candidate_image,
        prefix.candidate_profile,
        prefix.normalizer_image,
        prefix.normalizer_profile,
    ) != (
        expected_start_id,
        expected_start_id,
        expected_start_id,
        source_id,
        source_id,
        expected_factor_version_id,
        expected_factor_version_id,
        expected_snapshot_input_sha256,
        expected_snapshot_input_sha256,
        expected_snapshot_input_sha256,
        expected_prefix_sha256,
        expected_prefix_sha256,
        expected_cutoff,
        config.runtime.candidate_image,
        config.runtime.candidate_profile,
        config.runtime.normalizer_image,
        config.runtime.normalizer_profile,
    ) or snapshot != expected:
        raise DomainError(
            "native_candidate_source_drift",
            "Persisted candidate source/input lineage differs",
            409,
        )
    return cutoff, prefix


def require_configured_candidate_source(session, fence, **kwargs):
    """Explicit022 successor: old lineage plus fresh full retained-input witness."""
    from backend.app.rdagent_configured_inputs import require_configured_input_witness

    cutoff, prefix = require_candidate_source(session, fence, **kwargs)
    _, checked = require_configured_input_witness(
        session, fence, execution_id=cutoff.id, settings=kwargs["settings"]
    )
    if checked.prefix != prefix:
        raise DomainError(
            "configured_native_input_drift", "Exact input witness required", 409
        )
    return cutoff, prefix
