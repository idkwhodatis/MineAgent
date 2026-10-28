"""Private same-call projection doubles; not PostgreSQL/container acceptance."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import factor_analysis_results as results
from backend.app import rdagent_configured_evaluation as configured
from runtimes.rdagent_operational.rpc import RPCError


def setup_reader(monkeypatch):
    fence = NS(owner_id=uuid4(), run_id=uuid4(), attempt_id=uuid4(), generation=1)
    publication, analysis = NS(id=uuid4()), NS(id=uuid4())
    evaluation = NS(
        id=uuid4(),
        evaluated_source_id=uuid4(),
        start_id=uuid4(),
        factor_version_id=uuid4(),
        input_sha256="a" * 64,
        snapshot_input={},
    )
    snapshot = NS(
        snapshot_id=publication.id,
        evaluation_id=evaluation.id,
        source_id=evaluation.evaluated_source_id,
        generation_start_id=evaluation.start_id,
        factor_version_id=evaluation.factor_version_id,
        input_sha256=evaluation.input_sha256,
    )
    measured = NS(snapshot_id=publication.id)
    monkeypatch.setattr(configured, "require_rd_controller", lambda *a: None)
    monkeypatch.setattr(configured, "_scope_row", lambda *a: evaluation)
    monkeypatch.setattr(configured, "SnapshotInput", NS(model_validate=lambda x: x))
    calls = []

    def read(*args):
        calls.append(args)
        return snapshot, measured

    monkeypatch.setattr(configured, "_read_verified_analysis_with_snapshot", read)

    def invoke():
        sequence = iter([publication, analysis])
        session = NS(scalar=lambda *a: next(sequence))
        return configured.read_configured_evaluation(
            session, fence, object(), evaluation.id, object()
        )

    return invoke, snapshot, measured, calls


def test_each_public_call_consumes_new_private_closed_read(monkeypatch):
    invoke, snapshot, measured, calls = setup_reader(monkeypatch)
    assert invoke() == (snapshot, {}, measured)
    assert invoke() == (snapshot, {}, measured)
    assert len(calls) == 2
    assert calls[0][0] is not calls[1][0]


@pytest.mark.parametrize(
    "field",
    [
        "snapshot_id",
        "evaluation_id",
        "source_id",
        "generation_start_id",
        "factor_version_id",
        "input_sha256",
    ],
)
def test_returned_snapshot_still_requires_every_evaluation_binding(monkeypatch, field):
    invoke, snapshot, _, _ = setup_reader(monkeypatch)
    setattr(snapshot, field, uuid4())
    with pytest.raises(RPCError, match="configured_snapshot_binding"):
        invoke()


def test_analysis_snapshot_drift_rejected(monkeypatch):
    invoke, _, measured, _ = setup_reader(monkeypatch)
    measured.snapshot_id = uuid4()
    with pytest.raises(RPCError, match="configured_analysis_snapshot_drift"):
        invoke()


def test_public_analysis_wrapper_does_not_retain_snapshot(monkeypatch):
    calls = []

    def fresh(*args):
        calls.append(args)
        return object(), object()

    monkeypatch.setattr(results, "_read_verified_analysis_with_snapshot", fresh)
    first = results.read_verified_direct_analysis(1, 2, 3, 4)
    second = results.read_verified_direct_analysis(1, 2, 3, 4)
    assert first is not second and len(calls) == 2


@pytest.mark.parametrize("tamper", [None, "source", "receipt", "result"])
def test_measured_retains_stopped_then_final_physical_read(monkeypatch, tamper):
    from backend.app import analysis_process_services as process
    from backend.app.services import DomainError

    events = []
    snapshot = object()
    binding = NS(snapshot_id=uuid4())
    receipt = NS(
        analysis_id=3,
        admission=NS(id=4),
        result_bytes=b"ok",
        sha256="exact",
        size_bytes=2,
    )
    result = object()
    value = NS(protocol=1, factors=2, labels=3, calendar_sessions=4)
    monkeypatch.setattr(results, "require_analysis_plan", lambda *a: binding)

    def stopped(*args):
        events.append("stopped_full_input_and_receipts")
        if tamper == "receipt":
            raise DomainError("bad_receipt", "changed receipt", 409)
        return receipt, result

    def final_input(*args):
        events.append("final_fresh_input")
        if tamper == "source":
            raise DomainError("changed_source", "changed source", 409)
        if tamper == "result":
            receipt.result_bytes = b"changed"
        return None, None, value, b"input", snapshot

    monkeypatch.setattr(process, "read_stopped_analysis", stopped)
    monkeypatch.setattr(results, "_plan", lambda *a: (None, object()))
    monkeypatch.setattr(results, "_materialize_with_snapshot", final_input)
    monkeypatch.setattr(results, "DirectAnalysisProjection", lambda *a: a)
    monkeypatch.setattr(results, "measure_direct_objective", lambda *a: "measurement")
    monkeypatch.setattr(results, "canonical_bytes", lambda *a: b"ok")
    monkeypatch.setattr(results, "digest", lambda *a: "exact")
    if tamper:
        with pytest.raises(DomainError):
            results._measured_with_snapshot(1, 2, 3, 4, 5)
    else:
        assert results._measured_with_snapshot(1, 2, 3, 4, 5) == (
            binding,
            receipt,
            result,
            "measurement",
            snapshot,
        )
        assert results._measured_with_snapshot(1, 2, 3, 4, 5)[-1] is snapshot
        assert events == ["stopped_full_input_and_receipts", "final_fresh_input"] * 2
    assert events[0] == "stopped_full_input_and_receipts"
    if tamper != "receipt":
        assert events[1] == "final_fresh_input"
