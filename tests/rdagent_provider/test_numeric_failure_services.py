"""Fresh resolution negatives using DB/physical-reader doubles, not PG proof."""

from types import SimpleNamespace as NS
from uuid import UUID

import pytest

from backend.app import rdagent_numeric_failure_services as module
from packages.research_runtime.process_contracts import ProcessError
from runtimes.rdagent_operational.rpc import RPCError


@pytest.fixture
def graph(monkeypatch):
    source = NS(id=UUID(int=1), factor_version_id=UUID(int=3), start_id=UUID(int=4))
    evaluation = NS(
        id=UUID(int=2),
        evaluated_source_id=source.id,
        factor_version_id=source.factor_version_id,
        start_id=source.start_id,
        expected_cutoffs=1,
    )
    cutoff = NS(
        id=UUID(int=5),
        candidate_admission_id=UUID(int=6),
        evaluated_source_id=source.id,
        factor_version_id=source.factor_version_id,
        start_id=source.start_id,
        cutoff_ordinal=0,
        projection={"frozen": True},
        projection_sha256="e" * 64,
    )
    held = NS(
        id=UUID(int=7), purpose="process_unit", state="settled", amounts={"held": 1}
    )
    state = NS(
        record=NS(id=UUID(int=8), payload_sha256="a" * 64),
        charged=NS(outcome="conservative_unknown", charge=held.amounts),
        cutoffs=[cutoff],
        gates=0,
        reads=0,
        native=None,
        snapshot=None,
    )
    proof = NS(
        admission=NS(
            logical_operation_id=cutoff.id,
            role="factor_cutoff_candidate",
            reservation_id=held.id,
        ),
        inputs="exact-inputs",
        rejection_receipt_id=state.record.id,
        rejection_receipt_hash="a" * 64,
        stop_receipt_id=UUID(int=9),
        stop_receipt_hash="b" * 64,
        outcome="child_nonzero",
        child_exit_code=1,
    )
    resolved = NS(
        binding="exact-inputs",
        prefix=NS(
            sha256=cutoff.projection_sha256, model_dump=lambda **_: cutoff.projection
        ),
    )

    def gate(*_):
        state.gates += 1
        return NS(id=UUID(int=10))

    def physical(*a, **kw):
        state.reads += 1
        return proof

    monkeypatch.setattr(module, "require_failure_profile", gate)
    monkeypatch.setattr(module, "read_numeric_rejection", physical)
    monkeypatch.setattr(module, "resolve_candidate_inputs", lambda *a, **kw: resolved)
    values = {
        "FactorGenerationSource": source,
        "FactorEvaluationStart": evaluation,
        "ResearchBudgetReservation": held,
    }
    monkeypatch.setattr(
        module, "_scope_row", lambda session, model, *a, **kw: values[model.__name__]
    )

    class Session:
        def scalars(self, statement):
            return NS(all=lambda: state.cutoffs)

        def scalar(self, statement):
            model = statement.column_descriptions[0]["entity"].__name__
            return {
                "ResearchProcessReceipt": state.record,
                "ResearchBudgetSettlement": state.charged,
                "ExecutionSnapshotPublication": state.snapshot,
                "NativeHDFPublication": state.native,
            }[model]

    fence = NS(
        owner_id=UUID(int=20),
        run_id=UUID(int=21),
        attempt_id=UUID(int=22),
        generation=1,
    )
    state.call = lambda: module.read_candidate_failure(
        Session(),
        fence,
        object(),
        source_id=source.id,
        evaluation_id=evaluation.id,
        settings=NS(storage_root="unused"),
    )
    state.proof, state.held = proof, held
    return state


def test_positive_rejection_requires_fresh_input_charge_and_final_actor(graph):
    observed = graph.call()
    assert observed.frame.child_exit_code == 1
    assert graph.reads == 1 and graph.gates == 2


@pytest.mark.parametrize("missing", ["membership", "cutoff"])
def test_missing_output_is_not_a_rejection_frame(graph, missing):
    if missing == "membership":
        graph.record = None
    else:
        graph.cutoffs = []
    assert graph.call() is None
    assert graph.reads == 0


def test_no027_capability_cannot_send_failure_frame(graph, monkeypatch):
    def closed(*_):
        raise ProcessError("numeric_rejection_schema_unsupported")

    monkeypatch.setattr(module, "require_failure_profile", closed)
    with pytest.raises(ProcessError):
        graph.call()
    assert graph.reads == 0


def test_no_positive_stop_cannot_send_failure_frame(graph, monkeypatch):
    def uncertain(*a, **kw):
        raise ProcessError("numeric_rejection_uncommitted")

    monkeypatch.setattr(module, "read_numeric_rejection", uncertain)
    with pytest.raises(ProcessError):
        graph.call()


@pytest.mark.parametrize(
    "change", ["no_charge", "held", "refund", "wrong_inputs", "snapshot", "native"]
)
def test_incomplete_or_conflicting_evidence_never_becomes_failure_feedback(
    graph, change
):
    if change == "no_charge":
        graph.charged = None
    elif change == "held":
        graph.held.state = "uncertain"
    elif change == "refund":
        graph.charged.charge = {"held": 0}
    elif change == "wrong_inputs":
        graph.proof.inputs = "other-inputs"
    elif change == "snapshot":
        graph.snapshot = object()
    else:
        graph.native = object()
    with pytest.raises(RPCError):
        graph.call()


def test_committed_capture_checks027_before_claim_and_preserves_return(monkeypatch):
    from contextlib import contextmanager

    from backend.app import numeric_rejection_services
    from workers.research import numeric_process as worker

    events = []

    @contextmanager
    def lifecycle(*a, **kw):
        yield

    @contextmanager
    def transaction(*a, **kw):
        yield object()

    monkeypatch.setattr(worker, "shared_lifecycle", lifecycle)
    monkeypatch.setattr(worker, "neutral_transaction", transaction)

    def gate(*a):
        events.append("capability")

    def claim(*a):
        events.append("claim")
        return NS(admission=NS(role="factor_cutoff_candidate", image_id="image"))

    monkeypatch.setattr(
        numeric_rejection_services, "require_numeric_rejection_schema", gate
    )
    monkeypatch.setattr(worker, "claim_numeric_launch", claim)
    monkeypatch.setattr(worker, "check_input_bytes", lambda *a: None)

    def execute(*a, **kw):
        assert kw["capture_rejection"] is True
        events.append("execute")
        return "actual-fresh-rejection-receipt"

    monkeypatch.setattr(worker, "_execute_claimed_numeric_unit", execute)
    kwargs = {
        "factory": NS(kw={"bind": object()}),
        "fence": object(),
        "admission_id": UUID(int=1),
        "storage_root": "unused",
        "input_bytes": {},
        "inventory": {"image_id": "image"},
        "capture_rejection": True,
    }
    assert (
        worker._execute_committed_numeric_unit(**kwargs)
        == "actual-fresh-rejection-receipt"
    )
    assert events == ["capability", "claim", "execute"]
    events.clear()

    def closed(*a):
        raise ProcessError("numeric_rejection_schema_unsupported")

    monkeypatch.setattr(
        numeric_rejection_services, "require_numeric_rejection_schema", closed
    )
    with pytest.raises(ProcessError):
        worker._execute_committed_numeric_unit(**kwargs)
    assert events == []
