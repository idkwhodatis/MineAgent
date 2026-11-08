"""Batch observer adversarial doubles; actual Docker proof remains a separate gate."""

from unittest.mock import Mock

import pytest

from packages.research_runtime.process_contracts import ProcessError
from runtimes.openevolve_operational import daemon_identity as module
from tests.openevolve import test_operational_daemon_identity as predecessor


@pytest.fixture
def batch(monkeypatch):
    observer = predecessor.observer.__wrapped__(monkeypatch)
    observer.hosts[102] = (102, 502, "0::/unit\n", 101, (65533,) * 4, (65533,) * 4)
    raw = observer.cli._command.return_value[1] + b"102 65533 101 4026532777 python\n"
    observer.raw = raw
    observer.top = Mock(return_value=raw)
    monkeypatch.setattr(module, "read_top", observer.top)
    observer.cli._command.side_effect = AssertionError("CLI top must not run")
    return observer


def test_two_complete_snapshots_share_only_outer_inspects(batch):
    observed = batch.value.observe_members(101, 102)
    assert type(observed) is module.DaemonMemberObservation
    identities = (observed.leader, observed.controller, observed.worker)
    assert tuple(i.host_pid for i in identities) == (100, 101, 102)
    assert tuple(i.start_ticks for i in identities) == (500, 501, 502)
    assert {i.pid_namespace for i in identities} == {"pid:[4026532777]"}
    assert batch.cli._inspect.call_count == batch.top.call_count == 2
    assert [call.args[0] for call in batch.host.call_args_list] == [
        100,
        101,
        102,
        102,
        101,
        100,
    ] * 2
    for call in batch.top.call_args_list:
        assert call.args == ("d" * 64,) and call.kwargs == {"deadline": 60}
    batch.value.observe_members(101, 102)
    assert batch.top.call_count == 4  # Never reuse a prior call.
    batch.cli._command.assert_not_called()


def test_direct_top_preserves_chronological_host_and_inspect_brackets(
    batch, monkeypatch
):
    events = []

    def inspect(_cid):
        events.append("inspect")
        return batch.document

    def host(pid):
        events.append(pid)
        return batch.hosts[pid]

    def top(cid, *, deadline):
        assert cid == batch.value.container_id and deadline == batch.value.deadline
        events.append("top")
        return batch.raw

    batch.cli._inspect.side_effect = inspect
    batch.host.side_effect = host
    monkeypatch.setattr(module, "read_top", top)
    batch.value.observe_members(101, 102)
    assert events == [
        "inspect",
        100,
        101,
        102,
        "top",
        102,
        101,
        100,
        100,
        101,
        102,
        "top",
        102,
        101,
        100,
        "inspect",
    ]
    batch.cli._command.assert_not_called()


@pytest.mark.parametrize("error", [ValueError(), TimeoutError(), OSError()])
def test_direct_top_transport_failure_aborts_without_fallback(
    batch, monkeypatch, error
):
    top = Mock(side_effect=error)
    monkeypatch.setattr(module, "read_top", top)
    with pytest.raises(ProcessError, match="oe_controller_daemon_identity"):
        batch.value.observe_members(101, 102)
    assert batch.cli._inspect.call_count == top.call_count == 1
    assert batch.host.call_count == 3
    batch.cli._command.assert_not_called()


@pytest.mark.parametrize("target", [100, 101, 102])
@pytest.mark.parametrize(
    "field,value", [(1, 999), (2, "0::/foreign"), (3, 99), (4, (1,) * 4), (5, (1,) * 4)]
)
def test_host_drift_for_each_identity_inside_snapshot(batch, target, field, value):
    count = 0

    def host(pid):
        nonlocal count
        count += 1
        old = batch.hosts[pid]
        if count > 3 and pid == target:
            changed = list(old)
            changed[field] = value
            return tuple(changed)
        return old

    batch.host.side_effect = host
    with pytest.raises(ProcessError):
        batch.value.observe_members(101, 102)


