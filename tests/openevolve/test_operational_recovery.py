"""Real immutable journal bytes with Docker/kernel observation doubles only."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

from packages.research_runtime.process_contracts import ProcessError, canonical, digest
from runtimes.openevolve_operational import recovery as module
from tests.openevolve.operational_fixtures import records


def write(root, values, include_child=True):
    launch, identity, child, stop = values
    directory = (
        root
        / "research-processes"
        / str(launch.intent.admission.owner_id)
        / str(launch.intent.admission.id)
        / "control"
    )
    directory.mkdir(parents=True, mode=0o700)
    for name, value in [
        ("launch", launch),
        ("identity", identity),
        ("child", child),
        ("stop", stop),
    ]:
        if name == "child" and not include_child:
            continue
        path = directory / (name + ".json")
        path.write_bytes(canonical(value.model_dump(mode="json")))
        path.chmod(0o600)
    return directory


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    launch, identity, child, stop = records(tmp_path)
    identity = identity.model_copy(
        update={
            "boot_id": UUID(Path("/proc/sys/kernel/random/boot_id").read_text().strip())
        }
    )
    child = child.model_copy(
        update={"identity_sha256": digest(identity.model_dump(mode="json"))}
    )
    stop = stop.model_copy(
        update={
            "identity_sha256": child.identity_sha256,
            "child_sha256": digest(child.model_dump(mode="json")),
        }
    )
    directory = write(tmp_path, (launch, identity, child, stop))
    (directory / "stop.json").unlink()
    state = {
        "Id": identity.container_id,
        "State": {
            "Running": False,
            "Status": "exited",
            "Pid": identity.leader.host_pid,
        },
    }
    existing = [state]
    actions = []

    def command(args):
        actions.append(args)
        existing[0] = None
        return 0, b""

    cli = SimpleNamespace(
        _inspect=Mock(side_effect=lambda _: existing[0]),
        _command=Mock(side_effect=command),
    )
    supervisor = SimpleNamespace(
        image_id=launch.intent.admission.image_id, cli=cli, inventory={}
    )
    verify = Mock()
    monkeypatch.setattr(module, "verify_container", verify)
    gone = Mock(return_value=True)
    monkeypatch.setattr(module, "_kernel_gone", gone)
    observer = Mock()
    observer.observe.return_value = identity.leader
    observer.observe_pair.return_value = (identity.leader, child.controller)
    monkeypatch.setattr(module, "DaemonIdentityContext", lambda *a: observer)
    return SimpleNamespace(
        root=tmp_path,
        directory=directory,
        launch=launch,
        identity=identity,
        child=child,
        stop=stop,
        state=state,
        existing=existing,
        actions=actions,
        supervisor=supervisor,
        verify=verify,
        gone=gone,
        observer=observer,
    )


def recover(r):
    return module.recover_controller(r.supervisor, r.root, r.launch.intent.admission)


def test_exact_stopped_unit_positive_journal_and_replay(recovery):
    r = recovery
    assert recover(r) == r.stop
    assert r.actions == [["rm", "--force", r.identity.container_id]]
    assert recover(r) == r.stop
    assert len(r.actions) == 1


@pytest.mark.parametrize("status", ["created", "exited"])
def test_missing_identity_cleanup_remains_uncertain(recovery, status):
    r = recovery
    for name in ("identity.json", "child.json"):
        (r.directory / name).unlink()
    r.state["State"]["Status"] = status
    inputs = r.directory.parent / "input"
    inputs.mkdir()
    (inputs / "bootstrap.json").write_bytes(b"retained")
    with pytest.raises(ProcessError, match="identity_unobserved"):
        recover(r)
    assert len(r.actions) == 1
    assert not (r.directory / "stop.json").exists()
    assert (inputs / "bootstrap.json").read_bytes() == b"retained"


def test_missing_identity_and_unit_is_not_non_submission(recovery):
    r = recovery
    for name in ("identity.json", "child.json"):
        (r.directory / name).unlink()
    r.existing[0] = None
    with pytest.raises(ProcessError, match="identity_unobserved"):
        recover(r)
    assert not r.actions and not (r.directory / "stop.json").exists()


def test_running_missing_identity_actual_observation_then_stop(recovery):
    r = recovery
    for name in ("identity.json", "child.json"):
        (r.directory / name).unlink()
    r.state["State"]["Running"] = True
    result = recover(r)
    assert result.child_sha256 is None
    assert r.observer.observe.call_count == 1
    assert (r.directory / "identity.json").exists()
    assert len(r.actions) == 1


def test_running_missing_identity_observation_failure_only_cleanup(recovery):
    r = recovery
    for name in ("identity.json", "child.json"):
        (r.directory / name).unlink()
    r.state["State"]["Running"] = True
    r.observer.observe.side_effect = ProcessError("daemon_failed")
    with pytest.raises(ProcessError, match="identity_unobserved"):
        recover(r)
    assert len(r.actions) == 1
    assert not (r.directory / "identity.json").exists()
    assert not (r.directory / "stop.json").exists()


@pytest.mark.parametrize(
    "change", ["config", "cid", "root", "launch", "child_without_identity"]
)
def test_identity_or_config_drift_no_mutation(recovery, change):
    r = recovery
    if change == "config":
        r.verify.side_effect = ValueError("profile drift")
    elif change == "cid":
        r.state["Id"] = "e" * 64
    elif change == "root":
        import json

        path = r.directory / "launch.json"
        doc = json.loads(path.read_bytes())
        doc["root_inode"] += 1
        path.write_text(json.dumps(doc))
    elif change == "launch":
        (r.directory / "launch.json").unlink()
    else:
        (r.directory / "identity.json").unlink()
    with pytest.raises((ProcessError, OSError, ValueError)):
        recover(r)
    assert not r.actions
    assert not (r.directory / "stop.json").exists()


@pytest.mark.parametrize(
    "failure", ["rm", "daemon_present", "kernel_present", "child_present"]
)
def test_unknown_physical_stop_never_journaled(recovery, monkeypatch, failure):
    r = recovery
    if failure == "rm":
        r.supervisor.cli._command.side_effect = ProcessError("timeout")
    elif failure == "daemon_present":
        r.supervisor.cli._command.side_effect = lambda args: (0, b"")
    else:
        r.gone.side_effect = lambda identity: (
            failure == "child_present" and identity == r.identity.leader
        )
        monkeypatch.setattr(module.time, "monotonic", Mock(side_effect=[10, 10, 13]))
    with pytest.raises(ProcessError):
        recover(r)
    assert not (r.directory / "stop.json").exists()


def test_cleanup_failure_replays_existing_exact_stop(recovery, monkeypatch):
    r = recovery
    cleanup = Mock(side_effect=OSError("cleanup interrupted"))
    monkeypatch.setattr(module.RunningController, "_cleanup", cleanup)
    with pytest.raises(OSError):
        recover(r)
    assert (r.directory / "stop.json").exists()
    cleanup.side_effect = None
    assert recover(r) == r.stop
    assert len(r.actions) == 1


@pytest.mark.parametrize("running", [True, False])
def test_prior_boot_identity_is_never_bound_to_running_new_unit(recovery, running):
    from uuid import uuid4

    from packages.research_runtime.process_contracts import canonical

    r = recovery
    old_identity = r.identity.model_copy(update={"boot_id": uuid4()})
    old_child = r.child.model_copy(
        update={"identity_sha256": digest(old_identity.model_dump(mode="json"))}
    )
    (r.directory / "identity.json").write_bytes(
        canonical(old_identity.model_dump(mode="json"))
    )
    (r.directory / "child.json").write_bytes(
        canonical(old_child.model_dump(mode="json"))
    )
    if running:
        r.state["State"]["Running"] = True
        with pytest.raises(ProcessError, match="restarted"):
            recover(r)
        assert not r.actions
    else:
        r.existing[0] = None
        r.gone.return_value = False  # Old boot is gone even if numeric PID is reused.
        result = recover(r)
        assert result.identity_sha256 == digest(old_identity.model_dump(mode="json"))
        assert not r.actions


def test_live_identity_mismatch_refuses_deletion(recovery):
    from dataclasses import replace

    r = recovery
    r.state["State"]["Running"] = True
    r.gone.return_value = False
    r.observer.observe_pair.return_value = (
        replace(r.identity.leader, start_ticks=999),
        r.child.controller,
    )
    with pytest.raises(ProcessError, match="kernel_changed"):
        recover(r)
    assert not r.actions


@pytest.mark.parametrize("cid", [None, "", "bad", "a" * 63, "A" * 64, 123])
def test_unobserved_cleanup_rejects_noncanonical_container_id(cid):
    cli = SimpleNamespace(_command=Mock(), _inspect=Mock())
    with pytest.raises(ProcessError, match="container_changed"):
        module._remove_unobserved(
            SimpleNamespace(cli=cli), SimpleNamespace(runtime_name="fixed"), {"Id": cid}
        )
    cli._command.assert_not_called()
