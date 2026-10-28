"""Callback ordering/TOCTOU doubles; not live Docker or PostgreSQL acceptance."""

from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_rdagent_daemon_peer import daemon_authority as daemon_authority  # noqa: PLC0414
from test_rdagent_operational_peer import TICKET

from backend.app import rdagent_operational_handler as handler_module
from backend.app import rdagent_protected_controller as service
from backend.app import research_lifecycle
from packages.research_runtime.process_contracts import ProcessError
from runtimes.rdagent_operational import peer as peer_module


@pytest.fixture
def callback(monkeypatch, daemon_authority):
    state = daemon_authority
    state.now = [50.0]
    monkeypatch.setattr(service.time, "monotonic", lambda: state.now[0])
    state.events = []
    state.fail_commit = False
    state.session = object()
    state.factory = object()
    state.on_load = lambda: None
    state.observed.reset_mock()
    state.identity = SimpleNamespace(
        container_id=state.context.container_id,
        image_id=state.context.image_id,
        leader=state.peer.binding.leader,
    )
    state.current = state.peer.admission
    state.expected = state.peer.binding

    @contextmanager
    def transaction(factory, fence, *, resource_scope):
        assert factory is state.factory and fence is state.fence
        assert resource_scope == "processes"
        state.events.append("enter")
        yield state.session
        state.events.append("commit")
        if state.fail_commit:
            raise RuntimeError("commit failed")

    def records(session, fence):
        assert session is state.session and fence is state.fence
        state.events.append("records")
        state.on_load()
        return object(), state.current, state.expected, state.identity

    original = peer_module.authenticate_controller

    def authenticate(*args, **kwargs):
        state.events.append("kernel")
        return original(*args, **kwargs)

    monkeypatch.setattr(research_lifecycle, "neutral_transaction", transaction)
    monkeypatch.setattr(handler_module, "neutral_transaction", transaction)
    monkeypatch.setattr(service, "_controller_records", records)
    monkeypatch.setattr(peer_module, "authenticate_controller", authenticate)
    return state


def invoke(state, ticket=TICKET):
    return service.authenticate_current_controller(
        state.factory,
        state.fence,
        object(),
        state.peer.admission,
        binding=state.peer.binding,
        supplied_ticket=ticket,
        observer_context=state.context,
    )


def test_one_fresh_full_observation_inside_each_callback_then_commit(callback):
    for _ in range(2):
        result = invoke(callback)
        assert type(result) is peer_module.ControllerPeer
    assert callback.events == ["enter", "records", "kernel", "commit"] * 2
    assert callback.observed.call_count == 2
    # Handler authority remains independently fresh, never trusts callback evidence.
    handler_module.OperationalHandler(
        callback.factory, callback.fence, None
    )._authorize(result)
    assert callback.events.count("records") == 3
    assert callback.observed.call_count == 3


def test_wrong_ticket_rejected_before_transaction_or_kernel(callback):
    with pytest.raises(ProcessError):
        invoke(callback, "b" * 64)
    assert callback.events == []
    callback.observed.assert_not_called()


@pytest.mark.parametrize(
    "change", ["admission", "binding", "context", "bool_generation"]
)
def test_persisted_binding_drift_cannot_mint_authority(callback, change):
    if change == "admission":
        callback.current = callback.current.model_copy(update={"generation": 2})
    elif change == "binding":
        callback.expected = replace(callback.expected, admission_sha256="e" * 64)
    elif change == "context":
        callback.identity.container_id = "e" * 64
    else:
        callback.fence.generation = True
    with pytest.raises(ProcessError):
        invoke(callback)
    assert callback.events == ["enter", "records"]
    callback.observed.assert_not_called()


@pytest.mark.parametrize(
    "change", ["credentials", "pid", "start", "namespace", "cgroup"]
)
def test_peer_changes_during_record_load_fail_final_kernel_check(callback, change):
    def change_peer():
        if change == "credentials":
            callback.credentials[0] += 1
        else:
            leader, child = callback.observed.return_value
            key, value = {
                "pid": ("host_pid", 999),
                "start": ("start_ticks", 999),
                "namespace": ("pid_namespace", "pid:[999]"),
                "cgroup": ("cgroup", "0::/other\n"),
            }[change]
            callback.observed.return_value = (leader, replace(child, **{key: value}))

    callback.on_load = change_peer
    with pytest.raises(ProcessError):
        invoke(callback)
    assert callback.events == ["enter", "records", "kernel"]
    callback.observed.assert_called_once()


def test_commit_failure_never_returns_authenticated_peer(callback):
    callback.fail_commit = True
    with pytest.raises(RuntimeError, match="commit failed"):
        invoke(callback)
    assert callback.events == ["enter", "records", "kernel", "commit"]
    callback.observed.assert_called_once()


@pytest.mark.parametrize("changed", ["records", "peer"])
def test_second_callback_never_reuses_first_authority(callback, changed):
    invoke(callback)
    if changed == "records":
        callback.on_load = Mock(side_effect=ProcessError("rd_controller_receipts"))
    else:
        callback.credentials[1] = 123
    with pytest.raises(ProcessError):
        invoke(callback)
    assert callback.events.count("records") == (2 if changed == "records" else 1)
    assert callback.events.count("commit") == 1


@pytest.mark.parametrize("now", [100.0, 101.0, 39.0])
def test_invalid_original_deadline_rejected_before_db(callback, now):
    callback.now[0] = now
    with pytest.raises(ProcessError, match="rd_controller_daemon_identity"):
        invoke(callback)
    assert callback.events == []
    callback.observed.assert_not_called()


def test_expiry_during_db_load_is_still_rejected_by_full_observer(
    callback, monkeypatch
):
    from runtimes.rdagent_operational import daemon_identity

    # Restore real observe_pair/_observe deadline path; fake Docker constructor
    # must never reach inspect after deadline has expired during DB loading.
    def pair(context, pid):
        return daemon_identity._observe(
            container_id=context.container_id,
            image_id=context.image_id,
            leader_pid=context.leader_pid,
            pid=pid,
            deadline=context.deadline,
        )

    monkeypatch.setattr(daemon_identity.DaemonIdentityContext, "observe_pair", pair)
    cli = Mock()
    monkeypatch.setattr(daemon_identity, "DockerSupervisor", lambda image: cli)
    callback.on_load = lambda: callback.now.__setitem__(0, 100.0)
    with pytest.raises(ProcessError, match="rd_controller_daemon_identity"):
        invoke(callback)
    assert callback.events == ["enter", "records", "kernel"]
    cli._inspect.assert_not_called()


@pytest.mark.parametrize("index", [0, 1, 2])
def test_wrong_socket_credentials_reject_before_db(callback, index):
    callback.credentials[index] += 1
    with pytest.raises(ProcessError):
        invoke(callback)
    assert callback.events == []
    callback.observed.assert_not_called()
