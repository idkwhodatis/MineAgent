"""Configured issuer guards and exact input intent; no Docker/PG claim."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_rdagent_configured_inputs import case  # noqa: F401
from test_rdagent_configured_source import retained  # noqa: F401
from test_rdagent_configured_source_service import setup_source  # noqa: F401

from backend.app.rdagent_configured_inputs import resolve_candidate_inputs
from packages.factors.contracts import digest
from packages.research_runtime.process_contracts import ProcessError
from packages.research_runtime.process_declarations_v2 import NoIPC
from workers.research import rdagent_numeric as worker


@pytest.mark.parametrize(
    "versions",
    [
        [],
        ["021_execution_snapshots"],
        ["023_future"],
        ["022_factor_analysis", "021_execution_snapshots"],
    ],
)
def test_exact_revision_only(versions):
    session = SimpleNamespace(scalars=lambda sql: SimpleNamespace(all=lambda: versions))
    with pytest.raises(ProcessError, match="configured_numeric_schema_closed"):
        worker.require_configured_revision(session)


@pytest.mark.parametrize(
    "revision",
    [
        "022_factor_analysis",
        "023_rd_submissions",
        "024_openevolve_initial",
        "025_structured_factor_strategy",
    ],
)
def test_exact_revision(revision):
    session = SimpleNamespace(
        scalars=lambda sql: SimpleNamespace(all=lambda: [revision])
    )
    worker.require_configured_revision(session)


@pytest.mark.parametrize("callback", [None, False, object()])
def test_no_detached_or_absent_controller(callback):
    with pytest.raises(ProcessError, match="configured_controller_required"):
        worker._authorize(object(), object(), callback)


def test_peer_rechecked_not_merely_callback_return(monkeypatch):
    peer, session, fence = object(), object(), object()
    calls = []
    monkeypatch.setattr(
        worker, "require_rd_controller", lambda *args: calls.append(args)
    )
    assert worker._authorize(session, fence, lambda s, f: peer) is peer
    assert calls == [(session, fence, peer)]


def test_lost_peer_fails(monkeypatch):
    def lost(*args):
        raise ProcessError("controller_gone")

    monkeypatch.setattr(worker, "require_rd_controller", lost)
    with pytest.raises(ProcessError, match="controller_gone"):
        worker._authorize(object(), object(), lambda *args: object())


def test_exact_retained_input_intent(case, monkeypatch):  # noqa: F811
    s = case
    resolved = resolve_candidate_inputs(
        s.session,
        s.fence,
        evaluation_id=s.evaluation.id,
        cutoff_ordinal=0,
        settings=s.settings,
    )
    reservations, persisted = [], []
    reservation = SimpleNamespace(id=uuid4())

    def reserve(session, fence, **kw):
        assert (session, fence) == (s.session, s.fence)
        reservations.append(kw)
        return reservation

    monkeypatch.setattr(worker, "reserve_budget", reserve)
    monkeypatch.setattr(
        worker, "persist_numeric_intent", lambda *args: persisted.append(args)
    )
    cutoff_id = uuid4()
    intent = worker._intent(
        s.session,
        s.fence,
        role="factor_cutoff_candidate",
        profile=resolved.prefix.candidate_profile,
        image=resolved.prefix.candidate_image,
        logical_id=cutoff_id,
        binding=resolved.binding,
        settings=s.settings,
    )
    assert intent.id == intent.admission.id
    assert intent.inputs == resolved.binding
    assert intent.admission.input_sha256 == digest(
        resolved.binding.model_dump(mode="json")
    )
    assert intent.admission.logical_operation_id == cutoff_id
    assert isinstance(intent.admission.ipc, NoIPC)
    assert intent.admission.ipc.ticket_sha256 is None
    assert intent.not_after - intent.issued_at == timedelta(seconds=8)
    assert reservations[0]["settings"] is s.settings
    assert reservations[0]["amounts"].storage_bytes == 67_174_400
    assert reservations[0]["amounts"].process_slots == 1
    assert reservations[0]["amounts"].wall_milliseconds == 60_000
    assert persisted == [(s.session, s.fence, intent)]


def test_normalizer_keeps_independent_intent_identity(monkeypatch):
    from datetime import UTC, datetime

    from packages.rdagent_adapter.numeric_profile import NORMALIZER_LIMITS
    from packages.research_runtime.numeric_process_journal import (
        NumericInput,
        NumericInputFile,
    )

    fence = SimpleNamespace(
        owner_id=uuid4(), run_id=uuid4(), attempt_id=uuid4(), generation=1
    )
    binding = NumericInput(
        schema_version="mineagent.numeric-process-input.v2",
        source_object_id=uuid4(),
        source_sha256="a" * 64,
        prefix_sha256="b" * 64,
        cutoff=datetime.now(UTC),
        files=tuple(
            NumericInputFile(destination=path, sha256="a" * 64, size_bytes=1)
            for path in sorted(NORMALIZER_LIMITS)
        ),
    )
    reservation = SimpleNamespace(id=uuid4())
    persisted = []
    monkeypatch.setattr(worker, "reserve_budget", lambda *a, **kw: reservation)
    monkeypatch.setattr(
        worker, "persist_numeric_intent", lambda *args: persisted.append(args)
    )
    session, settings = object(), object()
    intent = worker._intent(
        session,
        fence,
        role="factor_normalizer",
        profile="mineagent.factor-numeric-hdf.v1",
        image="sha256:" + "a" * 64,
        logical_id=uuid4(),
        binding=binding,
        settings=settings,
    )
    assert intent.id != intent.admission.id
    assert intent.inputs == binding
    assert intent.not_after - intent.issued_at == timedelta(seconds=8)
    assert persisted == [(session, fence, intent)]
