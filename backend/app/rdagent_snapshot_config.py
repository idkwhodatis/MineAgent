"""Explicit successor config dispatch for inherited snapshot/analysis readers.

The flag is an internal validator argument, never serialized authority. Cold
callers select provider/workflow only for their checked research/RD tuples; live
historical callers derive it from the exact installed revision. Defaults retain
old v1 parsing. No unknown future revision or configuration is accepted.
"""

from sqlalchemy import text

from packages.factors.contracts import canonical_bytes
from packages.rdagent_adapter.contracts import FactorResearchConfig
from packages.rdagent_adapter.provider_config import parse_research_config


def parse_snapshot_config(
    value,
    *,
    owner_id,
    run_id=None,
    provider_config=False,
    workflow_config=False,
    json_mode=False,
    composed_controller=False,
    seeded_controller=False,
):
    if any(
        type(flag) is not bool
        for flag in (
            provider_config,
            workflow_config,
            json_mode,
            composed_controller,
            seeded_controller,
        )
    ):
        raise ValueError("Explicit snapshot config dispatch required")
    schema = (
        value.get("schema_version")
        if type(value) is dict
        else getattr(value, "schema_version", None)
    )
    if workflow_config and schema == "mineagent.factor-research.v3":
        from packages.rdagent_adapter.workflow_config import (
            WorkflowFactorResearchConfig,
        )

        # JSON round-trip validates retained wire types; no owner/current-head
        # lookup or reinterpretation of an old config is performed here.
        config = WorkflowFactorResearchConfig.model_validate_json(
            canonical_bytes(value)
        )
        if (
            run_id is None
            or str(config.workflow_child_run_id) != str(run_id)
            or str(config.workflow_inputs.binding.owner_id) != str(owner_id)
        ):
            raise ValueError("Workflow snapshot config owner/run differs")
        if config.controller_profile is not None and not composed_controller:
            raise ValueError("Composed controller is unavailable in historical schema")
        return config
    if workflow_config:
        provider_config = True  # Explicit v13/v8 retains exact older config grammars.
    if not provider_config:
        config = (
            FactorResearchConfig.model_validate_json(canonical_bytes(value))
            if json_mode
            else FactorResearchConfig.model_validate(value)
        )
    else:
        config = parse_research_config(value)
    if (
        config.controller_profile == "mineagent.rdagent-orchestrator.v4"
        and not seeded_controller
    ):
        raise ValueError("Seeded controller is unavailable in historical schema")
    if config.controller_profile is not None and not composed_controller:
        raise ValueError("Composed controller is unavailable in historical schema")
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
        "027_rd_controller_v3",
        "027a_contract_repairs",
        "027b_provider_material",
        "027c_seeded_controller_v4",
        "028_oe_structured_search",
        "029_research_workflow",
        "030_declared_upload_basket",
        "031_workflow_structured_stage",
        "032_normal_oe_note_pins",
        "032a_rd_terminal_results",
    }:
        raise ValueError("Unsupported historical snapshot revision")
    return revisions[0] in {
        "026_rd_seeded_submissions",
        "027_rd_controller_v3",
        "027a_contract_repairs",
        "027b_provider_material",
        "027c_seeded_controller_v4",
        "028_oe_structured_search",
        "029_research_workflow",
        "030_declared_upload_basket",
        "031_workflow_structured_stage",
        "032_normal_oe_note_pins",
        "032a_rd_terminal_results",
    }


def workflow_config_in_session(session):
    """Only the explicit installed successor may reconstruct v3 child inputs."""
    revisions = session.scalars(text("SELECT version_num FROM alembic_version")).all()
    if len(revisions) != 1 or revisions[0] not in {
        "021_execution_snapshots",
        "022_factor_analysis",
        "023_rd_submissions",
        "024_openevolve_initial",
        "025_structured_factor_strategy",
        "026_rd_seeded_submissions",
        "027_rd_controller_v3",
        "027a_contract_repairs",
        "027b_provider_material",
        "027c_seeded_controller_v4",
        "028_oe_structured_search",
        "029_research_workflow",
        "030_declared_upload_basket",
        "031_workflow_structured_stage",
        "032_normal_oe_note_pins",
        "032a_rd_terminal_results",
    }:
        raise ValueError("Unsupported workflow snapshot revision")
    return revisions in (
        ["029_research_workflow"],
        ["030_declared_upload_basket"],
        ["031_workflow_structured_stage"],
        ["032_normal_oe_note_pins"],
        ["032a_rd_terminal_results"],
    )


def composed_controller_in_session(session):
    provider_config_in_session(session)
    return session.scalars(text("SELECT version_num FROM alembic_version")).all() in (
        ["027_rd_controller_v3"],
        ["027a_contract_repairs"],
        ["027b_provider_material"],
        ["027c_seeded_controller_v4"],
        ["028_oe_structured_search"],
        ["029_research_workflow"],
        ["030_declared_upload_basket"],
        ["031_workflow_structured_stage"],
        ["032_normal_oe_note_pins"],
        ["032a_rd_terminal_results"],
    )


def declared_upload_in_session(session):
    return session.scalars(text("SELECT version_num FROM alembic_version")).all() in (
        ["030_declared_upload_basket"],
        ["031_workflow_structured_stage"],
        ["032_normal_oe_note_pins"],
        ["032a_rd_terminal_results"],
    )


def seeded_controller_in_session(session):
    provider_config_in_session(session)
    return session.scalars(text("SELECT version_num FROM alembic_version")).all() in (
        ["027c_seeded_controller_v4"],
        ["028_oe_structured_search"],
        ["029_research_workflow"],
        ["030_declared_upload_basket"],
        ["031_workflow_structured_stage"],
        ["032_normal_oe_note_pins"],
        ["032a_rd_terminal_results"],
    )
