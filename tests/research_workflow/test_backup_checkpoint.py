"""Relational terminal receipt checks; physical predicates remain separate."""

# ruff: noqa: F811
import json

import pytest
from sqlalchemy import select

from backend.app import research_workflow_backup as cold
from backend.app import research_workflow_rd_terminal as live
from backend.app.models import ExecutionAttempt
from backend.app.research_broker_models import (
    ResearchBudgetReservation,
    ResearchBudgetSettlement,
)
from backend.app.research_process_models import (
    ResearchProcessAdmission,
    ResearchProcessReceipt,
)
from packages.rdagent_adapter.workflow_config import WorkflowFactorResearchConfig
from tests.research_workflow.test_rd_terminal import completed_graph  # noqa:F401


def data(row):
    values = (
        {column.name: getattr(row, column.name) for column in row.__table__.columns}
        if hasattr(row, "__table__")
        else vars(row)
    )
    return json.loads(json.dumps(values, default=str))


@pytest.mark.parametrize(
    "change",
    [
        None,
        "status",
        "attempt",
        "source",
        "analysis",
        "stop",
        "extra_unit",
        "unsettled",
        "refund",
        "missing_hold",
        "running_unit",
        "missing_execution",
        "legacy_only",
        "execution_owner",
        "execution_run",
        "execution_attempt",
        "execution_generation",
        "execution_evaluation",
        "execution_start",
        "execution_snapshot",
        "execution_manifest",
        "execution_input",
        "execution_output",
        "execution_controller",
        "execution_boundary",
        "execution_status",
        "boundary_kind",
        "boundary_next",
        "boundary_step",
    ],
)
def test_checkpoint_requires_exact_retained_actual_chain(completed_graph, change):
    value = completed_graph
    cp = live.read_rd_terminal_in_session(
        value.session, value.owner, value.workflow, value.stage, value.settings
    )
    scope = {
        "owner_id": str(value.owner),
        "run_id": str(cp.child_run_id),
        "attempt_id": str(cp.attempt_id),
        "generation": cp.generation,
    }
    rdmodels = [
        live.FactorGenerationStart,
        live.FactorGenerationSource,
        live.FactorEvaluationStart,
        live.FactorGenerationBoundary,
        live.FactorControllerLaunch,
    ]
    names = [
        "factor_generation_starts",
        "factor_generation_sources",
        "factor_evaluation_starts",
        "factor_generation_boundaries",
        "factor_controller_launches",
    ]
    rd = {
        "tables": {
            n: [data(live._one(value.session, m))]
            for n, m in zip(names, rdmodels, strict=True)
        },
        "external_bindings": {"runs": [data(value.run)]},
    }
    shared = {
        "tables": {},
        "external_bindings": {
            "attempts": [
                {
                    "owner_id": str(value.owner),
                    **data(value.session.scalars(select(ExecutionAttempt))[0]),
                }
            ]
        },
    }
    for name, model in (
        ("research_process_admissions", ResearchProcessAdmission),
        ("research_process_receipts", ResearchProcessReceipt),
        ("research_budget_reservations", ResearchBudgetReservation),
        ("research_budget_settlements", ResearchBudgetSettlement),
    ):
        shared["tables"][name] = [data(r) for r in value.session.scalars(select(model))]
    admission_id = rd["tables"]["factor_controller_launches"][0]["admission_id"]
    for row in shared["tables"]["research_process_receipts"]:
        row["admission_id"] = admission_id
    shared["tables"]["research_process_states"] = [
        {**scope, "admission_id": r["id"], "status": "stopped"}
        for r in shared["tables"]["research_process_admissions"]
    ]
    execution = {
        "tables": {
            "factor_execution_snapshot_publications": [
                data(live._one(value.session, live.ExecutionSnapshotPublication))
            ],
            "factor_execution_evaluation_outcomes": [data(value.evaluated)],
        }
    }
    direct = {
        "tables": {
            "factor_direct_analysis_intents": [data(value.analysis)],
            "factor_direct_analysis_outcomes": [data(value.session.get(None, None))],
        }
    }
    factors = {"tables": {"factor_versions": [data(value.version)]}}
    receipt = {
        **data(value.receipt),
        "owner_id": str(value.owner),
        "workflow_id": str(value.workflow),
        "stage_id": str(value.stage),
    }
    metadata = {
        "indexes": {"factor_workflow_submissions": {str(cp.submission_id): receipt}},
        "checkpoints": {"checkpoint": cp},
    }
    if change == "status":
        rd["external_bindings"]["runs"][0]["status"] = "failed"
    elif change == "attempt":
        shared["external_bindings"]["attempts"][0]["sandbox_stopped"] = False
    elif change == "source":
        rd["tables"]["factor_evaluation_starts"][0]["evaluated_source_id"] = "wrong"
    elif change == "analysis":
        direct["tables"]["factor_direct_analysis_outcomes"][0]["result_sha256"] = (
            "0" * 64
        )
    elif change == "stop":
        shared["tables"]["research_process_receipts"].pop()
    elif change == "extra_unit":
        shared["tables"]["research_process_admissions"].append(
            {**scope, "id": "orphan", "role": "native_evaluator"}
        )
    elif change == "unsettled":
        shared["tables"]["research_budget_reservations"][0]["state"] = "uncertain"
    elif change == "refund":
        shared["tables"]["research_budget_settlements"][0]["charge"][
            "process_slots"
        ] = 0
    elif change == "missing_hold":
        shared["tables"]["research_budget_settlements"].clear()
    elif change == "running_unit":
        shared["tables"]["research_process_states"][0]["status"] = "running"
    execution_rows = execution["tables"]["factor_execution_evaluation_outcomes"]
    if change in ("missing_execution", "legacy_only"):
        if change == "legacy_only":
            rd["tables"]["factor_evaluation_outcomes"] = list(execution_rows)
        execution_rows.clear()
    fields = {
        "execution_owner": "owner_id",
        "execution_run": "run_id",
        "execution_attempt": "attempt_id",
        "execution_generation": "generation",
        "execution_evaluation": "evaluation_id",
        "execution_start": "generation_start_id",
        "execution_snapshot": "snapshot_id",
        "execution_manifest": "snapshot_manifest_sha256",
        "execution_input": "snapshot_input_sha256",
        "execution_output": "snapshot_output_sha256",
        "execution_controller": "controller_launch_id",
        "execution_boundary": "boundary_id",
        "execution_status": "status",
    }
    if change in fields:
        execution_rows[0][fields[change]] = (
            2 if change == "execution_generation" else "wrong"
        )
    if change == "boundary_kind":
        rd["tables"]["factor_generation_boundaries"][0]["boundary"] = "advanced"
    elif change == "boundary_next":
        rd["tables"]["factor_generation_boundaries"][0]["next_logical_step"] = 1
    elif change == "boundary_step":
        rd["tables"]["factor_generation_boundaries"][0]["logical_step"] = 1
    args = {
        "configs": {
            str(value.run.id): WorkflowFactorResearchConfig.model_validate_json(
                json.dumps(value.run.config)
            )
        },
        "research_closure": shared,
        "rd_source_closure": rd,
        "execution_closure": execution,
        "direct_analysis_closure": direct,
        "factor_closure": factors,
    }
    if change is None:
        cold.validate_rd_checkpoints(metadata, **args)
    else:
        with pytest.raises(ValueError):
            cold.validate_rd_checkpoints(metadata, **args)
