"""Real wire codecs and transaction doubles; not actual PG/Docker acceptance."""

from contextlib import contextmanager, nullcontext
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import oe_structured_evaluation_services as service
from packages.factors.contracts import digest
from packages.openevolve_structured.objective import StructuredMeasurement
from packages.research_runtime.process_contracts import ProcessError


def arranged(monkeypatch):
    fence = NS(owner_id=uuid4(), run_id=uuid4(), attempt_id=uuid4(), generation=1)
    row = NS(
        **vars(fence),
        id=uuid4(),
        epoch_id=uuid4(),
        upstream_program_id=uuid4(),
        ordinal=0,
    )
    eid, aid = uuid4(), uuid4()
    authority = NS(
        candidate_id=row.id,
        candidate_sha256="a" * 64,
        program_sha256="b" * 64,
        config=NS(search=NS(execution_binding_sha256="c" * 64)),
    )
    measurement = StructuredMeasurement(
        execution_id=eid,
        structured_strategy_version_id=uuid4(),
        snapshot_id=uuid4(),
        strategy_input_sha256="d" * 64,
        market_sha256="e" * 64,
        result_sha256="f" * 64,
        objective_sha256="1" * 64,
        total_return_pct=2.0,
        max_drawdown_pct=1.0,
        closed_trade_count=0,
        eligible=True,
        combined_score=1.0,
        reason="eligible",
    )
    outcome = NS(
        **vars(fence),
        id=uuid4(),
        candidate_id=row.id,
        result_sha256=measurement.result_sha256,
        measurement_sha256=digest(measurement),
    )
    verified = NS(
        candidate_id=row.id,
        execution_id=eid,
        result_sha256=measurement.result_sha256,
        measurement=measurement,
    )
    state = NS(
        outcome=None,
        active=False,
        native_locked=False,
        commits=0,
        commit_fails=False,
        engine_fails=False,
    )
    calls = []
    peer = object()  # explicit boundary double, never production Peer authority
    session = NS(get=lambda *a: row, scalar=lambda *a: state.outcome)
    factory, settings = NS(kw={"bind": object()}), NS(storage_root="/not-used")

    @contextmanager
    def transaction(*a, **kw):
        assert not state.active
        assert kw == {"resource_scope": "factor_numeric"}
        state.active = True
        calls.append("begin")
        try:
            yield session
            if state.commit_fails:
                raise ProcessError("commit_failed")
            calls.append("commit")
            state.commits += 1
        finally:
            state.active = False

    monkeypatch.setattr(service, "neutral_transaction", transaction)
    monkeypatch.setattr(service, "shared_lifecycle", lambda *a: nullcontext())

    @contextmanager
    def native(*a):
        assert not state.native_locked
        state.native_locked = True
        try:
            yield
        finally:
            state.native_locked = False

    monkeypatch.setattr(service, "native_storage_guard", native)

    def auth(*a, **kw):
        assert kw == {"peer": peer}
        return authority

    monkeypatch.setattr(service, "require_candidate_execution", auth)
    monkeypatch.setattr(
        service, "require_execution", lambda *a, **kw: NS(candidate_id=row.id)
    )
    monkeypatch.setattr(service, "register_candidate", lambda *a, **kw: (row, eid))

    def engine(*a, **kw):
        assert kw == {"peer": peer}
        assert not state.active and not state.native_locked and state.commits >= 1
        calls.append("engine")
        if state.engine_fails:
            raise ProcessError("uncertain_original_unit")
        return aid

    def publish(*a, **kw):
        assert state.active and a[3] == aid and kw == {"peer": peer}
        calls.append("publish")
        state.outcome = outcome
        return verified

    def read(*a, **kw):
        assert state.active and state.outcome is not None and kw == {"peer": peer}
        calls.append("physical_read")
        return verified

    monkeypatch.setattr(service, "execute_candidate_engine", engine)
    monkeypatch.setattr(service, "persist_candidate_engine_outcome", publish)
    monkeypatch.setattr(service, "read_candidate_engine_outcome", read)
    key = b"x" * 32

    def grant():
        return service.register_evaluation(
            factory,
            fence,
            row.epoch_id,
            row.upstream_program_id,
            b"source-double",
            settings,
            peer=peer,
            ticket_key=key,
        )

    def execute(g):
        return service.execute_evaluation(
            factory, fence, g, settings, peer=peer, ticket_key=key
        )

    return NS(**locals())


def test_declaration_commit_then_engine_then_stopped_publication_commit(monkeypatch):
    c = arranged(monkeypatch)
    grant = c.grant()
    assert c.state.commits == 1 and "engine" not in c.calls
    response = c.execute(grant)
    assert response.objective_receipt_id == c.outcome.id
    assert (
        response.combined_score == 1.0
        and response.feedback == service.measurement_feedback(c.measurement)
    )
    assert c.calls == [
        "begin",
        "commit",
        "begin",
        "commit",
        "engine",
        "begin",
        "publish",
        "physical_read",
        "commit",
    ]
    replay = c.execute(grant)
    assert replay == response and c.calls.count("engine") == 1
    assert c.calls.count("physical_read") == 2


@pytest.mark.parametrize(
    "field", ["ticket", "epoch_id", "upstream_program_id", "candidate_sha256", "phase"]
)
def test_grant_wire_cannot_rebind_actual_scope_or_candidate(monkeypatch, field):
    c = arranged(monkeypatch)
    grant = c.grant()
    changed = (
        "mutation"
        if field == "phase"
        else uuid4()
        if field.endswith("_id")
        else "0" * 64
    )
    with pytest.raises(ProcessError, match="evaluation_grant"):
        c.execute(grant.model_copy(update={field: changed}))
    assert "engine" not in c.calls and c.state.outcome is None


def test_failed_engine_never_invents_score_or_publishes(monkeypatch):
    c = arranged(monkeypatch)
    grant = c.grant()
    c.state.engine_fails = True
    with pytest.raises(ProcessError, match="uncertain_original_unit"):
        c.execute(grant)
    assert c.state.outcome is None and "publish" not in c.calls


def test_failed_declaration_commit_returns_no_grant(monkeypatch):
    c = arranged(monkeypatch)
    c.state.commit_fails = True
    with pytest.raises(ProcessError, match="commit_failed"):
        c.grant()
    assert c.state.commits == 0 and "engine" not in c.calls


def test_cached_metadata_still_requires_physical_reader(monkeypatch):
    c = arranged(monkeypatch)
    grant = c.grant()
    c.state.outcome = c.outcome

    def missing(*a, **kw):
        raise ProcessError("retained_output_missing")

    monkeypatch.setattr(service, "read_candidate_engine_outcome", missing)
    with pytest.raises(ProcessError, match="retained_output_missing"):
        c.execute(grant)
    assert "engine" not in c.calls


def test_failed_result_commit_has_no_success_ack(monkeypatch):
    c = arranged(monkeypatch)
    grant = c.grant()
    old = service.persist_candidate_engine_outcome

    def publish(*a, **kw):
        result = old(*a, **kw)
        c.state.commit_fails = True
        return result

    monkeypatch.setattr(service, "persist_candidate_engine_outcome", publish)
    with pytest.raises(ProcessError, match="commit_failed"):
        c.execute(grant)
    assert c.calls.count("engine") == 1 and c.state.commits == 2
