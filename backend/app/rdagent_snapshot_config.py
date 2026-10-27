"""Explicit 026 config dispatch for inherited snapshot/analysis readers.

The flag is an internal validator argument, never serialized authority. Cold
callers select it only for the already checked research-v10/RD-v5 tuple; live
historical callers derive it from the exact installed revision. Defaults retain
old v1 parsing. No unknown future revision or configuration is accepted.
"""

from sqlalchemy import text

from packages.factors.contracts import canonical_bytes
from packages.rdagent_adapter.contracts import FactorResearchConfig
from packages.rdagent_adapter.provider_config import parse_research_config


def parse_snapshot_config(value, *, owner_id, provider_config=False, json_mode=False):
    if type(provider_config) is not bool or type(json_mode) is not bool:
        raise ValueError("Explicit snapshot config dispatch required")
    if not provider_config:
        return (
            FactorResearchConfig.model_validate_json(canonical_bytes(value))
            if json_mode
            else FactorResearchConfig.model_validate(value)
        )
    config = parse_research_config(value)
    if config.model_profile == "configured.rdagent.v1" and str(
        config.steer_snapshot.owner_id
    ) != str(owner_id):
        raise ValueError("Provider snapshot config owner differs")
    return config


def provider_config_in_session(session):
    revisions = session.scalars(text("SELECT version_num FROM alembic_version")).all()
    if len(revisions) != 1 or revisions[0] not in {
        "021_execution_snapshots",
        "022_factor_analysis",
        "023_rd_submissions",
        "024_openevolve_initial",
        "025_structured_factor_strategy",
        "026_rd_seeded_submissions",
    }:
        raise ValueError("Unsupported historical snapshot revision")
    return revisions == ["026_rd_seeded_submissions"]
