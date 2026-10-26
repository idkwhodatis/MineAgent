"""Authenticated-event service doubles, never verified execution or PG acceptance."""

from types import SimpleNamespace
from uuid import UUID

import pytest

from backend.app import rdagent_boundary_services as service
from backend.app.rdagent_models import (
    FactorGenerationOutcome,
    FactorGenerationSource,
    FactorGenerationStart,
    FactorUpstreamCall,
    FactorUpstreamCallOutcome,
)
from backend.app.services import DomainError
from packages.rdagent_adapter.host_evidence import ObservedGenerationDecision
from packages.research_runtime.fences import AttemptFence


class Session:
    def __init__(self):
        self.rows = []
        self.flushes = 0

    def add(self, row):
        self.rows.append(row)

    def flush(self):
        self.flushes += 1

    def scalars(self, query):
        model = query.column_descriptions[0]["entity"]
        rows = [row for row in self.rows if isinstance(row, model)]
        for criterion in query._where_criteria:
            rows = [
                row
                for row in rows
                if getattr(row, criterion.left.key) == criterion.right.value
            ]
        return SimpleNamespace(all=lambda: rows)

    def scalar(self, query):
        return next(iter(self.scalars(query).all()), None)


@pytest.fixture
def setup(monkeypatch):
    fence = AttemptFence(UUID(int=1), UUID(int=2), UUID(int=3), 1, UUID(int=4))
    scope = service._scope(fence)
    launch = SimpleNamespace(id=UUID(int=5))
    session = Session()

    def step(index):
        start = FactorGenerationStart(
            id=UUID(int=10 + index), **scope, logical_step=index
        )
        source = FactorGenerationSource(
            id=UUID(int=20 + index), **scope, start_id=start.id
        )
        session.rows.extend([start, source])
        for phase, kind, offset in [
            ("generation", "chat", 30),
            ("knowledge_update", "embedding", 40),
        ]:
            call = FactorUpstreamCall(
                id=UUID(int=offset + index),
                **scope,
                start_id=start.id,
                phase=phase,
                kind=kind,
            )
            outcome = FactorUpstreamCallOutcome(
                id=UUID(int=offset + 100 + index),
                **scope,
                call_id=call.id,
                status="completed",
                controller_launch_id=launch.id,
            )
            session.rows.extend([call, outcome])
        return start, source

    start, source = step(0)
    monkeypatch.setattr(service, "require_neutral_transaction", lambda *a, **k: None)
    monkeypatch.setattr(service, "require_rd_controller", lambda *a, **k: launch)
    monkeypatch.setattr(
        service,
        "require_research_spec",
        lambda *a: (None, SimpleNamespace(max_iterations=3)),
    )

    def scoped(session, model, key, current, **kwargs):
        row = next(
            row for row in session.rows if isinstance(row, model) and row.id == key
        )
        if not service._matching(row, current):
            raise DomainError("scope", "foreign", 409)
        return row

    monkeypatch.setattr(service, "_scope_row", scoped)

    def observe(current=start, evaluated=source, previous=None):
        decision = ObservedGenerationDecision(
            logical_step=current.logical_step,
            evaluated_source_id=evaluated.id,
            previous_adopted_source_id=previous,
            adopted_source_id=evaluated.id,
            continuation_source_id=evaluated.id,
            adopted_new=True,
        )
        return service.record_generation_observation(
            session, fence, object(), start_id=current.id, decision=decision
        )

    def boundary(observation, kind="advanced", next_step=1):
        return service.confirm_generation_boundary(
            session,
            fence,
            object(),
            observation_id=observation.id,
            boundary=kind,
            next_logical_step=next_step,
        )

    return SimpleNamespace(
        session=session,
        fence=fence,
        start=start,
        source=source,
        launch=launch,
        observe=observe,
        boundary=boundary,
        step=step,
        scope=scope,
    )


