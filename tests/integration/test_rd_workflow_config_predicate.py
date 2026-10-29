"""Installed028 PostgreSQL v3 predicate differential; collect is not execution."""

import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from packages.factors.contracts import digest
from packages.rdagent_adapter.workflow_config import WorkflowFactorResearchConfig
from tests.rdagent.test_rdagent_workflow_child_contracts import config

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "hash",
        "owner",
        "reorder",
        "text",
        "hidden_manual",
        "old_profile",
        "duplicate",
        "control_title",
        "unknown_origin",
        "layout_new",
        "layout_null",
        "layout_false",
        "layout_unknown",
        "layout_number",
        "layout_object",
    ],
)
@pytest.mark.parametrize(
    "controller",
    [
        "missing",
        "mineagent.rdagent-orchestrator.v3",
        None,
        False,
        120,
        "mineagent.rdagent-orchestrator.v2",
        "unknown",
    ],
)
def test_installed_workflow_config_predicate_matches_strict_python(
    migrated_engine, mutation, controller
):
    value = config().model_dump(mode="json")
    value.pop("controller_profile", None)
    value.pop("workflow_prompt_layout", None)
    if controller != "missing":
        value["controller_profile"] = controller
    value["budget"]["wall_milliseconds"] = (
        300000 + 32 * value["model_binding"]["request_deadline_ms"]
    )
    if mutation is not None and mutation.startswith("layout_"):
        value["workflow_prompt_layout"] = {
            "layout_new": "generation_user.v1",
            "layout_null": None,
            "layout_false": False,
            "layout_unknown": "generation_user.v2",
            "layout_number": 1,
            "layout_object": {},
        }[mutation]
    elif mutation == "hash":
        value["workflow_inputs_sha256"] = "f" * 64
    elif mutation == "owner":
        value["workflow_inputs"]["binding"]["owner_id"] = str(uuid4())
        value["workflow_inputs_sha256"] = digest(value["workflow_inputs"])
    elif mutation == "reorder":
        value["workflow_inputs"]["inputs"].reverse()
        value["workflow_inputs_sha256"] = digest(value["workflow_inputs"])
    elif mutation == "text":
        value["workflow_inputs"]["inputs"][0]["text"] += "changed"
        value["workflow_inputs_sha256"] = digest(value["workflow_inputs"])
    elif mutation == "hidden_manual":
        value["steer_snapshot"]["owner_id"] = str(uuid4())
        value["steer_snapshot_sha256"] = digest(value["steer_snapshot"])
    elif mutation in ("duplicate", "control_title", "unknown_origin"):
        inputs = value["workflow_inputs"]
        if mutation == "duplicate":
            inputs["inputs"].append(inputs["inputs"][0].copy())
            inputs["selection"]["refs"].append(inputs["inputs"][0]["ref"].copy())
        elif mutation == "control_title":
            inputs["inputs"][0]["title"] = "bad\nheading"
        else:
            inputs["inputs"][0]["ref"]["origin"] = "unknown"
            inputs["selection"]["refs"][0]["origin"] = "unknown"
        inputs["binding"]["input_sha256"] = digest(inputs["selection"])
        value["workflow_inputs_sha256"] = digest(inputs)
    elif mutation == "old_profile":
        value["schema_version"] = "mineagent.factor-research.v2"
    try:
        WorkflowFactorResearchConfig.model_validate_json(json.dumps(value))
        python_ok = True
    except ValueError:
        python_ok = False
    assert python_ok is (
        mutation in (None, "layout_new")
        and controller in ("missing", "mineagent.rdagent-orchestrator.v3")
    )
    with migrated_engine.connect() as connection:
        sql_ok = connection.scalar(
            text("SELECT mineagent_rd_workflow_config(CAST(:value AS jsonb))"),
            {"value": json.dumps(value)},
        )
    assert sql_ok is python_ok


@pytest.mark.parametrize("layout", [None, "generation_user.v1"])
@pytest.mark.parametrize("offset", [-1, 0])
def test_workflow_explicit_composed_floor_is_preserved(migrated_engine, layout, offset):
    value = config().model_dump(mode="json")
    value["controller_profile"] = "mineagent.rdagent-orchestrator.v3"
    value.pop("workflow_prompt_layout", None)
    if layout is not None:
        value["workflow_prompt_layout"] = layout
    value["budget"]["wall_milliseconds"] = (
        300000 + 32 * value["model_binding"]["request_deadline_ms"] + offset
    )
    with migrated_engine.connect() as connection:
        actual = connection.scalar(
            text("SELECT mineagent_rd_workflow_config(CAST(:value AS jsonb))"),
            {"value": json.dumps(value)},
        )
    assert actual is (offset >= 0)
