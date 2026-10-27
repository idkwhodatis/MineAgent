"""Actual typed DTO/fixture tests with transaction and retained-reader doubles only."""

import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from backend.app import rdagent_operational_finalization as module
from backend.app.execution_snapshot_services import VerifiedExecutionSnapshot
from backend.app.rdagent_models import (
    FactorGenerationSource,
    FactorGenerationStart,
    FactorResponseAssembly,
)
from packages.factors.contracts import DateWindow, canonical_bytes
from packages.factors.fixtures import demo_fixture
from packages.rdagent_adapter.execution_outcomes import (
    RDExecutionFeedback,
    parse_document,
)
from packages.research_runtime.engine_broker_contracts import RDChatResponse
from runtimes.rdagent_operational.rpc import RPCError


@pytest.fixture
def finalizer(monkeypatch):
    fence = SimpleNamespace(
        owner_id=uuid4(), run_id=uuid4(), attempt_id=uuid4(), generation=1
    )
    source = SimpleNamespace(
        id=uuid4(), start_id=uuid4(), factor_version_id=uuid4(), assembly_id=uuid4()
    )
    fixture = demo_fixture(
        factor_version_id=source.factor_version_id, dataset_version_id=uuid4()
    )
    inp = fixture.manifest.input.model_copy(
        update={"window": DateWindow(start=date(2024, 3, 15), end=date(2024, 3, 16))}
    )
    source.source_sha256 = inp.version.code_sha256
    evaluation = SimpleNamespace(id=uuid4())
    snapshot = VerifiedExecutionSnapshot(
        uuid4(),
        source.id,
        evaluation.id,
        source.start_id,
        source.factor_version_id,
        inp.sha256,
        "b" * 64,
        "c" * 64,
        tuple(r for r in fixture.rows if r.session_date == date(2024, 3, 15)),
    )
    upstream = {
        "execution": "actual execution",
        "return_checking": "format",
        "code": "critique",
        "final_decision": True,
    }
    doc = RDExecutionFeedback(
        **module._common(fence, source, snapshot),
        upstream_feedback=canonical_bytes(upstream).decode(),
        row_count=3,
        missing_count=0,
        declared_window={**inp.window.model_dump(mode="json"), "complete": True},
        objective_passed=True,
    )
    feedback_obj = SimpleNamespace(id=uuid4(), sha256=doc.sha256)
    session = Mock()
    session.scalar.side_effect = lambda statement: (
        evaluation if "factor_evaluation_starts" in str(statement) else None
    )
    calls = [SimpleNamespace(id=uuid4())]
    session.scalars.side_effect = lambda _: calls
    scopes = []

    @contextmanager
    def transaction(*args):
        scopes.append("begin")
        try:
            yield session
        except BaseException:
            scopes.append("rollback")
            raise
        else:
            scopes.append("commit")

    monkeypatch.setattr(module, "snapshot_transaction", transaction)
    authorize = Mock()
    monkeypatch.setattr(module, "require_rd_controller", authorize)
    assembly = SimpleNamespace(id=source.assembly_id, object_sha256="d" * 64)
    start = SimpleNamespace(id=source.start_id, mutation_reservation_id=uuid4())

    def scoped(_session, model, identity, _fence, **kwargs):
        result = {
            FactorGenerationSource: source,
            FactorResponseAssembly: assembly,
            FactorGenerationStart: start,
        }[model]
        assert result.id == identity
        return result

    monkeypatch.setattr(module, "_scope_row", scoped)
    reader = Mock(return_value=(snapshot, inp, True))
    monkeypatch.setattr(module, "read_evaluation_snapshot", reader)
    feedback_reader = Mock(return_value=(feedback_obj, doc))
    monkeypatch.setattr(module, "_published_feedback", feedback_reader)
    members = [SimpleNamespace(id=uuid4())]
    monkeypatch.setattr(module, "_call_members", lambda *a: members)
    response_reader = Mock(
        return_value=(
            object(),
            RDChatResponse(content='{"final_decision":true}', finish_reason="stop"),
        )
    )
    monkeypatch.setattr(module, "_completed_member_response", response_reader)
    observation = SimpleNamespace(id=uuid4())
    boundary = SimpleNamespace(id=uuid4(), controller_launch_id=uuid4())
    observe = Mock(return_value=observation)
    confirm = Mock(return_value=boundary)
    monkeypatch.setattr(module, "record_generation_observation", observe)
    monkeypatch.setattr(module, "confirm_generation_boundary", confirm)
    files, published = {}, []

    def publish(factory, fence, settings, kind, raw, key):
        parse_document(kind, raw)
        identity = uuid4()
        files[identity] = raw
        published.append((kind, raw, key))
        return identity

    monkeypatch.setattr(module, "_publish", publish)
    object_reader = Mock(
        side_effect=lambda session, fence, identity, kind, sha, settings: files[
            identity
        ]
    )
    monkeypatch.setattr(module, "read_research_object", object_reader)
    settle = Mock()
    monkeypatch.setattr(module, "settle_budget", settle)
    body = {
        "start_id": str(source.start_id),
        "logical_step": 0,
        "evaluated_source_id": str(source.id),
        "previous_adopted_source_id": None,
        "adopted_source_id": str(source.id),
        "continuation_source_id": str(source.id),
        "adopted_new": True,
        "boundary": "returned",
        "next_logical_step": None,
    }
    return SimpleNamespace(
        fence=fence,
        source=source,
        snapshot=snapshot,
        inp=inp,
        evaluation=evaluation,
        upstream=upstream,
        doc=doc,
        feedback_obj=feedback_obj,
        session=session,
        reader=reader,
        feedback_reader=feedback_reader,
        response_reader=response_reader,
        calls=calls,
        members=members,
        observe=observe,
        confirm=confirm,
        published=published,
        object_reader=object_reader,
        settle=settle,
        body=body,
        scopes=scopes,
    )


