"""Actual socket objects and journal DTOs; daemon/auth/DB boundaries are doubled."""

import socket
import time
from dataclasses import asdict
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import oe_structured_channel_auth as service
from backend.app import oe_structured_controller_context as context
from packages.factors.contracts import canonical_bytes, digest
from packages.openevolve_structured.provider_bootstrap import (
    ProviderStructuredBootstrap,
)
from packages.research_runtime.process_contracts import ProcessError
from runtimes.openevolve_structured_host.peer import WorkerBinding
from runtimes.openevolve_structured_host.supervisor import RunningController
from runtimes.rdagent_operational.peer import KernelIdentity
from tests.openevolve.test_structured_controller_context import fixture


@pytest.fixture
def graph(monkeypatch, tmp_path):
    c = fixture(monkeypatch, tmp_path)
    launch, identity, child, _ = context.read_controller_journal(None, None)
    bootstrap = ProviderStructuredBootstrap.model_validate_json(
        c.epoch.bootstrap_canonical
    )
    receipts = {r.kind: r for r in c.receipts}
    monkeypatch.setattr(
        service,
        "expected_controller_bootstrap",
        lambda *a: (c.epoch, bootstrap, b"seed-double"),
    )
    monkeypatch.setattr(
        service,
        "_persisted",
        lambda *a: (c.peer.admission, c.state, receipts, launch.intent),
    )
    monkeypatch.setattr(
        service,
        "read_controller_journal",
        lambda *a, **kw: (launch, identity, child, None),
    )
    running = RunningController(
        NS(image_id=c.peer.admission.image_id),
        tmp_path,
        launch,
        identity,
        time.monotonic() + 50,
        None,
        None,
        None,
    )
    running.child = child
    channels = socket.socketpair()
    calls = []

    def authenticate(channel, admission, **kwargs):
        assert channel is channels[0] and admission == c.peer.admission
        assert kwargs["context"] is running.observer_context
        c.peer.binding = kwargs["binding"]
        calls.append(kwargs)
        return c.peer

    monkeypatch.setattr(service, "authenticate", authenticate)

    def call(**kwargs):
        return service.authenticate_channel(
            c.session,
            c.fence,
            c.epoch.id,
            c.settings,
            channels[0],
            running=running,
            process_ticket="a" * 64,
            **kwargs,
        )

    yield NS(
        c=c,
        launch=launch,
        identity=identity,
        child=child,
        receipts=receipts,
        running=running,
        calls=calls,
        call=call,
    )
    for channel in channels:
        channel.close()


def test_actual_channel_retains_original_observer_deadline(graph):
    original = graph.running.deadline
    assert graph.call() is graph.c.peer
    assert graph.calls[0]["context"].deadline == original
    assert graph.running.deadline == original
    assert graph.c.calls[-1]["intent_id"] is None


@pytest.mark.parametrize(
    "change",
    [
        "stopped",
        "missing_receipt",
        "payload",
        "finished",
        "stop_started",
        "deadline",
        "image",
        "root",
    ],
)
def test_actual_channel_denies_drift_before_authentication(graph, change):
    if change == "stopped":
        graph.c.state.status = "stopped"
    elif change == "missing_receipt":
        del graph.receipts["controller_child"]
    elif change == "payload":
        graph.receipts["controller_child"].payload = {}
    elif change == "finished":
        graph.running.finished = True
    elif change == "stop_started":
        graph.running._stop_started = True
    elif change == "deadline":
        graph.running.deadline = time.monotonic() - 1
    elif change == "image":
        graph.running.supervisor.image_id = "sha256:" + "0" * 64
    elif change == "root":
        graph.running.root = "/wrong"
    with pytest.raises(ProcessError):
        graph.call()
    assert not graph.calls


def test_worker_expected_identity_only_from_persisted_observed_placement(graph):
    c = graph.c
    scope = vars(c.fence)
    sample = NS(
        **scope,
        id=uuid4(),
        epoch_id=c.epoch.id,
        ordinal=1,
        sampling_state_id=uuid4(),
        worker_ticket_sha256="c" * 64,
    )
    old = c.session.get
    c.session.get = lambda cls, key: (
        sample if cls is service.OEStructuredSamplingIntent else old(cls, key)
    )
    control = c.peer.binding.controller
    worker = KernelIdentity(
        control.host_pid + 1,
        control.start_ticks + 1,
        control.pid_namespace,
        control.cgroup,
        control.host_pid,
        (65533,) * 4,
        (65533,) * 4,
    )
    placement = NS(
        **scope,
        sampling_intent_id=sample.id,
        controller_identity_sha256=digest(asdict(c.peer.binding)),
        worker_identity_canonical=canonical_bytes(asdict(worker)).decode(),
        worker_identity_sha256=digest(asdict(worker)),
    )
    cursor = NS(
        **scope, id=sample.sampling_state_id, phase="sampling", sequence=2, frontier=0
    )
    oldscalar = c.session.scalar

    def query(stmt):
        entity = stmt.column_descriptions[0]["entity"]
        if entity is service.OEStructuredWorkerPlacement:
            return placement
        if entity is service.OEStructuredControlState:
            return cursor
        return oldscalar(stmt)

    c.session.scalar = query
    peer = graph.call(sampling_id=sample.id, worker_ticket="b" * 64)
    assert type(peer.binding) is WorkerBinding and peer.binding.worker == worker
    assert graph.calls[0]["worker_ticket"] == "b" * 64
    assert c.calls[-1]["intent_id"] == sample.id
