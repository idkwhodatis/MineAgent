"""Pure cold graph negative cases, not persisted actor or PostgreSQL evidence."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from backend.app.rdagent_controller_cold_links import validate_controller_links
from packages.factors.contracts import digest


def graph():
    scope = {
        "owner_id": "owner",
        "run_id": "run",
        "attempt_id": "attempt",
        "generation": 1,
    }
    decision = {
        "schema_version": "mineagent.rdagent-adoption-observation.v1",
        "logical_step": 0,
        "evaluated_source_id": "source",
        "previous_adopted_source_id": None,
        "adopted_source_id": "source",
        "continuation_source_id": "source",
        "adopted_new": True,
    }
    idx = {
        "factor_controller_launches": {
            "launch": dict(scope, id="launch", admission_id="admission")
        },
        "factor_source_read_witnesses": {
            "witness": dict(scope, controller_launch_id="launch")
        },
        "factor_generation_starts": {"start": dict(scope, id="start", logical_step=0)},
        "factor_generation_sources": {
            "source": dict(scope, id="source", start_id="start")
        },
        "factor_generation_observations": {
            "observation": dict(
                scope,
                id="observation",
                controller_launch_id="launch",
                start_id="start",
                logical_step=0,
                decision=decision,
                decision_sha256=digest(decision),
            )
        },
        "factor_generation_boundaries": {
            "boundary": dict(
                scope,
                observation_id="observation",
                controller_launch_id="launch",
                start_id="start",
                logical_step=0,
                boundary="returned",
                next_logical_step=None,
            )
        },
        "factor_upstream_calls": {
            "call": dict(
                scope,
                id="call",
                start_id="start",
                kind="embedding",
                phase="knowledge_update",
            )
        },
        "factor_upstream_call_outcomes": {
            "outcome": dict(
                scope,
                call_id="call",
                controller_launch_id="launch",
                status="completed",
                member_count=1,
                member_ids=["member"],
                response_outcome_ids=["response"],
            )
        },
        "factor_generation_model_operations": {
            "member": dict(
                scope,
                id="member",
                call_id="call",
                operation_id="operation",
                request_ordinal=0,
                protocol="mineagent.rdagent-embedding.v1",
            )
        },
    }
    shared = {
        "research_process_admissions": {
            "admission": dict(
                scope, id="admission", profile="mineagent.rdagent-orchestrator.v2"
            )
        },
        "research_process_receipts": {
            "child": dict(scope, admission_id="admission", kind="controller_child")
        },
        "research_model_operations": {
            "operation": dict(scope, id="operation", state="completed")
        },
        "research_model_outcomes": {
            "response": dict(
                scope, id="response", operation_id="operation", outcome="completed"
            )
        },
    }
    return idx, shared, {"run": SimpleNamespace(max_iterations=1)}


def test_complete_initial_boundary():
    validate_controller_links(*graph())


@pytest.mark.parametrize(
    "table,key,field,value",
    [
        ("factor_generation_observations", "observation", "decision_sha256", "0" * 64),
        ("factor_generation_observations", "observation", "logical_step", 1),
        ("factor_generation_boundaries", "boundary", "boundary", "advanced"),
        ("factor_generation_boundaries", "boundary", "next_logical_step", 1),
        ("factor_generation_boundaries", "boundary", "owner_id", "foreign"),
        ("factor_upstream_calls", "call", "phase", "knowledge_query"),
        ("factor_upstream_call_outcomes", "outcome", "status", "aborted"),
        ("factor_upstream_call_outcomes", "outcome", "controller_launch_id", "foreign"),
        ("factor_upstream_call_outcomes", "outcome", "member_ids", []),
        ("factor_upstream_call_outcomes", "outcome", "response_outcome_ids", []),
        ("factor_generation_model_operations", "member", "request_ordinal", 1),
        (
            "factor_generation_model_operations",
            "member",
            "protocol",
            "mineagent.neutral-model.v1",
        ),
    ],
)
def test_mutated_graph(table, key, field, value):
    idx, shared, configs = deepcopy(graph())
    idx[table][key][field] = value
    with pytest.raises(ValueError):
        validate_controller_links(idx, shared, configs)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_child",
        "foreign_child",
        "old_profile",
        "incomplete_model",
        "wrong_response",
        "iterations",
    ],
)
def test_shared_or_profile_drift(mutation):
    idx, shared, configs = graph()
    if mutation == "missing_child":
        shared["research_process_receipts"].clear()
    if mutation == "foreign_child":
        shared["research_process_receipts"]["child"]["run_id"] = "foreign"
    if mutation == "old_profile":
        shared["research_process_admissions"]["admission"]["profile"] = (
            "mineagent.rdagent-orchestrator.v1"
        )
    if mutation == "incomplete_model":
        shared["research_model_operations"]["operation"]["state"] = "submitted"
    if mutation == "wrong_response":
        shared["research_model_outcomes"]["response"]["outcome"] = "failed"
    if mutation == "iterations":
        configs["run"].max_iterations = 2
    with pytest.raises(ValueError):
        validate_controller_links(idx, shared, configs)
