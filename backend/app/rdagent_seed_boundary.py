"""Persisted026 user-control boundary; no current runtime or filesystem gate.

Cancellation/recovery must remain possible after a runtime is disabled or parent
files disappear. Exact immutable receipt/spec/plan metadata is still required.
"""

from sqlalchemy import select

from backend.app.factor_analysis_models import FactorAnalysisPlan
from backend.app.rdagent_models import FactorResearchSpec
from backend.app.rdagent_seed_models import FactorSeededSubmission
from backend.app.rdagent_seed_submission import validate_saved_seed_binding


def seeded_boundary_id(session, run, revisions):
    if revisions not in (
        ["026_rd_seeded_submissions"],
        ["027_rd_controller_v3"],
        ["027a_contract_repairs"],
        ["027b_provider_material"],
    ):
        return None
    row = session.scalar(
        select(FactorSeededSubmission).filter_by(owner_id=run.owner_id, run_id=run.id)
    )
    if row is None:
        return None
    validate_saved_seed_binding(
        row,
        run,
        session.get(FactorResearchSpec, row.spec_id),
        session.get(FactorAnalysisPlan, row.analysis_plan_id),
        run.owner_id,
        run.idempotency_key,
    )
    return row.id
