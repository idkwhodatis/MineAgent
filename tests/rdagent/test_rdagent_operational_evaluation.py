"""Evaluator routing with real immutable DTOs and explicit authority/session doubles.

No test here executes SQL, Docker, a native parser, or a successful controller.
Actual persisted reader/peer authority is intentionally not claimed by these tests.
"""

from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app import factor_native_services
from backend.app import rdagent_operational_evaluation as service
from backend.app.execution_snapshot_models import (
    ExecutionSnapshotOutcome,
    ExecutionSnapshotPublication,
)
from backend.app.execution_snapshot_services import VerifiedExecutionSnapshot
from backend.app.factor_models import FactorVersion
from backend.app.rdagent_models import (
    FactorCutoffExecution,
    FactorEvaluationStart,
    FactorGenerationSource,
)
from packages.factors.contracts import digest
from packages.factors.fixtures import demo_fixture
from packages.research_runtime.fences import AttemptFence
from runtimes.rdagent_operational.rpc import RPCError
from workers.research import numeric_fixture


@pytest.fixture
def scenario(monkeypatch):
    fence = AttemptFence(uuid4(), uuid4(), uuid4(), 1, uuid4())
    peer, factory, settings = object(), object(), object()
    source_id, evaluation_id, start_id, version_id, snapshot_id = (
        uuid4() for _ in range(5)
    )
    evaluation = FactorEvaluationStart(
        id=evaluation_id,
        evaluated_source_id=source_id,
        factor_version_id=version_id,
        start_id=start_id,
        expected_cutoffs=1,
    )
    source = FactorGenerationSource(
        id=source_id, factor_version_id=version_id, start_id=start_id
    )
    fixed = demo_fixture(factor_version_id=version_id, dataset_version_id=uuid4())
    rows = tuple(row for row in fixed.rows if row.session_date == date(2024, 3, 15))
    snapshot = VerifiedExecutionSnapshot(
        snapshot_id,
        source_id,
        evaluation_id,
        start_id,
        version_id,
        fixed.manifest.input.sha256,
        "a" * 64,
        "b" * 64,
        rows,
    )
    state = SimpleNamespace(
        publication=ExecutionSnapshotPublication(id=snapshot_id),
        outcome=ExecutionSnapshotOutcome(publication_id=snapshot_id),
        cutoff=None,
        version=FactorVersion(id=version_id, code_object_id=uuid4()),
        events=[],
        tx_count=0,
        peer_lost=False,
        publication_failure=False,
    )

    class Session:
        def scalar(self, statement):
            model = statement.column_descriptions[0]["entity"]
            return {
                ExecutionSnapshotPublication: state.publication,
                FactorCutoffExecution: state.cutoff,
                FactorVersion: state.version,
            }[model]

        def get(self, model, identity):
            assert model is ExecutionSnapshotOutcome and identity == snapshot_id
            return state.outcome

    session = Session()

    @contextmanager
    def transaction(*_args, **_kwargs):
        state.tx_count += 1
        state.events.append("transaction")
        yield session

    def authorize(given_session, given_fence, given_peer):
        assert given_session is session and given_fence is fence and given_peer is peer
        state.events.append("authorize")
        if state.peer_lost:
            raise RPCError("synthetic_peer_lost")

    def scoped(_session, model, identity, given_fence):
        assert given_fence is fence
        row = {FactorEvaluationStart: evaluation, FactorGenerationSource: source}[model]
        assert row.id == identity
        return row

    def read(*args):
        assert args == (session, fence, peer, evaluation_id, settings)
        authorize(session, fence, peer)
        state.events.append("read")
        assert state.outcome is not None
        return snapshot, fixed.manifest.input, True

    def publish(*args):
        assert args == (factory, fence, evaluation_id, settings)
        state.events.append("publish")
        if state.publication_failure:
            raise RPCError("synthetic_publication_failure")
        state.outcome = ExecutionSnapshotOutcome(publication_id=snapshot_id)
        return snapshot_id

    def no_unit(*_args, **_kwargs):
        pytest.fail("Replay/rejection must not create another physical unit")

    monkeypatch.setattr(service, "snapshot_transaction", transaction)
    monkeypatch.setattr(service, "neutral_transaction", transaction)
    monkeypatch.setattr(service, "require_rd_controller", authorize)
    monkeypatch.setattr(service, "_scope_row", scoped)
    monkeypatch.setattr(service, "read_evaluation_snapshot", read)
    monkeypatch.setattr(service, "publish_execution_snapshot", publish)
    monkeypatch.setattr(numeric_fixture, "execute_fixed_candidate_from_source", no_unit)
    monkeypatch.setattr(
        numeric_fixture, "execute_fixed_normalizer_from_native", no_unit
    )
    monkeypatch.setattr(factor_native_services, "publish_completed_candidate", no_unit)
    return SimpleNamespace(
        state=state,
        source=source,
        evaluation=evaluation,
        snapshot=snapshot,
        fence=fence,
        peer=peer,
        factory=factory,
        settings=settings,
        source_id=source_id,
        evaluation_id=evaluation_id,
    )


