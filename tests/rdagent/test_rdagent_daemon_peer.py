"""Exact daemon context with live-authority doubles, not Docker/PG acceptance."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_rdagent_operational_peer import TICKET, binding, declaration
from test_rdagent_protected_controller import resolver as resolver  # noqa: PLC0414

from backend.app import rdagent_protected_controller as service
from packages.research_runtime.process_contracts import ProcessError
from runtimes.rdagent_operational import peer as module
from runtimes.rdagent_operational.daemon_identity import DaemonIdentityContext


@pytest.fixture
def daemon_authority(monkeypatch):
    admission = declaration()
    trusted = binding(admission)
    context = DaemonIdentityContext(
        "d" * 64, admission.image_id, trusted.leader.host_pid, 100.0
    )
    observed = Mock(return_value=(trusted.leader, trusted.controller))

    def pair(self, pid=None):
        assert self is context
        return observed(pid)

    monkeypatch.setattr(DaemonIdentityContext, "observe_pair", pair)
    monkeypatch.setattr(
        module,
        "observe_identity",
        Mock(side_effect=AssertionError("Legacy readlink must not run")),
    )
    credentials = [trusted.controller.host_pid, 65533, 65533]
    monkeypatch.setattr(module, "_credentials", lambda connection: tuple(credentials))
    peer = module.authenticate_controller(
        object(),
        admission,
        binding=trusted,
        supplied_ticket=TICKET,
        observer_context=context,
    )
    fence = SimpleNamespace(
        **{
            key: getattr(admission, key)
            for key in ("owner_id", "run_id", "attempt_id", "generation")
        }
    )
    return SimpleNamespace(
        peer=peer,
        fence=fence,
        context=context,
        observed=observed,
        credentials=credentials,
    )


def validate(value):
    return module.require_controller_peer(
        value.peer,
        value.fence,
        logical_operation_id=value.peer.admission.logical_operation_id,
    )


def test_every_event_reobserves_pair_and_keeps_true_namespace_semantics(
    daemon_authority,
):
    value = daemon_authority
    assert validate(value) is value.peer
    assert validate(value) is value.peer
    assert value.observed.call_count == 3  # Authentication plus both events.
    assert all(call.args == (101,) for call in value.observed.call_args_list)
    assert value.peer.binding.leader.pid_namespace == "pid:[12]"
    assert value.peer._observer_context is value.context


@pytest.mark.parametrize(
    "context",
    [lambda pid: None, object(), SimpleNamespace(observe_pair=lambda pid: None)],
)
def test_arbitrary_observer_callback_rejected(daemon_authority, context):
    value = daemon_authority
    value.peer = replace(value.peer, _observer_context=context)
    with pytest.raises(ProcessError):
        validate(value)
    assert value.observed.call_count == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("image_id", "sha256:" + "e" * 64),
        ("leader_pid", 999),
        ("profile", "mineagent.rdagent-orchestrator.v3"),
    ],
)
def test_context_binding_mismatch_before_observation(daemon_authority, field, value):
    state = daemon_authority
    state.peer = replace(
        state.peer, _observer_context=replace(state.context, **{field: value})
    )
    with pytest.raises(ProcessError):
        validate(state)
    assert state.observed.call_count == 1


@pytest.mark.parametrize("which", [0, 1])
@pytest.mark.parametrize(
    "field,value",
    [("start_ticks", 99999), ("pid_namespace", "pid:[999]"), ("cgroup", "0::/other\n")],
)
def test_new_kernel_pair_cannot_replace_persisted_identity(
    daemon_authority, which, field, value
):
    state = daemon_authority
    observed = list(state.observed.return_value)
    observed[which] = replace(observed[which], **{field: value})
    state.observed.return_value = tuple(observed)
    with pytest.raises(ProcessError):
        validate(state)


def test_changed_socket_peercred_still_rejected(daemon_authority):
    daemon_authority.credentials[0] = 102
    with pytest.raises(ProcessError):
        validate(daemon_authority)


def test_failed_observation_never_uses_cached_pair(daemon_authority):
    daemon_authority.observed.side_effect = ProcessError("daemon_gone")
    with pytest.raises(ProcessError):
        validate(daemon_authority)


def context_for(resolver):
    identity = next(r.payload for r in resolver.receipts if r.kind == "launch_identity")
    return DaemonIdentityContext(
        identity["container_id"],
        identity["image_id"],
        identity["leader"]["host_pid"],
        100.0,
    )


def test_db_resolver_checks_actual_typed_persisted_context(resolver):
    resolver.authorization = SimpleNamespace(_observer_context=context_for(resolver))
    assert (
        service.require_protected_controller(
            resolver.session, resolver.fence, resolver.authorization
        )
        is resolver.launch
    )
    resolver.authenticate.assert_called_once()


@pytest.mark.parametrize(
    "field,value",
    [
        ("container_id", "f" * 64),
        ("image_id", "sha256:" + "f" * 64),
        ("leader_pid", 999),
    ],
)
def test_db_context_mismatch_refused_before_peer_authority(resolver, field, value):
    context = replace(context_for(resolver), **{field: value})
    resolver.authorization = SimpleNamespace(_observer_context=context)
    with pytest.raises(ProcessError, match="observer_context_drift"):
        service.require_protected_controller(
            resolver.session, resolver.fence, resolver.authorization
        )
    resolver.authenticate.assert_not_called()


def test_db_arbitrary_context_cannot_reach_peer_authority(resolver):
    resolver.authorization = SimpleNamespace(_observer_context=lambda: True)
    with pytest.raises(ProcessError, match="observer_context_drift"):
        service.require_protected_controller(
            resolver.session, resolver.fence, resolver.authorization
        )
    resolver.authenticate.assert_not_called()


def test_context_subclass_cannot_override_observer(daemon_authority):
    class UntrustedContext(DaemonIdentityContext):
        def observe_pair(self, controller_pid=None):
            raise AssertionError("Override must never execute")

    state = daemon_authority
    state.peer = replace(
        state.peer,
        _observer_context=UntrustedContext(
            state.context.container_id,
            state.context.image_id,
            state.context.leader_pid,
            state.context.deadline,
        ),
    )
    with pytest.raises(ProcessError):
        validate(state)
    assert state.observed.call_count == 1
