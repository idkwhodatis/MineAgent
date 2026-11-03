"""Offline orchestration/row doubles, not a PostgreSQL or controller acceptance gate."""

import ast
from pathlib import Path
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import research_workflow_feedback_stage as allocation
from backend.app import research_workflow_rd_checkpoint as checkpoint
from packages.factors.contracts import canonical_bytes, digest
from packages.research_workflow.plan import RDGuidancePlan
from tests.research_workflow.test_rd_checkpoint import boundary  # noqa: F401
from tests.research_workflow.test_rd_terminal import completed_graph  # noqa: F401
from workers.research import workflow_tick as tick


def test_feedback_allocated_finishes_ready_without_running_feedback(
    boundary,  # noqa: F811
    monkeypatch,
):
    b = boundary
    plan = RDGuidancePlan.model_validate_json(b.root.plan_canonical)
    b.root.blocked_reason = None
    b.root.current_input_revision_id = uuid4()
    b.stage.child_run_id = b.actual.child_run_id
    b.stage.allocation_canonical = canonical_bytes(
        plan.research_template.budget
    ).decode()
    b.stage.allocation_sha256 = digest(plan.research_template.budget)
    facts = b.actual.model_copy(
        update={
            "owner_id": b.root.owner_id,
            "definition_sha256": b.stage.definition_sha256,
        }
    )
    monkeypatch.setattr(checkpoint, "read_rd_terminal_in_session", lambda *a: facts)
    calls = []
    allocated = []

    def read(*args):
        assert b.root.current_stage_id == b.stage.id
        return (
            b.root.revision,
            b.root.status,
            b.root.blocked_reason,
            b.root.current_input_revision_id,
            b.stage.id,
            b.stage.kind,
        )

    def observe(*args, **kwargs):
        calls.append("checkpoint")
        checkpoint.observe_rd_terminal(
            b.session,
            kwargs["owner_id"],
            kwargs["workflow_id"],
            kwargs["stage_id"],
            kwargs["expected_revision"],
            b.t.settings,
        )
        assert b.root.status == "blocked"
        assert b.root.blocked_reason == "workflow_feedback_pending"

    def allocate(*args, **kwargs):
        calls.append("allocate")
        rows = iter((b.root, b.added[0]))
        session = NS(
            scalar=lambda query: next(rows),
            scalars=lambda query: [b.stage],
            add=allocated.append,
            flush=lambda: None,
        )
        allocation.allocate_feedback_stage(
            session,
            kwargs["owner_id"],
            kwargs["workflow_id"],
            kwargs["expected_revision"],
            b.t.settings,
            admission=object(),
        )

    def forbidden(*args, **kwargs):
        pytest.fail("Allocation must not execute feedback or queue another child")

    monkeypatch.setattr(allocation, "require_schema", lambda *a: None)
    monkeypatch.setattr(allocation, "require_publication_admission", lambda *a: None)
    monkeypatch.setattr(allocation, "_try_neutral_lock", lambda *a: None)
    monkeypatch.setattr(tick, "_read", read)
    monkeypatch.setattr(tick, "checkpoint_rd_stage", observe)
    monkeypatch.setattr(tick, "dispatch_feedback_stage", allocate)
    monkeypatch.setattr(tick, "run_feedback_stage", forbidden)
    monkeypatch.setattr(tick, "dispatch_next_rd_stage", forbidden)
    monkeypatch.setattr(tick, "dispatch_initial_rd_stage", forbidden)

    outcome = tick.drive_one(
        None, b.t.settings, owner_id=b.root.owner_id, workflow_id=b.root.id
    )
    assert outcome.state == "feedback_allocated"
    assert calls == ["checkpoint", "allocate"]
    assert b.root.status == "ready" and b.root.blocked_reason is None
    assert b.root.revision == 3
    assert len(b.added) == len(allocated) == 1
    assert b.root.latest_checkpoint_id == b.added[0].id
    assert b.root.current_stage_id == allocated[0].id
    assert allocated[0].kind == "experiment_feedback_projection"
    assert allocated[0].child_run_id is None


def test_integration_expects_ready_at_the_feedback_allocation_boundary():
    source = (
        Path(__file__).resolve().parents[2]
        / "tests/integration/test_rdagent_workflow_user_flow.py"
    )
    tree = ast.parse(source.read_text())
    expectations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert) or not isinstance(node.test, ast.Compare):
            continue
        comparison = node.test
        if (
            ast.unparse(comparison.left) != "root.status"
            or len(comparison.ops) != 1
            or not isinstance(comparison.ops[0], ast.Eq)
            or len(comparison.comparators) != 1
        ):
            continue
        expected = comparison.comparators[0]
        if (
            isinstance(expected, ast.IfExp)
            and ast.unparse(expected.test) == "allow_feedback_release"
        ):
            expectations.append(
                (ast.literal_eval(expected.body), ast.literal_eval(expected.orelse))
            )
    assert expectations == [("paused", "ready")]
