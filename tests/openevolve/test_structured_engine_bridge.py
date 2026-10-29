"""Boundary unit doubles, not Docker, PostgreSQL, or actual accounting acceptance."""

import hashlib
from dataclasses import replace
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import oe_structured_engine_process as process
from backend.app import oe_structured_engine_results as results
from backend.app import oe_structured_engine_services as service
from packages.research_runtime.process_contracts import ProcessError
from packages.structured_factor_strategy.wire import write_input
from tests.structured.test_operator import declared_input

PEER = object()  # Explicit boundary double, never a live authenticated peer.


def fixture():
    value = declared_input()
    raw = write_input(value)
    market = b"market grammar only"
    pins = service.ResolvedOEExecution(
        value.execution_id,
        uuid4(),
        value.structured_strategy_version_id,
        value.snapshot.snapshot_id,
        "a" * 64,
        "b" * 64,
        "c" * 64,
        hashlib.sha256(raw).hexdigest(),
        len(raw),
        hashlib.sha256(market).hexdigest(),
        len(market),
        "sha256:" + "d" * 64,
        "sha256:" + "e" * 64,
        "f" * 64,
        None,
    )
    return (pins, raw, market)


def test_input_requires_fresh_equal_provenance_after_actual_codec(monkeypatch):
    pins, raw, market = fixture()
    calls = []
    monkeypatch.setattr(
        service, "require_execution", lambda *a, **kwargs: calls.append(1) or pins
    )
    monkeypatch.setattr(
        service, "resolve_execution_input", lambda *a, **kwargs: (raw, market)
    )
    assert service.execution_input(None, None, pins.execution_id, None, peer=PEER) == (
        raw,
        market,
    )
    assert len(calls) == 2


@pytest.mark.parametrize(
    "field", ["candidate_sha256", "config_sha256", "engine_image_id"]
)
def test_post_io_authority_drift_rejected(monkeypatch, field):
    pins, raw, market = fixture()
    values = iter((pins, replace(pins, **{field: "0" * 64})))
    monkeypatch.setattr(service, "require_execution", lambda *a, **kwargs: next(values))
    monkeypatch.setattr(
        service, "resolve_execution_input", lambda *a, **kwargs: (raw, market)
    )
    with pytest.raises(ProcessError, match="input_drift"):
        service.execution_input(None, None, pins.execution_id, None, peer=PEER)


@pytest.mark.parametrize(
    "field", ["snapshot_id", "strategy_input_sha256", "market_size_bytes"]
)
def test_retained_bytes_cannot_be_rebound(monkeypatch, field):
    pins, raw, market = fixture()
    changed = (
        uuid4() if field.endswith("_id") else 1 if field.endswith("bytes") else "0" * 64
    )
    monkeypatch.setattr(
        service,
        "require_execution",
        lambda *a, **kwargs: replace(pins, **{field: changed}),
    )
    monkeypatch.setattr(
        service, "resolve_execution_input", lambda *a, **kwargs: (raw, market)
    )
    with pytest.raises(ProcessError):
        service.execution_input(None, None, pins.execution_id, None, peer=PEER)


def test_cross_owner_execution_rejected_before_candidate_resolution(monkeypatch):
    monkeypatch.setattr(service, "require_schema", lambda *a, **kwargs: None)
    monkeypatch.setattr(
        service,
        "candidate_authority",
        lambda *a, **kwargs: pytest.fail("authority read"),
    )
    fence = NS(**{k: uuid4() for k in service.SCOPE})
    row = NS(**vars(fence))
    row.owner_id = uuid4()
    with pytest.raises(ProcessError):
        service.require_execution(
            NS(get=lambda *a, **kwargs: row), fence, uuid4(), None, peer=PEER
        )


def test_existing_admission_never_retried():
    with pytest.raises(ProcessError, match="already_admitted"):
        process._no_prior_process(NS(scalar=lambda *a, **kwargs: uuid4()), uuid4())


