"""027 actual-file/closed observer unit tests; host/engine calls are explicit doubles."""

import hashlib
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from packages.factors.contracts import canonical_bytes, digest
from packages.openevolve_structured import evaluator_bridge as bridge
from packages.openevolve_structured import observer, observer_transport
from packages.openevolve_structured.contracts import (
    EvaluationGrant,
    EvaluationResponse,
    StructuredBootstrap,
    StructuredSearchSpec,
)
from packages.openevolve_structured.objective import StructuredObjective
from tests.openevolve.test_structured_candidate import setup_candidate


def declared():
    domain, document = setup_candidate()
    raw = canonical_bytes(document)
    window = domain.seed_program.signal_window
    spec = StructuredSearchSpec(
        owner_id=uuid4(),
        run_id=uuid4(),
        execution_binding_id=uuid4(),
        execution_binding_sha256="a" * 64,
        domain=domain,
        seed_candidate_sha256=hashlib.sha256(raw).hexdigest(),
        objective=StructuredObjective(
            window={"start": window.start, "end": window.end + timedelta(days=1)},
            initial_cash=10000.0,
        ),
        maximum_mutation_slots=1,
    )
    now = datetime.now(UTC)
    bootstrap = StructuredBootstrap(
        admission_id=uuid4(),
        epoch_id=uuid4(),
        spec=spec,
        issued_at=now,
        not_after=now + timedelta(seconds=60),
        physical_profile_sha256="b" * 64,
        runtime_profile_sha256="b" * 64,
        config_sha256="c" * 64,
        adapter_patch_sha256="d" * 64,
    )
    return bootstrap, raw


def wire_case(monkeypatch, tmp_path, *, change=None):
    initial, raw = declared()
    native_id = uuid4()
    grant = EvaluationGrant(
        owner_id=initial.spec.owner_id,
        run_id=initial.spec.run_id,
        epoch_id=initial.epoch_id,
        candidate_id=uuid4(),
        upstream_program_id=native_id,
        candidate_sha256=hashlib.sha256(raw).hexdigest(),
        program_sha256=digest(initial.spec.domain.seed_program),
        execution_id=uuid4(),
        execution_binding_sha256=initial.spec.execution_binding_sha256,
        ticket="f" * 64,
        phase="seed",
    )
    answer = EvaluationResponse(
        candidate_id=grant.candidate_id,
        candidate_sha256=grant.candidate_sha256,
        program_sha256=grant.program_sha256,
        execution_id=grant.execution_id,
        execution_binding_sha256=grant.execution_binding_sha256,
        result_sha256="e" * 64,
        objective_sha256=digest(initial.spec.objective),
        objective_receipt_id=uuid4(),
        eligible=True,
        combined_score=-1.25,
        reason="eligible",
        feedback="Observed fixed-window accounting, not causal advantage",
    )
    if change is not None:
        key, value = change
        answer = answer.model_copy(update={key: value})
    events = []

    def event(kind, payload):
        events.append((kind, payload))
        if kind == "seed_source":
            return {}
        if kind == "candidate_intent":
            assert payload["source"].encode() == raw
            return grant.model_dump(mode="json")
        if kind == "execute_evaluation":
            assert payload["grant"] == grant.model_dump(mode="json")
            return answer.model_dump(mode="json")
        if kind == "evaluation_complete":
            return {
                "acknowledged": True,
                "objective_receipt_id": str(answer.objective_receipt_id),
            }
        raise AssertionError(kind)

    for module in (observer_transport, bridge):
        monkeypatch.setattr(
            module, "bootstrap", lambda: initial.model_dump(mode="json")
        )
        monkeypatch.setattr(module, "host_event", event)
    monkeypatch.setitem(
        sys.modules,
        "openevolve.evaluation_result",
        SimpleNamespace(EvaluationResult=lambda **kwargs: SimpleNamespace(**kwargs)),
    )
    path = tmp_path / "candidate.json"
    path.write_bytes(raw)
    return initial, raw, native_id, path, events


def test_actual_json_file_reaches_separate_evaluation_and_exact_host_ack(
    monkeypatch, tmp_path
):
    initial, raw, native_id, path, events = wire_case(monkeypatch, tmp_path)
    with observer.epoch_control(initial.epoch_id):
        observer.assert_seed_source(raw.decode())
        with observer.candidate_scope(str(native_id), raw.decode()):
            result = bridge.evaluate(path)
            assert result.metrics == {"combined_score": -1.25}
            observer.evaluation_complete(result.metrics)
    assert [kind for kind, _ in events] == [
        "seed_source",
        "candidate_intent",
        "execute_evaluation",
        "evaluation_complete",
    ]


@pytest.mark.parametrize(
    "field,value",
    [
        ("candidate_id", uuid4()),
        ("candidate_sha256", "1" * 64),
        ("program_sha256", "2" * 64),
        ("execution_id", uuid4()),
        ("execution_binding_sha256", "3" * 64),
        ("objective_sha256", "4" * 64),
    ],
)
def test_unrelated_actual_outcome_cannot_be_population_score(
    monkeypatch, tmp_path, field, value
):
    initial, raw, native_id, path, events = wire_case(
        monkeypatch, tmp_path, change=(field, value)
    )
    with (
        observer.epoch_control(initial.epoch_id),
        pytest.raises(observer.SearchControlAbort),
        observer.candidate_scope(str(native_id), raw.decode()),
    ):
        bridge.evaluate(path)
    assert not any(kind == "evaluation_complete" for kind, _ in events)


@pytest.mark.parametrize("mode", ["python", "symlink", "different_json"])
def test_temporary_candidate_must_match_registered_closed_document(
    monkeypatch, tmp_path, mode
):
    initial, raw, native_id, path, events = wire_case(monkeypatch, tmp_path)
    if mode == "python":
        path.write_bytes(b'raise AssertionError("must never execute")')
    elif mode == "symlink":
        other = tmp_path / "other"
        other.write_bytes(raw)
        path.unlink()
        path.symlink_to(other)
    else:
        path.write_bytes(raw + b" ")
    with (
        observer.epoch_control(initial.epoch_id),
        pytest.raises(observer.SearchControlAbort),
        observer.candidate_scope(str(native_id), raw.decode()),
    ):
        bridge.evaluate(path)
    assert not any(kind == "execute_evaluation" for kind, _ in events)


def test_original_bootstrap_deadline_never_renews():
    initial, _ = declared()
    assert initial.remaining(initial.issued_at.timestamp()) == 60
    with pytest.raises(ValueError):
        initial.remaining(initial.not_after.timestamp())
