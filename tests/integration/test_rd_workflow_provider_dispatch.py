"""Actual installed028 source/provider predicate matrix, never a fixture grant."""

import json

import pytest
from sqlalchemy import text

from packages.rdagent_adapter.contracts import FactorResearchConfig
from runtimes.rdagent.probe_fixture import research_dict
from tests.rdagent.test_rdagent_workflow_child_contracts import config
from tests.research_workflow.test_plan import plan

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize(
    "case,expected,provider_expected",
    [
        ("legacy", True, False),
        ("legacy_missing_defaults", False, False),
        ("provider", True, True),
        ("workflow", True, False),
        ("workflow_wrong_profile", False, False),
        ("workflow_wrong_hash", False, False),
        ("workflow_missing_child", False, False),
        ("workflow_foreign_extra", False, False),
        ("future", False, False),
    ],
)
def test_installed_workflow_source_dispatch_preserves_exact_old_provider(
    migrated_engine, case, expected, provider_expected
):
    value = config().model_dump(mode="json")
    if case == "legacy":
        value = FactorResearchConfig.model_validate(research_dict()).model_dump(
            mode="json"
        )
    elif case == "legacy_missing_defaults":
        value = research_dict()
    elif case == "provider":
        value = plan().research_template.model_dump(mode="json")
    elif case == "workflow_wrong_profile":
        value["model_profile"] = "configured.rdagent.v1"
    elif case == "workflow_wrong_hash":
        value["workflow_inputs_sha256"] = "f" * 64
    elif case == "workflow_missing_child":
        del value["workflow_child_run_id"]
    elif case == "workflow_foreign_extra":
        value["approved"] = True
    elif case == "future":
        value["schema_version"] = "mineagent.factor-research.v4"
    with migrated_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT mineagent_rd_source_config(CAST(:value AS jsonb)), "
                "mineagent_rd_provider_config(CAST(:value AS jsonb))"
            ),
            {"value": json.dumps(value)},
        ).one()
    assert tuple(row) == (expected, provider_expected)
