"""Seed declaration resolver boundary doubles, not real controller or DB grants."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import oe_structured_candidate_services as service
from backend.app.oe_structured_identity import decode_spec
from backend.app.oe_structured_models import OEStructuredCandidate
from packages.research_runtime.process_contracts import ProcessError
from runtimes.openevolve_structured_host.peer import ControllerBinding
from tests.openevolve.test_structured_persisted_identity import graph
from tests.structured_controller_runtime.fixtures import records


def setup(monkeypatch, tmp_path):
    session, run, _, _, candidate, rows = graph()
    config = decode_spec(session, run)[1]
    rows[OEStructuredCandidate, candidate.id] = candidate
    _, identity, child, _ = records(tmp_path)
    peer = NS(
        binding=ControllerBinding(
            identity.container_id,
            identity.admission_sha256,
            identity.leader,
            child.controller,
        )
    )
    fence = NS(
        **{
            k: getattr(candidate, k)
            for k in ("owner_id", "run_id", "attempt_id", "generation")
        }
    )
    context = NS(config=config, bootstrap=NS(spec=config.search))
    calls = []
    monkeypatch.setattr(
        service, "require_neutral_transaction", lambda *a, **kw: (run, None)
    )
    monkeypatch.setattr(service, "require_native_storage_guard", lambda *a: None)
    monkeypatch.setattr(
        service,
        "require_controller_context",
        lambda *a, **kw: calls.append(kw) or context,
    )
    return NS(
        session=session,
        run=run,
        candidate=candidate,
        peer=peer,
        fence=fence,
        context=context,
        calls=calls,
        settings=NS(storage_root=tmp_path),
    )


def read(c):
    return service.require_candidate_execution(
        c.session, c.fence, c.candidate.id, c.settings, peer=c.peer
    )


def test_seed_comes_from_actual_persisted_user_program_not_sdk_placeholder(
    monkeypatch, tmp_path
):
    c = setup(monkeypatch, tmp_path)
    value = read(c)
    assert value.provider_exchange_id is None and value.parent_candidate_id is None
    assert value.candidate_bytes == c.candidate.candidate_canonical.encode()
    assert len(c.calls) == 2 and all(call["peer"] is c.peer for call in c.calls)


@pytest.mark.parametrize("field", ["owner_id", "run_id", "attempt_id", "generation"])
def test_cross_scope_cannot_reach_controller(monkeypatch, tmp_path, field):
    c = setup(monkeypatch, tmp_path)
    setattr(c.candidate, field, 2 if field == "generation" else uuid4())
    with pytest.raises(ProcessError):
        read(c)
    assert not c.calls


def test_worker_or_arbitrary_binding_cannot_launch_seed(monkeypatch, tmp_path):
    c = setup(monkeypatch, tmp_path)
    c.peer.binding = object()
    with pytest.raises(ProcessError):
        read(c)


def test_post_io_frozen_binding_drift_rejected(monkeypatch, tmp_path):
    c = setup(monkeypatch, tmp_path)
    values = iter([c.context, NS(config=None, bootstrap=None)])
    monkeypatch.setattr(
        service, "require_controller_context", lambda *a, **kw: next(values)
    )
    with pytest.raises(ProcessError):
        read(c)


def test_missing_guard_cannot_be_replaced_by_candidate_hash(monkeypatch, tmp_path):
    c = setup(monkeypatch, tmp_path)

    def missing(*a):
        raise ValueError("guard missing")

    monkeypatch.setattr(service, "require_native_storage_guard", missing)
    with pytest.raises(ValueError, match="guard missing"):
        read(c)
    assert not c.calls