def execute(s):
    return service.execute_or_reuse_snapshot(
        s.factory,
        s.fence,
        s.peer,
        source_id=s.source_id,
        evaluation_id=s.evaluation_id,
        settings=s.settings,
    )


def test_completed_replay_reads_retained_snapshot_without_writes_or_units(scenario):
    reply = execute(scenario)
    assert reply["snapshot_id"] == str(scenario.snapshot.snapshot_id)
    assert reply["rows"] == [
        row.model_dump(mode="json") for row in scenario.snapshot.rows
    ]
    assert reply["rows_sha256"] == digest(reply["rows"])
    assert scenario.state.events == ["transaction", "authorize", "authorize", "read"]
    assert execute(scenario) == reply
    assert "publish" not in scenario.state.events


def test_pending_publication_repairs_only_existing_publication_then_fresh_read(
    scenario,
):
    scenario.state.outcome = None
    reply = execute(scenario)
    assert reply["snapshot_id"] == str(scenario.snapshot.snapshot_id)
    assert scenario.state.events == [
        "transaction",
        "authorize",
        "transaction",
        "authorize",
        "publish",
        "transaction",
        "authorize",
        "read",
    ]
    assert execute(scenario) == reply
    assert scenario.state.events.count("publish") == 1


def test_pending_publication_failure_is_not_read_as_success(scenario):
    scenario.state.outcome = None
    scenario.state.publication_failure = True
    with pytest.raises(RPCError, match="synthetic_publication_failure"):
        execute(scenario)
    assert "read" not in scenario.state.events
    assert scenario.state.outcome is None


def test_partial_cutoff_never_reexecutes_or_creates_publication(scenario):
    scenario.state.publication = None
    scenario.state.cutoff = FactorCutoffExecution(id=uuid4())
    with pytest.raises(RPCError, match="partial_execution_requires_reconciliation"):
        execute(scenario)
    assert (
        "publish" not in scenario.state.events and "read" not in scenario.state.events
    )


@pytest.mark.parametrize(
    "field",
    ["evaluated_source_id", "factor_version_id", "start_id", "expected_cutoffs"],
)
def test_source_evaluation_mismatch_rejected_before_any_execution_or_read(
    scenario, field
):
    setattr(scenario.evaluation, field, 2 if field == "expected_cutoffs" else uuid4())
    with pytest.raises(RPCError, match="complete_single_cutoff_required"):
        execute(scenario)
    assert scenario.state.events == ["transaction", "authorize"]


def test_lost_peer_cannot_repair_pending_publication(scenario):
    scenario.state.outcome = None
    scenario.state.peer_lost = True
    with pytest.raises(RPCError, match="synthetic_peer_lost"):
        execute(scenario)
    assert (
        "publish" not in scenario.state.events and "read" not in scenario.state.events
    )


def test_five_completed_reads_keep_each_fresh_reader_and_no_new_unit(scenario):
    for _ in range(5):
        execute(scenario)
    assert scenario.state.tx_count == 5
    assert scenario.state.events.count("read") == 5
    assert scenario.state.events.count("authorize") == 10


@pytest.mark.parametrize("reason", ["revoked", "owner_changed", "epoch_changed"])
def test_completed_reuse_propagates_current_reader_rejection(
    scenario, monkeypatch, reason
):
    def changed(*args):
        raise RPCError(reason)

    monkeypatch.setattr(service, "read_evaluation_snapshot", changed)
    with pytest.raises(RPCError, match=reason):
        execute(scenario)
    assert scenario.state.tx_count == 1
