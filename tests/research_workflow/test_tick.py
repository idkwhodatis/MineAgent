"""Bounded driver orchestration doubles, not worker/PG acceptance."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from workers.research import workflow_tick as tick


def test_actual_normalize_checkpoint_then_new_child_in_order(monkeypatch):
    owner, workflow, stage, selection = uuid4(), uuid4(), uuid4(), uuid4()
    reads = iter(
        [
            (1, "ready", None, selection, stage, "external_normalization"),
            (2, "ready", None, selection, stage, "external_normalization"),
            (
                3,
                "blocked",
                "workflow_child_adapter_unavailable",
                selection,
                stage,
                "external_normalization",
            ),
        ]
    )
    events = []
    monkeypatch.setattr(tick, "_read", lambda *a: next(reads))
    monkeypatch.setattr(
        tick,
        "run_external_stage",
        lambda *a, **k: events.append(("normalize", k["stage_id"])),
    )
    monkeypatch.setattr(
        tick,
        "checkpoint_external_stage",
        lambda *a, **k: events.append(("checkpoint", k["expected_revision"])),
    )
    monkeypatch.setattr(
        tick,
        "dispatch_initial_rd_stage",
        lambda *a, **k: events.append(("claim", k["command"])),
    )
    result = tick.drive_one(None, None, owner_id=owner, workflow_id=workflow)
    assert result.state == "child_queued"
    assert [e[0] for e in events] == ["normalize", "checkpoint", "claim"]
    assert events[1][1] == 2
    assert (
        events[2][1].expected_revision == 3
        and events[2][1].input_revision_id == selection
    )


@pytest.mark.parametrize(
    "status,kind,expected",
    [
        ("paused", "external_normalization", "paused"),
        ("cancelled", "external_normalization", "control_pending_or_terminal"),
        ("running", "rd_factor_generation", "child_owned_by_engine_worker"),
        ("succeeded", "rd_factor_generation", "child_owned_by_engine_worker"),
    ],
)
def test_tick_never_steals_engine_or_resumes_pause(monkeypatch, status, kind, expected):
    workflow = uuid4()
    monkeypatch.setattr(
        tick, "_read", lambda *a: (2, status, None, uuid4(), uuid4(), kind)
    )
    from backend.app.services import DomainError

    def pending(*a, **k):
        raise DomainError("workflow_waiting_child", "Owned by existing worker", 409)

    monkeypatch.setattr(tick, "checkpoint_rd_stage", pending)
    monkeypatch.setattr(tick, "_record_child_terminal_diagnostic", lambda *a: None)
    monkeypatch.setattr(
        tick,
        "run_external_stage",
        lambda *a, **k: pytest.fail("unexpected normalization"),
    )
    monkeypatch.setattr(
        tick,
        "dispatch_initial_rd_stage",
        lambda *a, **k: pytest.fail("unexpected child"),
    )
    assert (
        tick.drive_one(None, None, owner_id=uuid4(), workflow_id=workflow).state
        == expected
    )


def test_same_observed_claim_has_stable_idempotency_key(monkeypatch):
    workflow, selection = uuid4(), uuid4()
    monkeypatch.setattr(
        tick,
        "_read",
        lambda *a: (
            2,
            "blocked",
            "workflow_child_adapter_unavailable",
            selection,
            None,
            None,
        ),
    )
    calls = []
    monkeypatch.setattr(
        tick, "dispatch_initial_rd_stage", lambda *a, **k: calls.append(k["command"])
    )
    for _ in range(2):
        tick.drive_one(None, None, owner_id=uuid4(), workflow_id=workflow)
    assert calls[0] == calls[1]


@pytest.mark.parametrize("limit", [0, 5, True])
def test_query_bound_cannot_be_expanded(limit):
    with pytest.raises(ValueError):
        tick.tick_workflows(None, None, limit=limit)


def test_scan_is_bounded_excludes_unfinished_started_and_engine_stages(monkeypatch):
    from contextlib import contextmanager

    from sqlalchemy.dialects import postgresql

    captured = []

    @contextmanager
    def lifecycle(*a):
        yield

    @contextmanager
    def opened():
        yield NS(execute=lambda statement: captured.append(statement) or ())

    factory = NS(kw={"bind": object()})

    class Factory:
        def __init__(self):
            self.kw = factory.kw

        def __call__(self):
            return opened()

    monkeypatch.setattr(tick, "shared_lifecycle", lifecycle)
    monkeypatch.setattr(tick, "require_schema", lambda s: None)
    assert tick.tick_workflows(Factory(), NS(storage_root="root")) == ()
    sql = str(
        captured[0].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "LIMIT 4" in sql and "external_normalization" in sql
    assert "NOT (EXISTS" in sql and "started" in sql and "cancelled" in sql


@pytest.mark.parametrize(
    "result_status,reason,expected",
    [
        ("completed", None, "completed"),
        ("paused", None, "paused"),
        ("blocked", "workflow_feedback_pending", "feedback_allocated"),
    ],
)
def test_actual_child_checkpoint_precedes_feedback_or_terminal(
    monkeypatch, result_status, reason, expected
):
    owner, workflow, stage, selection = (uuid4() for _ in range(4))
    reads = iter(
        [
            (4, "running", None, selection, stage, "rd_factor_generation"),
            (5, result_status, reason, selection, stage, "rd_factor_generation"),
        ]
    )
    calls = []
    monkeypatch.setattr(tick, "_read", lambda *a: next(reads))
    monkeypatch.setattr(
        tick, "checkpoint_rd_stage", lambda *a, **k: calls.append("actual_boundary")
    )
    monkeypatch.setattr(
        tick,
        "dispatch_feedback_stage",
        lambda *a, **k: calls.append(("allocate", k["expected_revision"])),
    )
    assert (
        tick.drive_one(None, None, owner_id=owner, workflow_id=workflow).state
        == expected
    )
    assert calls == (
        ["actual_boundary", ("allocate", 5)]
        if expected == "feedback_allocated"
        else ["actual_boundary"]
    )


def test_missing_sdk_stop_has_distinct_waiting_not_release(monkeypatch):
    from backend.app.services import DomainError

    owner, workflow, stage, selection = (uuid4() for _ in range(4))
    monkeypatch.setattr(
        tick,
        "_read",
        lambda *a: (4, "running", None, selection, stage, "rd_factor_generation"),
    )

    def unresolved(*a, **k):
        raise DomainError("workflow_waiting_containment", "Missing SDK stop", 409)

    monkeypatch.setattr(tick, "checkpoint_rd_stage", unresolved)
    calls = []
    monkeypatch.setattr(
        tick, "_record_containment_wait", lambda *a: calls.append(a[-2:])
    )
    monkeypatch.setattr(
        tick,
        "dispatch_feedback_stage",
        lambda *a, **k: pytest.fail("No feedback before containment"),
    )
    assert (
        tick.drive_one(None, None, owner_id=owner, workflow_id=workflow).state
        == "workflow_waiting_containment"
    )
    assert calls == [(stage, 4)]


@pytest.mark.parametrize("state", ["success", "missing_release", "cancelled"])
def test_actual_feedback_then_checkpoint_never_invents_next_child(monkeypatch, state):
    from backend.app.services import DomainError

    owner, workflow, stage, selection = uuid4(), uuid4(), uuid4(), uuid4()
    reads = iter(
        [
            (
                5,
                "cancel_requested" if state == "cancelled" else "ready",
                None,
                selection,
                stage,
                "experiment_feedback_projection",
            ),
            (
                5,
                "cancel_requested" if state == "cancelled" else "ready",
                None,
                selection,
                stage,
                "experiment_feedback_projection",
            ),
            (
                6,
                "cancelled" if state == "cancelled" else "blocked",
                "workflow_next_child_pending",
                selection,
                stage,
                "experiment_feedback_projection",
            ),
        ]
    )
    events = []
    monkeypatch.setattr(tick, "_read", lambda *a: next(reads))

    def derive(*a, **kw):
        events.append("derive")
        if state == "missing_release":
            raise DomainError(
                "workflow_feedback_release_required", "Existing release required", 409
            )

    monkeypatch.setattr(tick, "run_feedback_stage", derive)
    monkeypatch.setattr(
        tick, "checkpoint_feedback_stage", lambda *a, **kw: events.append("checkpoint")
    )
    monkeypatch.setattr(tick, "_record_wait", lambda *a: events.append("waiting"))
    monkeypatch.setattr(
        tick,
        "dispatch_initial_rd_stage",
        lambda *a, **kw: pytest.fail("Not an initial child"),
    )
    monkeypatch.setattr(
        tick, "dispatch_next_rd_stage", lambda *a, **kw: events.append("next_child")
    )
    result = tick.drive_one(None, None, owner_id=owner, workflow_id=workflow)
    assert (
        result.state
        == {
            "success": "child_queued",
            "missing_release": "waiting_release",
            "cancelled": "control_pending_or_terminal",
        }[state]
    )
    assert events == (
        ["derive", "waiting"]
        if state == "missing_release"
        else (
            ["derive", "checkpoint", "next_child"]
            if state == "success"
            else ["derive", "checkpoint"]
        )
    )


@pytest.mark.parametrize(
    "new_status,wait_status,wait_reason",
    [
        ("ready", "ready", "workflow_feedback_release_required"),
        ("cancel_requested", "ready", "workflow_feedback_release_required"),
        ("ready", "cancel_requested", "workflow_child_cancelled_unverified"),
    ],
)
def test_four_waiting_roots_cannot_starve_new_actionable_root(
    monkeypatch, new_status, wait_status, wait_reason
):
    """Execute the actual bounded SELECT on SQLite, not a PG/authority proof."""
    from contextlib import nullcontext

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite://")
    owner = uuid4()
    waits = [uuid4() for _ in range(4)]
    actionable = uuid4()
    # Only query columns: no simulated publication or process authority.
    with engine.begin() as connection:
        for ddl in (
            "CREATE TABLE research_workflows (owner_id TEXT, id TEXT, current_stage_id TEXT, status TEXT, blocked_reason TEXT, created_at INTEGER)",
            "CREATE TABLE research_workflow_stages (id TEXT, kind TEXT, child_run_id TEXT)",
            "CREATE TABLE research_input_derivations (id TEXT, stage_id TEXT)",
            "CREATE TABLE research_input_derivation_events (id TEXT, derivation_id TEXT, state TEXT)",
            "CREATE TABLE research_runs (id TEXT, owner_id TEXT, status TEXT)",
        ):
            connection.exec_driver_sql(ddl)
        for ordinal, workflow_id in enumerate([*waits, actionable]):
            stage_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO research_workflow_stages VALUES (:id, 'experiment_feedback_projection', NULL)"
                ),
                {"id": stage_id.hex},
            )
            connection.execute(
                text(
                    "INSERT INTO research_workflows VALUES (:owner,:id,:stage,:status,:reason,:created)"
                ),
                {
                    "owner": owner.hex,
                    "id": workflow_id.hex,
                    "stage": stage_id.hex,
                    "status": wait_status if ordinal < 4 else new_status,
                    "reason": (
                        wait_reason
                        if ordinal < 4
                        else (
                            "workflow_feedback_release_required"
                            if new_status == "cancel_requested"
                            else None
                        )
                    ),
                    "created": ordinal,
                },
            )
    factory = sessionmaker(engine)
    monkeypatch.setattr(tick, "shared_lifecycle", lambda *a: nullcontext())
    monkeypatch.setattr(tick, "require_schema", lambda session: None)
    visited = []

    def observe(factory, settings, *, owner_id, workflow_id):
        assert owner_id == owner
        visited.append(workflow_id)
        return tick.TickOutcome(workflow_id, "not_executed")

    monkeypatch.setattr(tick, "drive_one", observe)
    try:
        result = tick.tick_workflows(factory, NS(storage_root="unused"))
        assert len(result) == 4
        assert visited == [actionable, *waits[:3]]
        # Waiting roots remain candidates; nothing creates release/clears waits.
        with engine.connect() as connection:
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM research_workflows WHERE blocked_reason=:reason"
                ),
                {"reason": wait_reason},
            ) == (5 if new_status == "cancel_requested" else 4)
    finally:
        engine.dispose()


@pytest.mark.parametrize("scenario", ["new", "identical", "stale", "cancel"])
def test_wait_diagnostic_uses_root_cas_without_repeated_mutation(monkeypatch, scenario):
    """Transaction double checks host mutation; actual root SQL is separately probed."""
    from contextlib import contextmanager, nullcontext

    from backend.app import research_lifecycle

    owner, workflow, stage = uuid4(), uuid4(), uuid4()
    reason = "workflow_feedback_release_required"
    root = NS(
        current_stage_id=stage,
        revision=7,
        status="cancel_requested" if scenario == "cancel" else "running",
        blocked_reason=reason if scenario == "identical" else None,
    )
    original_status = root.status
    flushes = []
    locks = []

    def flush():
        assert root.revision == 8 and root.blocked_reason == reason
        assert root.status == original_status
        flushes.append(root.revision)

    @contextmanager
    def begin():
        yield NS(scalar=lambda statement: root, flush=flush)

    factory = NS(kw={"bind": object()}, begin=begin)
    monkeypatch.setattr(tick, "shared_lifecycle", lambda *a: nullcontext())
    monkeypatch.setattr(tick, "require_schema", lambda session: None)
    monkeypatch.setattr(
        research_lifecycle, "_try_neutral_lock", lambda session, key: locks.append(key)
    )
    tick._record_wait(
        factory,
        NS(storage_root="root"),
        owner,
        workflow,
        stage,
        6 if scenario == "stale" else 7,
        reason,
    )
    assert locks == [f"submit:{owner}"]
    assert flushes == ([8] if scenario in {"new", "cancel"} else [])
    assert root.revision == (8 if flushes else 7)
    assert root.status == original_status
    if scenario == "stale":
        assert root.blocked_reason is None


@pytest.mark.parametrize(
    "scenario",
    [
        "failed",
        "cancelled",
        "stale",
        "new_child",
        "repeated",
        "cancel_control",
        "active",
        "foreign",
        "receipt_swap",
    ],
)
def test_child_terminal_metadata_blocks_without_fabricated_checkpoint(
    monkeypatch, scenario
):
    from contextlib import contextmanager, nullcontext

    from backend.app import rdagent_workflow_saved, research_lifecycle
    from backend.app.services import DomainError

    owner, workflow, stage_id, child = (uuid4() for _ in range(4))
    reason = "workflow_child_failed_unverified"
    root = NS(
        owner_id=owner,
        current_stage_id=stage_id,
        revision=7,
        status="running",
        blocked_reason=None,
        latest_checkpoint_id=None,
    )
    stage = NS(
        owner_id=owner,
        workflow_id=workflow,
        id=stage_id,
        kind="rd_factor_generation",
        child_run_id=child,
    )
    run = NS(
        owner_id=owner,
        id=child,
        engine="rdagent",
        purpose="factor_research",
        status="failed",
    )
    receipt = NS(
        id=uuid4(),
        owner_id=owner,
        workflow_id=workflow,
        stage_id=stage_id,
        run_id=child,
    )
    if scenario == "cancelled":
        run.status = "cancelled"
    elif scenario == "stale":
        root.revision = 8
    elif scenario == "new_child":
        root.current_stage_id = uuid4()
    elif scenario == "repeated":
        root.status, root.blocked_reason = "blocked", reason
    elif scenario == "cancel_control":
        root.status = "cancel_requested"
    elif scenario == "active":
        run.status = "running"
    elif scenario == "foreign":
        run.owner_id = uuid4()
    elif scenario == "receipt_swap":
        receipt.run_id = uuid4()
    rows = iter([root, stage, run, receipt])
    flushes = []

    @contextmanager
    def begin():
        yield NS(
            scalar=lambda query: next(rows), flush=lambda: flushes.append(root.revision)
        )

    factory = NS(kw={"bind": object()}, begin=begin)
    monkeypatch.setattr(tick, "shared_lifecycle", lambda *a: nullcontext())
    monkeypatch.setattr(tick, "require_schema", lambda session: None)
    monkeypatch.setattr(research_lifecycle, "_try_neutral_lock", lambda *a: None)
    monkeypatch.setattr(
        rdagent_workflow_saved,
        "verify_workflow_submission_metadata",
        lambda *a: receipt,
    )
    if scenario in {"foreign", "receipt_swap"}:
        with pytest.raises(DomainError, match="differs"):
            tick._record_child_terminal_diagnostic(
                factory, NS(storage_root="root"), owner, workflow, stage_id, 7
            )
        assert root.revision == 7 and root.status == "running" and not flushes
    else:
        actual = tick._record_child_terminal_diagnostic(
            factory, NS(storage_root="root"), owner, workflow, stage_id, 7
        )
        if scenario in {"stale", "new_child", "active"}:
            assert actual is None and not flushes and root.status == "running"
        else:
            assert actual == f"workflow_child_{run.status}_unverified"
            assert root.blocked_reason == actual
            assert root.status == (
                "cancel_requested" if scenario == "cancel_control" else "blocked"
            )
            assert flushes == ([] if scenario == "repeated" else [8])
    assert root.latest_checkpoint_id is None
    assert stage.child_run_id == child