def test_missing_current_authority_prevents_physical_result_read(monkeypatch):

    def reject(*args, **kwargs):
        raise ProcessError("provider_not_stopped")

    monkeypatch.setattr(results, "require_execution", reject)
    monkeypatch.setattr(
        results, "read_structured_output", lambda *a, **k: pytest.fail("physical read")
    )
    with pytest.raises(ProcessError, match="provider_not_stopped"):
        results._read(None, None, uuid4(), uuid4(), None, peer=PEER)


def test_absent_positive_engine_stop_never_projects_measurement(monkeypatch):
    pins, _, _ = fixture()
    monkeypatch.setattr(results, "require_execution", lambda *a, **kwargs: pins)

    def reject(*args, **kwargs):
        raise ProcessError("structured_process_stop")

    monkeypatch.setattr(results, "read_structured_output", reject)
    monkeypatch.setattr(
        results, "project_result", lambda *a, **kwargs: pytest.fail("fabricated score")
    )
    with pytest.raises(ProcessError, match="structured_process_stop"):
        results._read(
            None, None, pins.execution_id, uuid4(), NS(storage_root="unused"), peer=PEER
        )


def publication_fixture(monkeypatch):
    from backend.app import research_broker_services as broker
    from packages.research_runtime.budget_contracts import BudgetVector

    pins, _, _ = fixture()
    pins = replace(pins, objective={"unit_double": True})
    fence = NS(owner_id=uuid4(), run_id=uuid4(), attempt_id=uuid4(), generation=1)
    admission_id = uuid4()
    reservation = NS(
        id=uuid4(), state="reserved", amounts=process.PROCESS_AMOUNT.model_dump()
    )
    receipt = NS(
        admission=NS(id=admission_id, reservation_id=reservation.id),
        stop_receipt_id=uuid4(),
        stop_receipt_hash="a" * 64,
        extraction_receipt_id=uuid4(),
        extraction_receipt_hash="b" * 64,
        result_bytes=b"{}",
        sha256=hashlib.sha256(b"{}").hexdigest(),
        size_bytes=2,
    )
    result, measurement = (object(), {"unit_double": "not native execution"})
    monkeypatch.setattr(
        results, "_read", lambda *a, **kwargs: (pins, receipt, result, measurement)
    )
    events, rows = ([], [])
    session = NS(
        add=lambda row: (events.append("add"), rows.append(row)),
        flush=lambda: events.append("flush"),
        get=lambda *a, **kwargs: reservation,
    )
    monkeypatch.setattr(
        results, "_outcome", lambda *a, **kwargs: rows[0] if rows else None
    )

    def settle(_session, _fence, reservation_id, *, charge, outcome):
        assert reservation_id == reservation.id
        assert charge == BudgetVector.model_validate(reservation.amounts)
        assert outcome == "conservative_unknown"
        events.append("settle")
        reservation.state = "settled"

    monkeypatch.setattr(broker, "settle_budget", settle)
    return NS(**locals())


def test_outcome_exact_model_fields_and_full_charge_no_commit(monkeypatch):
    f = publication_fixture(monkeypatch)
    value = results.persist_candidate_engine_outcome(
        f.session, f.fence, f.pins.execution_id, f.admission_id, None, peer=PEER
    )
    assert f.events == ["add", "settle", "flush"]
    from backend.app.oe_structured_models import OEStructuredEngineOutcome

    assert isinstance(f.rows[0], OEStructuredEngineOutcome)
    assert f.rows[0].stop_receipt_kind == "stop"
    assert f.rows[0].extraction_receipt_kind == "extraction"
    assert value.result is f.result
    assert value.measurement is f.measurement
    f.events.clear()
    assert (
        results.persist_candidate_engine_outcome(
            f.session, f.fence, f.pins.execution_id, f.admission_id, None, peer=PEER
        )
        == value
    )
    assert not f.events


