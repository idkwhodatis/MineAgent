"""Real threaded admission; no Docker, peer or PostgreSQL authority claim."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.app.openevolve_checkpoint_boundary import CheckpointBoundary


def test_checkpoint_drains_readers_and_blocks_new_readers():
    gate = CheckpointBoundary()
    first = threading.Event()
    finish = threading.Event()
    writer_entered = threading.Event()
    release_writer = threading.Event()
    second = threading.Event()
    deadline = time.monotonic() + 3

    def reader():
        with gate.enter(checkpoint=False, deadline=deadline):
            first.set()
            assert finish.wait(2)

    def writer():
        with gate.enter(checkpoint=True, deadline=deadline):
            writer_entered.set()
            assert release_writer.wait(2)

    def next_reader():
        with gate.enter(checkpoint=False, deadline=deadline):
            second.set()

    with ThreadPoolExecutor(max_workers=3) as pool:
        a = pool.submit(reader)
        assert first.wait(1)
        b = pool.submit(writer)
        with gate._condition:
            assert gate._condition.wait_for(lambda: gate._waiting == 1, timeout=1)
        c = pool.submit(next_reader)
        assert not writer_entered.is_set() and not second.is_set()
        finish.set()
        assert writer_entered.wait(1)
        assert not second.is_set()
        release_writer.set()
        for future in (a, b, c):
            future.result(timeout=2)
    assert second.is_set()


def test_normal_handlers_remain_concurrent():
    gate = CheckpointBoundary()
    rendezvous = threading.Barrier(2)

    def normal():
        with gate.enter(checkpoint=False, deadline=time.monotonic() + 2):
            rendezvous.wait(timeout=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(normal), pool.submit(normal)
        a.result(timeout=2)
        b.result(timeout=2)


def test_two_checkpoint_writers_never_overlap():
    gate = CheckpointBoundary()
    entered, release, second = threading.Event(), threading.Event(), threading.Event()
    deadline = time.monotonic() + 3

    def first_writer():
        with gate.enter(checkpoint=True, deadline=deadline):
            entered.set()
            assert release.wait(2)

    def second_writer():
        with gate.enter(checkpoint=True, deadline=deadline):
            second.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(first_writer)
        assert entered.wait(1)
        b = pool.submit(second_writer)
        with gate._condition:
            assert gate._condition.wait_for(lambda: gate._waiting == 1, timeout=1)
        assert not second.is_set()
        release.set()
        a.result(timeout=2)
        b.result(timeout=2)
    assert second.is_set() and gate._waiting == 0 and not gate._writer


@pytest.mark.parametrize("checkpoint", [False, True])
@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_body_failure_never_replayed_and_always_releases(checkpoint, failure):
    gate, calls = CheckpointBoundary(), []
    with (
        pytest.raises(failure),
        gate.enter(checkpoint=checkpoint, deadline=time.monotonic() + 2),
    ):
        calls.append(1)
        raise failure()
    with gate.enter(checkpoint=True, deadline=time.monotonic() + 2):
        assert calls == [1]
    assert gate._readers == gate._waiting == 0 and not gate._writer


@pytest.mark.parametrize("checkpoint", [False, True])
def test_disconnect_wakes_waiter_without_body(checkpoint):
    gate = CheckpointBoundary()
    with (
        ThreadPoolExecutor(max_workers=1) as pool,
        gate.enter(checkpoint=True, deadline=time.monotonic() + 2),
    ):

        def blocked():
            with gate.enter(checkpoint=checkpoint, deadline=time.monotonic() + 2):
                pytest.fail("Disconnected admission entered body")

        future = pool.submit(blocked)
        gate.close()
        with pytest.raises(ValueError, match="disconnected"):
            future.result(timeout=1)


def test_expired_waiting_writer_releases_reader_queue_without_renewal():
    gate = CheckpointBoundary()
    deadline = time.monotonic() + 0.08
    with (
        ThreadPoolExecutor(max_workers=1) as pool,
        gate.enter(checkpoint=False, deadline=time.monotonic() + 2),
    ):

        def expired():
            with gate.enter(checkpoint=True, deadline=deadline):
                pytest.fail("Expired admission entered body")

        future = pool.submit(expired)
        with pytest.raises(ValueError, match="deadline"):
            future.result(timeout=1)
        with gate.enter(checkpoint=False, deadline=time.monotonic() + 1):
            assert gate._waiting == 0


@pytest.mark.parametrize("deadline", [float("nan"), float("inf"), True, "5", 0])
def test_invalid_or_expired_admission_fails_before_body(deadline):
    with (
        pytest.raises(ValueError),
        CheckpointBoundary().enter(checkpoint=True, deadline=deadline),
    ):
        pytest.fail("Invalid admission entered body")


def test_actual_server_dispatch_waits_for_boundary_then_rechecks_authority(monkeypatch):
    """Actual socket framing/gate; peer/domain are explicit fault doubles."""
    import socket
    import struct

    from backend.app import openevolve_socket_server as server
    from tests.openevolve.test_operational_socket_server import initial, wire

    unit = initial()
    authed, dispatched = threading.Event(), []
    failures = []
    monkeypatch.setattr(
        server, "authenticate_socket", lambda *a, **k: authed.set() or object()
    )

    def reject_stale(*args, **kwargs):
        dispatched.append(kwargs["event"].kind)
        # All actual domain handlers retain their existing fresh persisted fence
        # and peer validation after waiting. This double exercises refusal.
        raise ValueError("fresh authority lost")

    monkeypatch.setattr(server, "dispatch_event", reject_stale)
    client, host = socket.socketpair()

    def handle():
        try:
            unit._handle(host, "events")
        except ValueError as error:
            failures.append(str(error))

    try:
        with unit._checkpoint_boundary.enter(
            checkpoint=False, deadline=unit.running.deadline
        ):
            thread = threading.Thread(target=handle)
            thread.start()
            raw = wire(unit)
            client.sendall(struct.pack("!I", len(raw)) + raw)
            assert authed.wait(1)
            with unit._checkpoint_boundary._condition:
                assert unit._checkpoint_boundary._condition.wait_for(
                    lambda: unit._checkpoint_boundary._waiting == 1, timeout=1
                )
            assert dispatched == []
        thread.join(timeout=1)
        assert not thread.is_alive()
        assert dispatched == ["checkpoint_publish"]
        assert failures == ["fresh authority lost"]
        assert unit.progress()["acknowledgements"] == 0
        assert not unit._checkpoint_boundary._writer
    finally:
        unit.disconnect()
        client.close()
        host.close()


def test_actual_server_disconnect_releases_waiting_checkpoint(monkeypatch):
    import socket
    import struct

    from backend.app import openevolve_socket_server as server
    from tests.openevolve.test_operational_socket_server import initial, wire

    unit, errors = initial(), []
    monkeypatch.setattr(server, "authenticate_socket", lambda *a, **k: object())
    monkeypatch.setattr(
        server, "dispatch_event", lambda *a, **k: pytest.fail("closed dispatch")
    )
    client, host = socket.socketpair()

    def handle():
        try:
            unit._handle(host, "events")
        except ValueError as error:
            errors.append(str(error))

    try:
        with unit._checkpoint_boundary.enter(
            checkpoint=False, deadline=unit.running.deadline
        ):
            thread = threading.Thread(target=handle)
            thread.start()
            raw = wire(unit)
            client.sendall(struct.pack("!I", len(raw)) + raw)
            with unit._checkpoint_boundary._condition:
                assert unit._checkpoint_boundary._condition.wait_for(
                    lambda: unit._checkpoint_boundary._waiting == 1, timeout=1
                )
            unit.disconnect()
            thread.join(timeout=1)
            assert not thread.is_alive()
        assert errors == ["Checkpoint boundary disconnected"]
        assert unit.progress()["acknowledgements"] == 0
    finally:
        client.close()
        host.close()


def test_queued_worker_after_final_boundary_cannot_reopen_closed_assignment(
    monkeypatch,
):
    """Original worker phase gate, not a simulated full final checkpoint."""
    from unittest.mock import Mock

    from backend.app import openevolve_worker_actions as actions
    from backend.app.services import DomainError
    from tests.openevolve.test_worker_actions import binding, fixture

    values = fixture()
    fence, _, _, _, _, _, assignment = values
    monkeypatch.setattr(actions, "_live_worker", lambda *a: binding(values))
    session = Mock()
    # The original immutable assignment closure is returned after publication;
    # it is never removed or bypassed by the host scheduling boundary.
    session.scalar.side_effect = [assignment, object()]
    gate, queued = CheckpointBoundary(), threading.Event()
    deadline = time.monotonic() + 2

    def late_worker():
        queued.set()
        with gate.enter(checkpoint=False, deadline=deadline):
            actions._open_worker(session, fence, object(), object(), "fixed")

    with ThreadPoolExecutor(max_workers=1) as pool:
        with gate.enter(checkpoint=True, deadline=deadline):
            future = pool.submit(late_worker)
            assert queued.wait(1)
            session.scalar.assert_not_called()
        with pytest.raises(DomainError):
            future.result(timeout=1)
    session.add.assert_not_called()
    session.commit.assert_not_called()
