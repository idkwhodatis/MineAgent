"""Installed022 + persisted pre-generation plan selects configured successors.

No caller flag, table-existence probe, or arbitrary later migration enables this
branch. Historical unplanned fixture runs preserve their exact old semantics.
"""

from sqlalchemy import select, text

from backend.app.factor_analysis_models import FactorAnalysisPlan
from backend.app.research_lifecycle import require_neutral_transaction
from backend.app.services import DomainError


def configured_analysis_run(session, fence):
    require_neutral_transaction(session, fence, resource_scope="factor_objects")
    revisions = session.scalars(text("SELECT version_num FROM alembic_version")).all()
    if len(revisions) != 1:
        raise DomainError(
            "factor_analysis_schema", "Exact installed schema required", 409
        )
    if revisions[0] not in {
        "022_factor_analysis",
        "023_rd_submissions",
        "024_openevolve_initial",
        "025_structured_factor_strategy",
        "026_rd_seeded_submissions",
        "027_rd_controller_v3",
    }:
        return False
    return (
        session.scalar(
            select(FactorAnalysisPlan.id).filter_by(
                owner_id=fence.owner_id,
                run_id=fence.run_id,
            )
        )
        is not None
    )
