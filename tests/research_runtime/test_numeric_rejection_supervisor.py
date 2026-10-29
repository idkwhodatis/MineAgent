"""Transport/state doubles run the real supervisor flow; no Docker proof claim."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from packages.research_runtime.numeric_process_journal import (
    NumericIdentity,
    NumericStop,
)
from packages.research_runtime.process_contracts import ProcessError, digest
from runtimes.factor_execution.protected_output import rejection_metadata
from runtimes.research_supervisor import numeric_supervisor as ns
from runtimes.research_supervisor.supervisor import operation_path, write_record
from tests.research_runtime.test_numeric_issuer import bound


def setup(monkeypatch, root, *, code=1, fail=None):
    intent, inputs = bound()
    supervisor = ns.NumericSupervisor(intent.admission.image_id)
    token = ns._mint_numeric_authority(intent, inputs, {})
    state = {"Id": "c" * 64, "State": {"Running": True}}
    supervisor.client = SimpleNamespace(
        deadline=None,
        _inspect=lambda _: state,
        _command=lambda args: (0, ("c" * 64).encode()),
    )
    monkeypatch.setattr(supervisor, "_verify", lambda *a: state)
    reader = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(ns.AttachStream, "connect", lambda *a, **k: reader)
    monkeypatch.setattr(
        ns, "read_numeric_frame", lambda *a: (3, rejection_metadata("result.h5", code))
    )
    monkeypatch.setattr(ns, "require_live_numeric_frame", lambda *a: None)
    observed = []

    def identity(_state, launch):
        value = NumericIdentity(
            schema_version="mineagent.numeric-process-identity.v2",
            admission_sha256=digest(intent.admission.model_dump(mode="json")),
            launch_sha256=digest(launch.model_dump(mode="json")),
            container_id=state["Id"],
            image_id=intent.admission.image_id,
            root_device=launch.root_device,
            root_inode=launch.root_inode,
            init_pid=100,
            init_start_ticks=200,
            boot_id=uuid4(),
            pid_namespace_inode=300,
        )
        observed.append(value)
        return value

    monkeypatch.setattr(supervisor, "_observe_identity", identity)
    eof = []

    def complete(*_):
        eof.append(True)
        if fail == "eof":
            raise ProcessError("numeric_stream_trailing")

    monkeypatch.setattr(supervisor, "_complete_original_stream", complete)
    stops = []

    def stop(_root, admission, *, original_deadline=None):
        stops.append(original_deadline)
        if fail == "stop":
            raise ProcessError("numeric_process_uncertain")
        value = NumericStop(
            schema_version="mineagent.numeric-process-stop.v2",
            launch_sha256=observed[0].launch_sha256,
            identity_sha256=digest(observed[0].model_dump(mode="json")),
            container_id=state["Id"],
            confirmed_absent=True,
            kernel_identity_absent=True,
            observed_at=intent.not_after,
            deadline_bound_met=fail != "late",
        )
        write_record(
            operation_path(root, admission) / "control" / "stop.json",
            value.model_dump(mode="json"),
        )
        return value

    monkeypatch.setattr(supervisor, "stop_and_verify", stop)
    return supervisor, token, intent, eof, stops


@pytest.mark.parametrize("code", [0, 1, -9])
def test_explicit_successor_persists_only_after_eof_and_stop(
    monkeypatch, tmp_path, code
):
    supervisor, token, intent, eof, stops = setup(monkeypatch, tmp_path, code=code)
    result = supervisor.execute_with_rejection(
        tmp_path, token, on_identity=lambda _: None
    )
    assert result.child_exit_code == code and eof == [True]
    assert len(stops) == 1 and stops[0] == token.deadline + 1
    path = operation_path(tmp_path, intent.admission)
    assert (path / "control" / "rejection.json").exists()
    assert not (path / "control" / "extraction.json").exists()


@pytest.mark.parametrize("fail", ["eof", "stop", "late", "legacy"])
def test_missing_positive_completion_never_leaves_rejection(
    monkeypatch, tmp_path, fail
):
    supervisor, token, intent, _eof, _stops = setup(monkeypatch, tmp_path, fail=fail)
    execute = (
        supervisor.execute if fail == "legacy" else supervisor.execute_with_rejection
    )
    with pytest.raises(ProcessError):
        execute(tmp_path, token, on_identity=lambda _: None)
    assert not (
        operation_path(tmp_path, intent.admission) / "control" / "rejection.json"
    ).exists()