@pytest.mark.parametrize(
    "field", ["measurement_sha256", "stop_receipt_sha256", "result_canonical"]
)
def test_persisted_projection_tamper_is_not_cached_authority(monkeypatch, field):
    f = publication_fixture(monkeypatch)
    results.persist_candidate_engine_outcome(
        f.session, f.fence, f.pins.execution_id, f.admission_id, None, peer=PEER
    )
    setattr(f.rows[0], field, "changed")
    with pytest.raises(ProcessError, match="outcome_drift"):
        results.read_candidate_engine_outcome(
            f.session, f.fence, f.pins.execution_id, None, peer=PEER
        )


def test_unsettled_cached_outcome_rejected(monkeypatch):
    f = publication_fixture(monkeypatch)
    results.persist_candidate_engine_outcome(
        f.session, f.fence, f.pins.execution_id, f.admission_id, None, peer=PEER
    )
    f.reservation.state = "uncertain"
    with pytest.raises(ProcessError, match="unsettled"):
        results.read_candidate_engine_outcome(
            f.session, f.fence, f.pins.execution_id, None, peer=PEER
        )


@pytest.mark.parametrize(
    "function",
    [
        service.prepare_execution,
        service.require_execution,
        service.execution_input,
        process.execute_candidate_engine,
        results.read_candidate_engine_outcome,
        results.persist_candidate_engine_outcome,
    ],
)
def test_live_peer_is_explicit_required_keyword_not_serialized_flag(function):
    from inspect import Parameter, signature

    parameter = signature(function).parameters["peer"]
    assert parameter.kind is Parameter.KEYWORD_ONLY
    assert parameter.default is Parameter.empty


def test_authoritative_resolver_receives_exact_live_peer_reference(monkeypatch):
    import sys

    seen = []
    module = NS(
        require_candidate_execution=lambda *args, **kwargs: (
            seen.append(kwargs["peer"]) or "resolved"
        )
    )
    monkeypatch.setitem(
        sys.modules, "backend.app.oe_structured_candidate_services", module
    )
    assert (
        service.candidate_authority(None, None, uuid4(), None, peer=PEER) == "resolved"
    )
    assert seen == [PEER]


def test_current_worker_reads_completed_parent_without_reusing_old_peer(monkeypatch):
    f = publication_fixture(monkeypatch)
    results.persist_candidate_engine_outcome(
        f.session, f.fence, f.pins.execution_id, f.admission_id, None, peer=PEER
    )
    intent_id = uuid4()
    calls = []

    def parent(*args, peer, current_intent_id):
        assert args[2] == f.pins.candidate_id
        assert peer is PEER and current_intent_id == intent_id
        calls.append("parent_guard")
        return f.pins

    monkeypatch.setattr(results, "require_parent_execution", parent)
    monkeypatch.setattr(
        results,
        "require_execution",
        lambda *a, **k: pytest.fail("parent launch eligibility"),
    )
    monkeypatch.setattr(
        results,
        "_read_physical",
        lambda *a: (f.pins, f.receipt, f.result, f.measurement),
    )
    events = list(f.events)
    value = results.read_parent_engine_outcome(
        f.session,
        f.fence,
        f.pins.candidate_id,
        None,
        peer=PEER,
        current_intent_id=intent_id,
    )
    assert value.result is f.result
    assert calls == ["parent_guard", "parent_guard"]
    assert f.events == events


def test_unsettled_parent_cannot_feed_current_candidate(monkeypatch):
    f = publication_fixture(monkeypatch)
    results.persist_candidate_engine_outcome(
        f.session, f.fence, f.pins.execution_id, f.admission_id, None, peer=PEER
    )
    f.reservation.state = "uncertain"
    monkeypatch.setattr(results, "require_parent_execution", lambda *a, **k: f.pins)
    monkeypatch.setattr(
        results,
        "_read_physical",
        lambda *a: (f.pins, f.receipt, f.result, f.measurement),
    )
    with pytest.raises(ProcessError, match="parent_unsettled"):
        results.read_parent_engine_outcome(
            f.session,
            f.fence,
            f.pins.candidate_id,
            None,
            peer=PEER,
            current_intent_id=uuid4(),
        )