def test_controller_drift_during_worker_snapshot_is_rechecked_after_worker(batch):
    count = 0

    def host(pid):
        nonlocal count
        count += 1
        old = batch.hosts[pid]
        if count == 4:
            assert pid == 102
            c = batch.hosts[101]
            batch.hosts[101] = (c[0], 999, *c[2:])
        return old

    batch.host.side_effect = host
    with pytest.raises(ProcessError):
        batch.value.observe_members(101, 102)


@pytest.mark.parametrize("target", [100, 101, 102])
def test_pid_reuse_between_complete_snapshots_rejected(batch, target):
    count = 0

    def host(pid):
        nonlocal count
        count += 1
        old = batch.hosts[pid]
        return (old[0], 999, *old[2:]) if count > 6 and pid == target else old

    batch.host.side_effect = host
    with pytest.raises(ProcessError):
        batch.value.observe_members(101, 102)


@pytest.mark.parametrize("target", [b"100", b"101", b"102"])
def test_namespace_drift_between_daemon_snapshots_rejected(batch, target):
    lines = batch.raw.splitlines()
    changed = (
        b"\n".join(
            line.replace(b"4026532777", b"4026532999")
            if line.startswith(target + b" ")
            else line
            for line in lines
        )
        + b"\n"
    )
    batch.top.side_effect = [batch.raw, changed]
    with pytest.raises(ProcessError):
        batch.value.observe_members(101, 102)


@pytest.mark.parametrize(
    "change", ["missing", "foreign", "duplicate", "uid", "parent", "malformed"]
)
def test_top_inventory_cannot_substitute_for_exact_members(batch, change):
    row = b"102 65533 101 4026532777 python\n"
    replacement = {
        "missing": b"",
        "foreign": row.replace(b"102", b"103"),
        "duplicate": row + row,
        "uid": row.replace(b"65533", b"0"),
        "parent": row.replace(b" 101 ", b" 100 "),
        "malformed": row.replace(b"4026532777", b"0"),
    }[change]
    batch.top.return_value = batch.raw.replace(row, replacement)
    with pytest.raises(ProcessError):
        batch.value.observe_members(101, 102)


@pytest.mark.parametrize(
    "field,value",
    [
        ("Id", "e" * 64),
        ("Image", "sha256:" + "e" * 64),
        ("Pid", 999),
        ("Running", False),
        ("Status", "restarting"),
    ],
)
@pytest.mark.parametrize("when", ["before", "after"])
def test_unit_drift_rejected_at_either_inspect(batch, field, value, when):
    bad = {**batch.document, "State": dict(batch.document["State"])}
    if field in ("Id", "Image"):
        bad[field] = value
    else:
        bad["State"][field] = value
    batch.cli._inspect.side_effect = (
        [bad, batch.document] if when == "before" else [batch.document, bad]
    )
    with pytest.raises(ProcessError):
        batch.value.observe_members(101, 102)


@pytest.mark.parametrize("late_at", range(1, 10))
def test_original_deadline_checked_throughout_batch(batch, monkeypatch, late_at):
    monkeypatch.setattr(
        module.time, "monotonic", Mock(side_effect=[10.0] * (late_at - 1) + [61.0] * 12)
    )
    with pytest.raises(ProcessError):
        batch.value.observe_members(101, 102)


@pytest.mark.parametrize(
    "controller,worker",
    [
        (100, 102),
        (101, 100),
        (101, 101),
        (True, 102),
        (101, False),
        (101, 0),
        (None, 102),
        (101, "102"),
    ],
)
def test_invalid_member_inventory_fails_before_io(batch, controller, worker):
    with pytest.raises(ProcessError):
        batch.value.observe_members(controller, worker)
    batch.cli._inspect.assert_not_called()
    batch.top.assert_not_called()
    batch.cli._command.assert_not_called()
