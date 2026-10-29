"""New plan does not rewrite old RD-only completion or infer a future snapshot."""

from datetime import timedelta

import pytest
from test_plan import plan

from packages.factors.contracts import canonical_bytes
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_workflow.plan import RDGuidancePlan, stage_schedule
from packages.research_workflow.structured_plan import (
    RDStructuredGuidancePlan,
    guidance_stage_schedule,
    parse_guidance_plan,
)


def combined():
    old = plan()
    budget = BudgetVector(calls=2, process_slots=4, wall_milliseconds=600000)
    raw = old.model_dump(mode="json")
    raw.update(
        schema_version="mineagent.rd-structured-guidance-workflow-plan.v1",
        profile="rd_guidance_then_structured_oe.v1",
        budget_ceiling=old.budget_ceiling.add(budget).model_dump(mode="json"),
    )
    end = old.research_template.input.window.start + timedelta(days=1)
    raw["research_template"]["input"]["window"]["end"] = str(end)
    raw["objective"]["window"]["end"] = str(end)
    raw["structured_template"] = {
        "seed_parameters": {
            "threshold": 0.0,
            "top_k": 1,
            "maximum_weight": 0.5,
            "maximum_gross_weight": 1.0,
        },
        "objective": {
            "window": {
                "start": str(old.research_template.input.window.start),
                "end": str(end + timedelta(days=1)),
            },
            "initial_cash": 10000.0,
        },
        "model_binding": {
            "profile_id": "offline",
            "profile_sha256": "a" * 64,
            "model": "chat",
            "max_output_tokens": 100,
            "request_deadline_ms": 8000,
            "pricing": {
                "status": "operator_declared",
                "input_microusd_per_million": 0,
                "output_microusd_per_million": 0,
            },
        },
        "runtime": {
            k: ("a" * 64 if "sha256" in k else "sha256:" + "a" * 64)
            for k in [
                "controller_image_id",
                "controller_installed_parent_image_id",
                "controller_inventory_sha256",
                "engine_image_id",
                "engine_installed_parent_image_id",
                "engine_dependency_lock_sha256",
            ]
        },
        "budget": budget.model_dump(mode="json"),
        "maximum_mutation_slots": 1,
    }
    return raw


def test_legacy_bytes_and_completion_schedule_unchanged():
    old = plan()
    assert type(parse_guidance_plan(canonical_bytes(old))) is RDGuidancePlan
    assert canonical_bytes(
        parse_guidance_plan(canonical_bytes(old))
    ) == canonical_bytes(old)
    assert guidance_stage_schedule(old) == stage_schedule(old)
    assert guidance_stage_schedule(old)[-1][0] == "rd_factor_generation"


def test_new_version_appends_exactly_one_terminal_structured_stage():
    new = RDStructuredGuidancePlan.model_validate_json(canonical_bytes(combined()))
    assert guidance_stage_schedule(new) == (
        *stage_schedule(new.rd_plan()),
        ("structured_factor_execution", None),
    )
    assert (
        new.structured_template.release_policy == "explicit_snapshot_release_required"
    )
    assert "snapshot_id" not in new.structured_template.model_dump()
    with pytest.raises(ValueError):
        RDGuidancePlan.model_validate_json(canonical_bytes(new))


@pytest.mark.parametrize(
    "mutation", ["budget", "unknown_price", "process", "window", "release", "snapshot"]
)
def test_no_budget_or_release_or_identity_inference(mutation):
    raw = combined()
    template = raw["structured_template"]
    if mutation == "budget":
        raw["budget_ceiling"] = plan().budget_ceiling.model_dump(mode="json")
    elif mutation == "unknown_price":
        template["model_binding"]["pricing"] = {"status": "unavailable"}
    elif mutation == "process":
        template["budget"]["process_slots"] = 3
    elif mutation == "window":
        template["objective"]["window"]["end"] = raw["research_template"]["input"][
            "window"
        ]["end"]
    elif mutation == "release":
        template["release_policy"] = "allow_development_feedback"
    else:
        template["snapshot_id"] = "00000000-0000-0000-0000-000000000001"
    with pytest.raises(ValueError):
        RDStructuredGuidancePlan.model_validate_json(canonical_bytes(raw))


def test_parser_rejects_duplicate_fields_and_unknown_versions():
    raw = canonical_bytes(plan())
    with pytest.raises(ValueError):
        parse_guidance_plan(b'{"owner_id":"bad",' + raw[1:])
    with pytest.raises(ValueError):
        parse_guidance_plan(canonical_bytes({"schema_version": "unknown"}))


@pytest.mark.parametrize("days", [2, 3])
def test_new_terminal_oe_plan_rejects_multi_day_signal_before_admission(days):
    from datetime import date

    raw = combined()
    start = date.fromisoformat(raw["research_template"]["input"]["window"]["start"])
    end = start + timedelta(days=days)
    raw["research_template"]["input"]["window"]["end"] = str(end)
    raw["objective"]["window"]["end"] = str(end)
    raw["structured_template"]["objective"]["window"]["end"] = str(
        end + timedelta(days=1)
    )
    with pytest.raises(ValueError, match="one frozen signal day"):
        RDStructuredGuidancePlan.model_validate_json(canonical_bytes(raw))