@pytest.mark.parametrize("settled,drift", [(True, False), (False, False), (True, True)])
def test_presampling_controller_member_read_has_no_fake_intent(
    monkeypatch, settled, drift
):
    f = publication_fixture(monkeypatch)
    results.persist_candidate_engine_outcome(
        f.session, f.fence, f.pins.execution_id, f.admission_id, None, peer=PEER
    )
    control_id = uuid4()
    calls = []

    def guard(*args, peer, current_control_id):
        assert peer is PEER and current_control_id == control_id
        assert args[2] == f.pins.candidate_id
        calls.append("guard")
        return None if drift and len(calls) == 2 else f.pins

    monkeypatch.setattr(service, "require_sampling_execution", guard)
    monkeypatch.setattr(
        results, "require_execution", lambda *a, **k: pytest.fail("launch authority")
    )
    monkeypatch.setattr(
        results,
        "_read_physical",
        lambda *a: (f.pins, f.receipt, f.result, f.measurement),
    )
    if not settled:
        f.reservation.state = "uncertain"
    events = list(f.events)
    if not settled or drift:
        with pytest.raises(ProcessError, match="sampling_"):
            results.read_sampling_engine_outcome(
                f.session,
                f.fence,
                f.pins.candidate_id,
                None,
                peer=PEER,
                current_control_id=control_id,
            )
    else:
        value = results.read_sampling_engine_outcome(
            f.session,
            f.fence,
            f.pins.candidate_id,
            None,
            peer=PEER,
            current_control_id=control_id,
        )
        assert value.result is f.result and calls == ["guard", "guard"]
    assert f.events == events


@pytest.mark.parametrize(
    "function",
    [service.require_sampling_execution, results.read_sampling_engine_outcome],
)
def test_presampling_cursor_and_peer_are_required_keywords(function):
    from inspect import Parameter, signature

    for name in ("peer", "current_control_id"):
        parameter = signature(function).parameters[name]
        assert parameter.kind is Parameter.KEYWORD_ONLY
        assert parameter.default is Parameter.empty


@pytest.mark.parametrize("settled,drift", [(True, False), (False, False), (True, True)])
def test_terminal_controller_member_read_has_no_fake_intent(
    monkeypatch, settled, drift
):
    f = publication_fixture(monkeypatch)
    results.persist_candidate_engine_outcome(
        f.session, f.fence, f.pins.execution_id, f.admission_id, None, peer=PEER
    )
    control_id = uuid4()
    calls = []

    def guard(*args, peer, current_control_id):
        assert peer is PEER and current_control_id == control_id
        assert args[2] == f.pins.candidate_id
        calls.append("guard")
        return None if drift and len(calls) == 2 else f.pins

    monkeypatch.setattr(service, "require_terminal_execution", guard)
    monkeypatch.setattr(
        results, "require_execution", lambda *a, **k: pytest.fail("launch authority")
    )
    monkeypatch.setattr(
        results,
        "_read_physical",
        lambda *a: (f.pins, f.receipt, f.result, f.measurement),
    )
    if not settled:
        f.reservation.state = "uncertain"
    events = list(f.events)
    if not settled or drift:
        with pytest.raises(ProcessError, match="terminal_"):
            results.read_terminal_engine_outcome(
                f.session,
                f.fence,
                f.pins.candidate_id,
                None,
                peer=PEER,
                current_control_id=control_id,
            )
    else:
        value = results.read_terminal_engine_outcome(
            f.session,
            f.fence,
            f.pins.candidate_id,
            None,
            peer=PEER,
            current_control_id=control_id,
        )
        assert value.result is f.result and calls == ["guard", "guard"]
    assert f.events == events


@pytest.mark.parametrize(
    "function",
    [service.require_terminal_execution, results.read_terminal_engine_outcome],
)
def test_terminal_cursor_and_peer_are_required_keywords(function):
    from inspect import Parameter, signature

    for name in ("peer", "current_control_id"):
        parameter = signature(function).parameters[name]
        assert parameter.kind is Parameter.KEYWORD_ONLY
        assert parameter.default is Parameter.empty