def retain(f):
    return module.retain_feedback(
        object(),
        f.fence,
        object(),
        start_id=f.source.start_id,
        source_id=f.source.id,
        evaluation_id=f.evaluation.id,
        upstream_feedback=f.upstream,
        settings=object(),
    )


def finish(f):
    return module.finish_generation(
        object(), f.fence, object(), body=f.body, settings=object()
    )


def test_feedback_retains_real_typed_snapshot_and_upstream(finalizer):
    f = finalizer
    retain(f)
    kind, raw, _ = f.published[0]
    doc = parse_document(kind, raw)
    assert doc == f.doc
    f.response_reader.assert_called_once()
    f.session.add.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        "source",
        "generation",
        "missing_call",
        "extra_member",
        "decision",
        "integer_decision",
        "feedback_shape",
        "feedback_bound",
    ],
)
def test_feedback_mismatch_never_publishes(finalizer, change):
    f = finalizer
    if change in {"source", "generation"}:
        field = "source_id" if change == "source" else "generation_start_id"
        f.reader.return_value = (replace(f.snapshot, **{field: uuid4()}), f.inp, True)
    elif change == "missing_call":
        f.calls.clear()
    elif change == "extra_member":
        f.members.append(object())
    elif change in {"decision", "integer_decision"}:
        f.response_reader.return_value = (
            object(),
            RDChatResponse(
                content=json.dumps(
                    {"final_decision": False if change == "decision" else 1}
                ),
                finish_reason="stop",
            ),
        )
    elif change == "feedback_shape":
        f.upstream["extra"] = True
    else:
        f.upstream["execution"] = "x" * 32769
    with pytest.raises((RPCError, ValueError)):
        retain(f)
    assert not f.published
    f.session.add.assert_not_called()


def test_finalization_fresh_reads_actual_artifacts_before_acceptance(finalizer):
    f = finalizer
    result = finish(f)
    assert result is not None
    assert f.reader.call_count == f.feedback_reader.call_count == 2
    assert f.object_reader.call_count == 2
    assert [k for k, _, _ in f.published] == [
        "factor_generation_proposal",
        "factor_adoption_receipt",
    ]
    assert f.scopes == ["begin", "commit", "begin", "commit"]
    assert f.session.add.call_count == 2
    outcomes = [call.args[0] for call in f.session.add.call_args_list]
    assert outcomes[0].status == "valid" and outcomes[1].outcome == "accepted"
    assert f.settle.call_args.kwargs["outcome"] == "conservative_unknown"
    assert f.settle.call_args.kwargs["charge"].mutation_slots == 1


@pytest.mark.parametrize(
    "change",
    [
        "objective",
        "feedback_objective",
        "upstream_decision",
        "source",
        "evaluation",
        "input",
        "boundary",
        "step",
    ],
)
def test_preobservation_mismatch_no_accepted_writes(finalizer, change):
    f = finalizer
    if change == "objective":
        f.reader.return_value = (f.snapshot, f.inp, False)
    elif change == "feedback_objective":
        f.feedback_reader.return_value = (
            f.feedback_obj,
            f.doc.model_copy(update={"objective_passed": False}),
        )
    elif change == "upstream_decision":
        f.feedback_reader.return_value = (
            f.feedback_obj,
            f.doc.model_copy(update={"upstream_feedback": '{"final_decision":false}'}),
        )
    elif change in {"source", "evaluation", "input"}:
        key = {
            "source": "source_id",
            "evaluation": "evaluation_id",
            "input": "input_sha256",
        }[change]
        f.feedback_reader.return_value = (
            f.feedback_obj,
            f.doc.model_copy(update={key: "f" * 64 if change == "input" else uuid4()}),
        )
    else:
        f.body["boundary" if change == "boundary" else "logical_step"] = (
            "advanced" if change == "boundary" else True
        )
    with pytest.raises((RPCError, ValueError)):
        finish(f)
    f.observe.assert_not_called()
    f.session.add.assert_not_called()
    f.settle.assert_not_called()
    assert not f.published


@pytest.mark.parametrize("change", ["objective", "feedback", "object_bytes"])
def test_final_fresh_reread_failure_no_result_or_charge(finalizer, change):
    f = finalizer
    if change == "objective":
        f.reader.side_effect = [(f.snapshot, f.inp, True), (f.snapshot, f.inp, False)]
    elif change == "feedback":
        f.feedback_reader.side_effect = [
            (f.feedback_obj, f.doc),
            (SimpleNamespace(id=uuid4()), f.doc),
        ]
    else:
        f.object_reader.side_effect = lambda *a: b"{}"
    with pytest.raises(RPCError):
        finish(f)
    assert len(f.published) == 2  # Prior durable evidence remains, no fake rollback.
    assert f.scopes[-1] == "rollback"
    f.session.add.assert_not_called()
    f.settle.assert_not_called()
