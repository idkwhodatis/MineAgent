"""026 host-derived seed bootstrap; incoming controller events choose no parent."""

from sqlalchemy import select, text

from backend.app.factor_analysis_models import FactorAnalysisPlan
from backend.app.models import ResearchRun
from backend.app.rdagent_seed_models import FactorSeededSubmission
from backend.app.rdagent_seed_services import saved_seed_metadata
from backend.app.rdagent_seed_submission import validate_saved_seed_binding
from packages.rdagent_adapter.provider_bootstrap import (
    RDProviderSeededBootstrap,
    build_operational_bootstrap,
)
from packages.rdagent_adapter.seeded_bootstrap import RDSeededOperationalBootstrap


def seeded_bootstrap_if_declared(session, fence, *, binding, spec, config):
    # Historical databases have no seeded table; do not query an absent family.
    if session.scalars(text("SELECT version_num FROM alembic_version")).all() not in (
        ["026_rd_seeded_submissions"],
        ["027_rd_controller_v3"],
        ["027a_contract_repairs"],
        ["027b_provider_material"],
    ):
        return None
    row = session.scalar(
        select(FactorSeededSubmission).filter_by(
            owner_id=fence.owner_id, run_id=fence.run_id
        )
    )
    if row is None:
        return None
    run = session.get(ResearchRun, fence.run_id)
    plan = session.get(FactorAnalysisPlan, row.analysis_plan_id)
    validate_saved_seed_binding(
        row, run, spec, plan, fence.owner_id, run.idempotency_key
    )
    seed = saved_seed_metadata(session, fence.owner_id, row)
    return build_operational_bootstrap(
        owner_id=fence.owner_id,
        run_id=fence.run_id,
        attempt_id=fence.attempt_id,
        generation=fence.generation,
        spec_id=spec.id,
        attempt_binding_id=binding.id,
        config=config,
        config_sha256=spec.config_sha256,
        input_sha256=spec.input_sha256,
        seeded_submission_id=row.id,
        seed=seed,
    )


def reread_bootstrap_parent(session, fence, bootstrap, settings):
    """Fresh factor-scope read before physical work; no historical replay dependency."""
    if not isinstance(
        bootstrap, (RDSeededOperationalBootstrap, RDProviderSeededBootstrap)
    ):
        return
    from backend.app.rdagent_seed_services import reread_saved_seed
    from backend.app.research_lifecycle import require_neutral_transaction
    from backend.app.services import DomainError

    require_neutral_transaction(session, fence, resource_scope="factor_numeric")
    row = session.scalar(
        select(FactorSeededSubmission).filter_by(
            owner_id=fence.owner_id,
            run_id=fence.run_id,
            id=bootstrap.seeded_submission_id,
        )
    )
    if (
        row is None
        or reread_saved_seed(session, fence.owner_id, row, settings) != bootstrap.seed
    ):
        raise DomainError("seed_parent_drift", "Admitted seed bytes differ", 409)
