"""Explicit v3 reconstruction is owner and actual child-run bound, not authority."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app.rdagent_snapshot_config import (
    parse_snapshot_config,
    workflow_config_in_session,
)
from tests.rdagent.test_rdagent_workflow_child_contracts import config


@pytest.mark.parametrize("json_mode", [False, True])
def test_v3_requires_explicit_successor_and_exact_actual_child(json_mode):
    value = config()
    raw = value.model_dump(mode="json")
    owner = value.workflow_inputs.binding.owner_id
    kwargs = {
        "owner_id": owner,
        "run_id": value.workflow_child_run_id,
        "json_mode": json_mode,
    }
    for provider in (False, True):
        with pytest.raises(ValueError):
            parse_snapshot_config(raw, **kwargs, provider_config=provider)
    assert parse_snapshot_config(raw, **kwargs, workflow_config=True) == value
    for wrong in ({"owner_id": uuid4()}, {"run_id": uuid4()}, {"run_id": None}):
        with pytest.raises(ValueError, match="owner/run differs"):
            parse_snapshot_config(raw, **{**kwargs, **wrong}, workflow_config=True)


@pytest.mark.parametrize(
    "revision,expected",
    [
        ("026_rd_seeded_submissions", False),
        ("028_oe_structured_search", False),
        ("029_research_workflow", True),
        ("029_future", None),
        ("029_research_workflows", None),
    ],
)
def test_workflow_dispatch_exact_closed_revision(revision, expected):
    session = NS(scalars=lambda query: NS(all=lambda: [revision]))
    if expected is None:
        with pytest.raises(ValueError):
            workflow_config_in_session(session)
    else:
        assert workflow_config_in_session(session) is expected


from test_rdagent_configured_backup import cold  # noqa: F401
from test_rdagent_configured_inputs import case  # noqa: F401
from test_rdagent_configured_source import retained  # noqa: F401
from test_rdagent_configured_source_service import setup_source  # noqa: F401


def test_v3_reconstructs_actual_retained_inputs_only_with_new_child_binding(cold):  # noqa: F811
    from uuid import UUID

    from test_rdagent_provider_snapshot_config import provider

    from backend.app.rdagent_configured_backup import validate_configured_input_closure
    from packages.rdagent_adapter.workflow_config import WorkflowFactorResearchConfig
    from packages.research_workflow.contracts import (
        StageExecutionBinding,
        WorkflowInputSnapshot,
    )
    from packages.research_workflow.model_inputs import ModelStageInputSnapshot

    witnesses, args = cold
    spec = args["tables"]["factor_research_specs"][0]
    owner, run = UUID(spec["owner_id"]), UUID(spec["run_id"])
    base = provider(spec["config"], owner)
    selection = WorkflowInputSnapshot(
        owner_id=owner, workflow_id=uuid4(), revision_id=uuid4()
    )
    binding = StageExecutionBinding(
        owner_id=owner,
        workflow_id=selection.workflow_id,
        stage_id=uuid4(),
        input_revision_id=selection.revision_id,
        input_sha256=selection.sha256,
        definition_sha256="b" * 64,
    )
    inputs = ModelStageInputSnapshot(binding=binding, selection=selection)
    value = WorkflowFactorResearchConfig.model_validate(
        {
            **base.model_dump(mode="python"),
            "schema_version": "mineagent.factor-research.v3",
            "model_profile": "workflow.rdagent.v1",
            "workflow_child_run_id": run,
            "workflow_inputs": inputs,
            "workflow_inputs_sha256": inputs.sha256,
            "workflow_parent": None,
            "max_iterations": 1,
        }
    )
    spec["config"] = value.model_dump(mode="json")
    args["tables"]["factor_analysis_plans"][0]["config_sha256"] = value.sha256
    with pytest.raises(ValueError):
        validate_configured_input_closure(witnesses, **args, provider_config=True)
    validate_configured_input_closure(witnesses, **args, workflow_config=True)
    value = WorkflowFactorResearchConfig.model_validate(
        {**value.model_dump(mode="python"), "workflow_child_run_id": uuid4()}
    )
    spec["config"] = value.model_dump(mode="json")
    args["tables"]["factor_analysis_plans"][0]["config_sha256"] = value.sha256
    with pytest.raises(ValueError, match="owner/run differs"):
        validate_configured_input_closure(witnesses, **args, workflow_config=True)
