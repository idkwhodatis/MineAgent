"""Objective authority boundary; host responses mocked, not durable evidence."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from uuid import uuid4

import pytest
from pydantic import ValidationError

from packages.openevolve_adapter import observer as o
from packages.openevolve_adapter import observer_transport as transport
from packages.openevolve_adapter.evaluator_protocol import EvaluationResponse


def response(**changes):
    return EvaluationResponse(
        candidate_id=uuid4(),
        source_sha256="a" * 64,
        objective_sha256="b" * 64,
        objective_receipt_id=uuid4(),
        feedback="",
        **({"eligible": True, "combined_score": -1.25, "reason": "eligible"} | changes),
    )


def test_context_copy_thread_returns_shared_objective_record(monkeypatch):
    receipt = response()
    current = {"grant": {}, "result": None}
    token = o._EVALUATION.set(current)
    monkeypatch.setattr(
        transport,
        "host_event",
        lambda *_: {
            "acknowledged": True,
            "objective_receipt_id": str(receipt.objective_receipt_id),
        },
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(
                copy_context().run, o.record_evaluation_result, receipt
            ).result()
        assert current["result"] is receipt
        assert o.evaluation_complete({"combined_score": -1.25}) == {
            "combined_score": -1.25
        }
    finally:
        o._EVALUATION.reset(token)


@pytest.mark.parametrize(
    "metrics", [{"error": 0.0}, {"combined_score": 0.0}, {"combined_score": False}, {}]
)
def test_fallback_or_score_drift_never_admitted(monkeypatch, metrics):
    token = o._EVALUATION.set({"grant": {}, "result": response()})
    monkeypatch.setattr(
        transport, "host_event", lambda *_: pytest.fail("no ACK for invented score")
    )
    try:
        with pytest.raises(o.SearchControlAbort):
            o.evaluation_complete(metrics)
    finally:
        o._EVALUATION.reset(token)


def test_undefined_is_ordinary_rejection_only_after_receipt_ack(monkeypatch):
    receipt = response(eligible=False, combined_score=None, reason="native_undefined")
    token = o._EVALUATION.set({"grant": {}, "result": receipt})
    calls = []

    def host(kind, payload):
        calls.append((kind, payload))
        return {
            "acknowledged": True,
            "objective_receipt_id": str(receipt.objective_receipt_id),
        }

    monkeypatch.setattr(transport, "host_event", host)
    try:
        with o.epoch_control(uuid4()) as epoch:
            with (
                pytest.raises(o.CandidateEvaluationRejected),
                o.control_guard("candidate_registration"),
            ):
                o.evaluation_complete({})
            assert epoch.population_state_trusted
        assert calls[0][0] == "evaluation_complete"
        assert calls[0][1]["metrics"] == {}
    finally:
        o._EVALUATION.reset(token)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True])
def test_invalid_objective_scalar_rejected(value):
    with pytest.raises(ValidationError):
        response(combined_score=value)


def test_ineligible_cannot_carry_zero():
    with pytest.raises(ValidationError):
        response(eligible=False, combined_score=0.0, reason="native_error")


def test_missing_receipt_and_duplicate_receipt_fail_stop():
    token = o._EVALUATION.set({"grant": {}, "result": None})
    try:
        with pytest.raises(o.SearchControlAbort):
            o.evaluation_complete({"combined_score": 0.0})
        o.record_evaluation_result(response())
        with pytest.raises(o.SearchControlAbort):
            o.record_evaluation_result(response())
    finally:
        o._EVALUATION.reset(token)
