"""Protocol and fail-stop tests; mocked host is not durable acceptance."""

from uuid import uuid4

import pytest

from packages.openevolve_adapter import observer as o
from packages.openevolve_adapter import observer_transport as transport


def ack(kind, payload):
    assert kind == "terminal_applied"
    return {"frontier": payload["iteration"], "state_sha256": payload["state_sha256"]}


def test_seed_then_contiguous_terminal_ack(monkeypatch):
    monkeypatch.setattr(transport, "host_event", ack)
    with o.epoch_control(uuid4()) as state:
        for i, outcome in enumerate(("seed", "error", "accepted", "rejected")):
            o.terminal_applied(i, outcome, {"last_iteration": i})
            assert state.processed_frontier == i
        with pytest.raises(o.SearchControlAbort):
            o.terminal_applied(3, "accepted", {"last_iteration": 3})
        assert not state.population_state_trusted
        assert state.processed_frontier == 3


@pytest.mark.parametrize("iteration", [True, 1, 2, -1])
def test_out_of_order_precedes_host_request(monkeypatch, iteration):
    monkeypatch.setattr(
        transport, "host_event", lambda *_: pytest.fail("must not dispatch")
    )
    with o.epoch_control(uuid4()) as state:
        with pytest.raises(o.SearchControlAbort):
            o.terminal_applied(iteration, "accepted", {})
        assert state.processed_frontier == -1


def test_failed_ack_does_not_advance(monkeypatch):
    monkeypatch.setattr(
        transport,
        "host_event",
        lambda *_: {"frontier": False, "state_sha256": o._state_digest({})},
    )
    with o.epoch_control(uuid4()) as state:
        with pytest.raises(o.SearchControlAbort):
            o.terminal_applied(0, "seed", {})
        assert state.processed_frontier == -1
        assert not state.population_state_trusted


def test_source_rejection_not_exempt_from_checkpoint_guard():
    with o.epoch_control(uuid4()) as state:
        with pytest.raises(o.SearchControlAbort), o.control_guard("checkpoint_load"):
            raise o.CandidateSourceRejected("host_rejected")
        assert not state.population_state_trusted


def test_source_rejection_remains_ordinary_at_registration():
    with o.epoch_control(uuid4()) as state:
        with (
            pytest.raises(o.CandidateSourceRejected),
            o.control_guard("candidate_registration"),
        ):
            raise o.CandidateSourceRejected("source_profile")
        assert state.population_state_trusted


def test_checkpoint_protocol_and_state_drift(monkeypatch, tmp_path):
    token = str(uuid4())
    destination = tmp_path / str(uuid4())
    monkeypatch.setattr(o, "_checkpoint_path", lambda _: destination)
    calls = []

    def host(kind, payload):
        calls.append(kind)
        if kind == "terminal_applied":
            return ack(kind, payload)
        if kind == "checkpoint_prepare":
            return {"token": token, "path": str(destination)}
        return {
            "published": True,
            "frontier": payload["frontier"],
            "state_sha256": payload["state_sha256"],
        }

    monkeypatch.setattr(transport, "host_event", host)
    with o.epoch_control(uuid4()) as state:
        o.terminal_applied(0, "seed", {"last_iteration": 0})
        assert o.before_checkpoint(0, {"last_iteration": 0})["token"] == token
        assert destination.is_dir()
        with pytest.raises(o.SearchControlAbort):
            o.after_checkpoint(token, 0, {"last_iteration": 1})
        assert calls == ["terminal_applied", "checkpoint_prepare"]
        assert not state.population_state_trusted


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/a",
        "/work/checkpoints/../x",
        "/work/checkpoints/not-a-uuid",
        "/work/checkpoints/" + str(uuid4()) + "/child",
    ],
)
def test_checkpoint_fixed_root(path):
    with pytest.raises(ValueError):
        o._checkpoint_path(path)


def test_exact_checkpoint_and_load_acknowledgements(monkeypatch, tmp_path):
    token = str(uuid4())
    destination = tmp_path / str(uuid4())
    native = {"last_iteration": 0}
    state_sha = o._state_digest(native)
    monkeypatch.setattr(o, "_checkpoint_path", lambda _: destination)

    def host(kind, payload):
        if kind == "terminal_applied":
            return ack(kind, payload)
        if kind == "checkpoint_prepare":
            return {"token": token, "path": str(destination)}
        if kind == "checkpoint_publish":
            return {"published": True, "frontier": 0, "state_sha256": state_sha}
        if kind == "checkpoint_load_prepare":
            return {
                "token": token,
                "path": str(destination),
                "frontier": 0,
                "state_sha256": state_sha,
            }
        return {"loaded": True, "frontier": 0}

    monkeypatch.setattr(transport, "host_event", host)
    with o.epoch_control(uuid4()) as state:
        o.terminal_applied(0, "seed", native)
        o.before_checkpoint(0, native)
        o.after_checkpoint(token, 0, native)
        assert state.pending_checkpoints == {}
    with o.epoch_control(uuid4()) as state:
        descriptor = o.before_checkpoint_load(str(destination))
        o.after_checkpoint_load(descriptor, native)
        assert state.processed_frontier == 0
        assert state.pending_checkpoints == {}


def test_silent_native_repair_cannot_finish_load(monkeypatch, tmp_path):
    descriptor = {
        "token": str(uuid4()),
        "path": str(tmp_path),
        "frontier": 3,
        "state_sha256": o._state_digest({"last_iteration": 3}),
    }
    monkeypatch.setattr(o, "_checkpoint_path", lambda _: tmp_path)
    calls = []

    def host(kind, payload):
        calls.append(kind)
        return descriptor

    monkeypatch.setattr(transport, "host_event", host)
    with o.epoch_control(uuid4()) as state:
        o.before_checkpoint_load(str(tmp_path))
        with pytest.raises(o.SearchControlAbort):
            o.after_checkpoint_load(descriptor, {"last_iteration": 2})
        assert state.processed_frontier == -1
        assert calls == ["checkpoint_load_prepare"]
