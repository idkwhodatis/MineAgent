"""Configured replay routing with explicit authority/DB doubles; no execution claim."""

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app import rdagent_configured_evaluation as service
from backend.app.execution_snapshot_models import (
    ExecutionSnapshotOutcome,
    ExecutionSnapshotPublication,
)
from backend.app.rdagent_models import (
    FactorCutoffExecution,
    FactorGenerationSource,
)
from backend.app.research_process_models import ResearchProcessAdmission
from runtimes.rdagent_operational.rpc import RPCError


@pytest.fixture
def case(monkeypatch):
    source = SimpleNamespace(id=uuid4(), factor_version_id=uuid4(), start_id=uuid4())
    evaluation = SimpleNamespace(
        id=uuid4(),
        evaluated_source_id=source.id,
        factor_version_id=source.factor_version_id,
        start_id=source.start_id,
        expected_cutoffs=1,
    )
    fence = SimpleNamespace(
        owner_id=uuid4(), run_id=uuid4(), attempt_id=uuid4(), generation=1
    )
    snapshot = SimpleNamespace(
        snapshot_id=uuid4(),
        source_id=source.id,
        evaluation_id=evaluation.id,
        factor_version_id=source.factor_version_id,
        rows=(),
    )
    state = SimpleNamespace(
        publication=SimpleNamespace(id=snapshot.snapshot_id),
        snapshot_outcome=object(),
        analysis_outcome=object(),
        cutoff=None,
        analysis_admission=None,
        events=[],
        status="not_evaluated",
    )
    aid = uuid4()

    class Session:
        def scalar(self, query):
            return {
                ExecutionSnapshotPublication: state.publication,
                FactorCutoffExecution: state.cutoff,
                ResearchProcessAdmission: state.analysis_admission,
            }[query.column_descriptions[0]["entity"]]

        def get(self, model, identity):
            return (
                state.snapshot_outcome
                if model is ExecutionSnapshotOutcome
                else state.analysis_outcome
            )

    session = Session()

    @contextmanager
    def transaction(*args):
        yield session

    monkeypatch.setattr(service, "snapshot_transaction", transaction)
    monkeypatch.setattr(
        service, "require_rd_controller", lambda *args: state.events.append("authorize")
    )
    monkeypatch.setattr(
        service,
        "_scope_row",
        lambda s, model, *args: (
            source if model is FactorGenerationSource else evaluation
        ),
    )
    monkeypatch.setattr(service, "prepare_direct_analysis_intent", lambda *args: aid)
    monkeypatch.setattr(
        service,
        "read_configured_evaluation",
        lambda *args: (
            snapshot,
            None,
            SimpleNamespace(
                analysis_id=aid,
                measurement=SimpleNamespace(
                    status=state.status, reason="no_defined_days"
                ),
            ),
        ),
    )

    def publish(*args):
        state.events.append("publish")
        state.snapshot_outcome = object()
        return snapshot.snapshot_id

    monkeypatch.setattr(service, "publish_execution_snapshot", publish)
    from workers.research import analysis_process, rdagent_numeric

    def denied(*args, **kwargs):
        pytest.fail("Unexpected new external unit")

    monkeypatch.setattr(analysis_process, "execute_analysis_acceptance_unit", denied)
    monkeypatch.setattr(rdagent_numeric, "execute_configured_candidate", denied)
    monkeypatch.setattr(rdagent_numeric, "execute_configured_normalizer", denied)
    return SimpleNamespace(
        state=state, source=source, evaluation=evaluation, fence=fence
    )


def execute(s):
    return service.execute_configured_evaluation(
        object(),
        s.fence,
        object(),
        source_id=s.source.id,
        evaluation_id=s.evaluation.id,
        settings=object(),
    )


def test_completed_replay_no_units_and_undefined_not_success(case):
    result = execute(case)
    assert "not_evaluated" in result["feedback"]
    assert "passed=True" not in result["feedback"]
    assert "publish" not in case.state.events


def test_pending_snapshot_publication_only_repair(case):
    case.state.snapshot_outcome = None
    execute(case)
    assert case.state.events.count("publish") == 1


def test_partial_candidate_never_reexecutes(case):
    case.state.publication = None
    case.state.cutoff = object()
    with pytest.raises(RPCError, match="partial_execution"):
        execute(case)


def test_partial_analysis_never_reexecutes(case):
    case.state.analysis_outcome = None
    case.state.analysis_admission = object()
    with pytest.raises(RPCError, match="partial_analysis"):
        execute(case)


@pytest.mark.parametrize(
    "field",
    ["evaluated_source_id", "factor_version_id", "start_id", "expected_cutoffs"],
)
def test_exact_evaluation_scope(case, field):
    setattr(case.evaluation, field, 2 if field == "expected_cutoffs" else uuid4())
    with pytest.raises(RPCError, match="complete_single_cutoff"):
        execute(case)