def test_authenticated_decision_is_not_completed_generation_or_snapshot(setup):
    observation = setup.observe()
    assert observation.qualification == service.QUALIFICATION
    assert not any(
        isinstance(row, FactorGenerationOutcome) for row in setup.session.rows
    )
    assert setup.session.flushes == 1
    boundary = setup.boundary(observation)
    assert boundary.observation_id == observation.id and boundary.next_logical_step == 1
    assert not any(
        isinstance(row, FactorGenerationOutcome) for row in setup.session.rows
    )


def test_replay_does_not_append_after_terminal_generation(setup):
    observation = setup.observe()
    boundary = setup.boundary(observation)
    setup.session.rows.append(
        FactorGenerationOutcome(id=UUID(int=90), **setup.scope, start_id=setup.start.id)
    )
    assert setup.observe() is observation
    assert setup.boundary(observation) is boundary
    assert setup.session.flushes == 2


def test_next_observation_requires_prior_boundary_and_exact_adopted_lineage(setup):
    first = setup.observe()
    second, source = setup.step(1)
    with pytest.raises(DomainError):
        setup.observe(second, source, setup.source.id)
    setup.boundary(first)
    with pytest.raises(DomainError):
        setup.observe(second, source, None)
    observation = setup.observe(second, source, setup.source.id)
    assert observation.logical_step == 1


@pytest.mark.parametrize(
    "kind,next_step",
    [("advanced", True), ("advanced", 2), ("returned", 1), ("invented", None)],
)
def test_boundary_type_or_next_step_drift_rejected(setup, kind, next_step):
    observation = setup.observe()
    with pytest.raises(DomainError):
        setup.boundary(observation, kind, next_step)
    assert setup.session.flushes == 1


def test_returned_boundary_is_distinct_and_has_no_next_step(setup):
    observation = setup.observe()
    boundary = setup.boundary(observation, "returned", None)
    assert boundary.next_logical_step is None
    with pytest.raises(DomainError):
        setup.boundary(observation)


@pytest.mark.parametrize(
    "change",
    [
        "open_call",
        "aborted",
        "wrong_controller",
        "no_knowledge",
        "wrong_source",
        "terminal",
    ],
)
def test_incomplete_or_foreign_evidence_cannot_create_observation(setup, change):
    if change == "open_call":
        setup.session.rows = [
            row
            for row in setup.session.rows
            if not isinstance(row, FactorUpstreamCallOutcome)
        ]
    if change == "aborted":
        next(
            row
            for row in setup.session.rows
            if isinstance(row, FactorUpstreamCallOutcome)
        ).status = "aborted"
    if change == "wrong_controller":
        next(
            row
            for row in setup.session.rows
            if isinstance(row, FactorUpstreamCallOutcome)
        ).controller_launch_id = UUID(int=99)
    if change == "no_knowledge":
        setup.session.rows = [
            row
            for row in setup.session.rows
            if not (
                isinstance(row, FactorUpstreamCall) and row.phase == "knowledge_update"
            )
        ]
    if change == "wrong_source":
        setup.source.id = UUID(int=99)
    if change == "terminal":
        setup.session.rows.append(
            FactorGenerationOutcome(
                id=UUID(int=90), **setup.scope, start_id=setup.start.id
            )
        )
    if change == "wrong_source":
        decision = ObservedGenerationDecision(
            logical_step=0,
            evaluated_source_id=UUID(int=20),
            previous_adopted_source_id=None,
            adopted_source_id=UUID(int=20),
            continuation_source_id=UUID(int=20),
            adopted_new=True,
        )
        invoke = lambda: service.record_generation_observation(
            setup.session,
            setup.fence,
            object(),
            start_id=setup.start.id,
            decision=decision,
        )
    else:
        invoke = setup.observe
    with pytest.raises(DomainError):
        invoke()
    assert setup.session.flushes == 0


def test_new_upstream_work_is_closed_after_observation(setup):
    service.require_unobserved_generation(setup.session, setup.fence, setup.start.id)
    setup.observe()
    with pytest.raises(DomainError):
        service.require_unobserved_generation(
            setup.session, setup.fence, setup.start.id
        )
