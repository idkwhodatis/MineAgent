"""Closed source-family boundary for normal OE, before snapshot value reads.

Workflow v3 parsing alone does not prove its child receipt, combined inputs and
parent closure. Normal OE deliberately remains limited to predecessor sources
until that actual qualification path exists; an explicit release is still needed.
"""

from sqlalchemy import select

from backend.app.models import ResearchRun
from backend.app.services import DomainError


def require_source_config(config):
    if not isinstance(config, dict) or config.get("schema_version") not in {
        "mineagent.factor-research.v1",
        "mineagent.factor-research.v2",
    }:
        raise DomainError(
            "structured_source_family_unsupported",
            "Normal structured search requires a qualified predecessor factor source; workflow sources are not supported",
            409,
        )


def require_source_family(session, owner_id, source_run_id):
    run = session.scalar(
        select(ResearchRun).filter_by(id=source_run_id, owner_id=owner_id)
    )
    if run is None or (run.purpose, run.engine) != ("factor_research", "rdagent"):
        raise DomainError(
            "structured_search_input_unavailable", "Factor source unavailable", 409
        )
    require_source_config(run.config)
