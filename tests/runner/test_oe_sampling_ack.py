"""Actual host/client sampling wire roundtrip; DB/peer and socket I/O doubles only."""

import json
import struct
import time
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import oe_structured_events as host
from backend.app.oe_structured_event_wire import read_event
from backend.app.oe_structured_operational_models import OEStructuredControlState
from packages.openevolve_structured import checkpoint_runtime, observer
from packages.openevolve_structured import observer_transport as transport
from packages.openevolve_structured import sampling_control as client
from packages.openevolve_structured.checkpoint import canonical
from packages.research_runtime.process_contracts import ProcessError
from tests.openevolve.test_structured_events import fixture
from tests.openevolve.test_structured_population import domain, graph, native  # noqa: F401


@pytest.fixture
def wire(monkeypatch, graph):  # noqa: F811
    c = fixture(monkeypatch)
    c.epoch = graph.intent.epoch_id
    scope = vars(graph.fence)
    pid = next(iter(graph.state["programs"]))
    parent = NS(**scope, id=uuid4(), candidate_sha256="a" * 64)
    row = NS(
        **scope,
        id=uuid4(),
        epoch_id=c.epoch,
        ordinal=1,
        parent_candidate_id=parent.id,
        selection_sha256="b" * 64,
        sampling_state_id=graph.rows[1].id,
    )
    state = NS(control=graph.rows[1], replies=[], alter=lambda value: value)
    c.state.session.get = lambda model, identity: (
        state.control if model is OEStructuredControlState else parent
    )
    monkeypatch.setattr(host, "register_sampling", lambda *a, **k: (row, "c" * 64))
    monkeypatch.setattr(checkpoint_runtime, "_domain", lambda: graph.domain)
    monkeypatch.setattr(
        client,
        "_LAST_ACK",
        __import__("contextvars").ContextVar("test_sampling_ack", default=None),
    )
    monkeypatch.setattr(
        transport,
        "_bootstrap",
        lambda: NS(
            schema_version="mineagent.oe-structured-bootstrap.v2",
            admission_id=c.admission,
            epoch_id=c.epoch,
        ),
    )
    monkeypatch.setattr(transport, "process_ticket", lambda: "c" * 64)

    class Channel:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def settimeout(self, value):
            assert 0 < value <= 95

        def connect(self, _):
            pass

        def sendall(self, raw):
            assert struct.unpack("!I", raw[:4])[0] == len(raw) - 4
            event = read_event(raw[4:])
            answer = host.apply_event(
                c.factory,
                graph.fence,
                event,
                c.settings,
                peer=c.peer,
                ticket_key=b"x" * 32,
            )
            assert c.calls[-2:] == ["commit", "peer"]
            value = json.loads(answer)
            state.replies.append(value)
            value["result"] = state.alter(value["result"])
            raw = canonical(value)
            self.buffer = struct.pack("!I", len(raw)) + raw

        def recv(self, size):
            result, self.buffer = self.buffer[:size], self.buffer[size:]
            return result

    monkeypatch.setattr(transport.socket, "socket", lambda *_: Channel())
    monkeypatch.setattr(
        client,
        "host_event",
        lambda kind, payload: transport._send(
            kind, payload, deadline=time.monotonic() + 60
        ),
    )

    def call():
        return client.before_submit(1, pid, [], {}, graph.state, graph.state)

    return NS(**locals())


def test_host_sampling_ack_exact_client_roundtrip_and_duplicate_replay(wire):
    with observer.epoch_control(wire.c.epoch):
        grant = wire.call()
        assert grant["intent_id"] == str(wire.row.id)
        assert grant["epoch_id"] == str(wire.c.epoch)
        answer = wire.state.replies[0]["result"]
        assert answer == {
            "iteration_grant": grant,
            "sampling_receipt_id": str(wire.state.control.id),
            "control_sequence": wire.state.control.sequence,
            "state_sha256": wire.state.control.state_sha256,
        }
        # A successful ACK is not permission to dispatch the same selection twice.
        with pytest.raises(observer.SearchControlAbort) as caught:
            wire.call()
        assert caught.value.phase == "submission"
        assert wire.state.replies[1] == wire.state.replies[0]


@pytest.mark.parametrize(
    "field",
    [
        "missing",
        "id",
        "owner_id",
        "run_id",
        "attempt_id",
        "generation",
        "epoch_id",
        "phase",
        "sequence",
        "frontier",
    ],
)
def test_host_sampling_receipt_scope_and_cursor_drift_never_acknowledged(wire, field):
    if field == "missing":
        wire.state.control = None
    else:
        old = getattr(wire.state.control, field)
        setattr(
            wire.state.control,
            field,
            "applied" if field == "phase" else old + 1 if type(old) is int else uuid4(),
        )
    with (
        observer.epoch_control(wire.c.epoch),
        pytest.raises((ProcessError, observer.SearchControlAbort)),
    ):
        wire.call()
    assert not wire.state.replies and "commit" not in wire.c.calls


@pytest.mark.parametrize(
    "fault",
    [
        "bare",
        "extra",
        "missing",
        "receipt",
        "sequence",
        "boolean",
        "hash",
        "epoch",
        "ordinal",
    ],
)
def test_client_rejects_bare_or_drifted_sampling_receipt(wire, fault):
    def alter(value):
        if fault == "bare":
            return value["iteration_grant"]
        if fault == "extra":
            value["unexpected"] = True
        elif fault == "missing":
            del value["sampling_receipt_id"]
        elif fault == "receipt":
            value["sampling_receipt_id"] = "not-a-uuid"
        elif fault == "sequence":
            value["control_sequence"] = 0
        elif fault == "boolean":
            value["control_sequence"] = True
        elif fault == "hash":
            value["state_sha256"] = "0" * 64
        elif fault == "epoch":
            value["iteration_grant"]["epoch_id"] = str(uuid4())
        elif fault == "ordinal":
            value["iteration_grant"]["native_iteration"] = 2
        return value

    wire.state.alter = alter
    with (
        observer.epoch_control(wire.c.epoch),
        pytest.raises(observer.SearchControlAbort) as caught,
    ):
        wire.call()
    assert caught.value.phase == "submission"
    assert len(wire.state.replies) == 1


def test_failed_sampling_commit_cannot_return_client_grant(wire):
    wire.c.state.fail_commit = True
    with (
        observer.epoch_control(wire.c.epoch),
        pytest.raises(observer.SearchControlAbort) as caught,
    ):
        wire.call()
    assert caught.value.phase == "submission"
    assert not wire.state.replies and "commit" not in wire.c.calls
