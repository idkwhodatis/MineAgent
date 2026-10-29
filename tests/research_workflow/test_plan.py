"""Pure finite-plan declaration tests; no child admission or process execution."""

from uuid import UUID

import pytest

from packages.factors.contracts import canonical_bytes
from packages.rdagent_adapter.provider_config import ProviderFactorResearchConfig
from packages.rdagent_adapter.steer import SteerSnapshot
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_workflow.plan import RDGuidancePlan, stage_schedule
from runtimes.rdagent.probe_fixture import research_dict


def plan():
    owner = UUID(int=3)
    empty = SteerSnapshot(owner_id=owner)
    raw = research_dict()
    raw.update(
        schema_version="mineagent.factor-research.v2",
        max_iterations=1,
        model_profile="configured.rdagent.v1",
        steer_snapshot=empty.model_dump(mode="json"),
        steer_snapshot_sha256=empty.sha256,
        model_binding={
            "profile_id": "offline",
            "profile_sha256": "a" * 64,
            "chat_model": "chat",
            "embedding_model": "embed",
            "embedding_dimensions": 2,
            "max_output_tokens": 100,
            "request_deadline_ms": 8000,
            "pricing": {
                "status": "operator_declared",
                "chat_input_microusd_per_million": 1,
                "chat_output_microusd_per_million": 2,
                "embedding_microusd_per_million": 3,
            },
        },
    )
    template = ProviderFactorResearchConfig.model_validate(raw)
    cap = BudgetVector(
        **{key: getattr(template.budget, key) * 2 for key in BudgetVector.model_fields}
    )
    return RDGuidancePlan(
        owner_id=owner,
        maximum_iterations=2,
        research_template=template,
        objective={
            "window": template.input.window,
            "label_horizon_sessions": 1,
            "metric": "rank_ic",
            "direction": "at_least",
            "threshold": 0.0,
            "minimum_defined_days": 1,
            "minimum_pairs": 2,
        },
        source_policy={"acknowledged_unverified": True},
        qlib_installed_parent_image_id="sha256:" + "b" * 64,
        analysis_dependency_lock_sha256="c" * 64,
        initial_parent_factor_version_id=None,
        budget_ceiling=cap.add(BudgetVector(storage_bytes=65536)),
    )


def test_default_continuous_schedule_has_real_feedback_between_new_rd_children():
    value = plan()
    assert value.mode == "continuous"
    assert stage_schedule(value) == (
        ("rd_factor_generation", 0),
        ("experiment_feedback_projection", 0),
        ("rd_factor_generation", 1),
    )
    assert RDGuidancePlan.model_validate_json(canonical_bytes(value)) == value


def test_normalization_is_not_fabricated_research_iteration():
    value = RDGuidancePlan.model_validate(
        {**plan().model_dump(), "normalize_external_first": True}
    )
    assert stage_schedule(value)[0] == ("external_normalization", None)
    assert len(stage_schedule(value)) == 4


@pytest.mark.parametrize(
    "mutation", ["iterations", "nested_iterations", "owner", "unknown_price", "budget"]
)
def test_plan_rejects_undeclared_or_unfunded_scope(mutation):
    raw = plan().model_dump()
    if mutation == "iterations":
        raw["maximum_iterations"] = 9
    elif mutation == "nested_iterations":
        raw["research_template"]["max_iterations"] = 2
    elif mutation == "owner":
        raw["owner_id"] = UUID(int=4)
    elif mutation == "unknown_price":
        raw["research_template"]["model_binding"]["pricing"] = {"status": "unavailable"}
    else:
        raw["budget_ceiling"]["calls"] = 0
    with pytest.raises(ValueError):
        RDGuidancePlan.model_validate(raw)


def test_feedback_release_defaults_off_and_is_immutable_plan_commitment():
    original = plan()
    assert original.allow_workflow_development_feedback_release is False
    enabled = original.model_copy(
        update={"allow_workflow_development_feedback_release": True}
    )
    assert enabled.sha256 != original.sha256
    assert (
        enabled.feedback_release_scope == "own_bound_workflow_development_outputs_only"
    )
    assert original.allow_workflow_development_feedback_release is False


@pytest.mark.parametrize("value", [1, "true", None])
def test_feedback_opt_in_is_explicit_strict_boolean(value):
    raw = plan().model_dump(mode="json")
    raw["allow_workflow_development_feedback_release"] = value
    with pytest.raises(ValueError):
        RDGuidancePlan.model_validate_json(__import__("json").dumps(raw))
