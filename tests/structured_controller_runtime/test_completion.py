"""Real report parsing/journal files; Docker/kernel observations are explicit doubles."""

import json
from types import SimpleNamespace as NS

import pytest

from packages.research_runtime.process_contracts import ProcessError, canonical, digest
from runtimes.openevolve_structured.trusted_report import report_bytes
from runtimes.openevolve_structured_host import supervisor
from runtimes.openevolve_structured_host.completion_journal import (
    ControllerCompletion,
    read_controller_completion,
)
from runtimes.research_supervisor.supervisor import operation_path, write_record
from tests.structured_controller_runtime.fixtures import records
from tests.structured_controller_runtime.test_supervisor import Reader


def running(
    monkeypatch,
    root,
    *,
    child_exit=0,
    daemon_exit=124,
    oom=False,
    observed=100.1,
    eof=True,
):
    launch, identity, child, _ = records(root)
    operation = operation_path(root, launch.intent.admission)
    (operation / "control").mkdir(parents=True)
    for name, value in (("launch", launch), ("identity", identity), ("child", child)):
        write_record(
            operation / "control" / (name + ".json"), value.model_dump(mode="json")
        )
    state = NS(removed=False)

    def inspect(cid):
        return (
            None
            if state.removed
            else {
                "State": {
                    "Running": False,
                    "Status": "exited",
                    "ExitCode": daemon_exit,
                    "OOMKilled": oom,
                }
            }
        )

    def command(args):
        assert args == ["rm", identity.container_id]
        state.removed = True

    first = report_bytes("started", 2, 1001)
    last = report_bytes("quiescent", 2, 1001, child_exit)

    class ActualReader(Reader):
        def read_frame(self, *, deadline):
            result = super().read_frame(deadline=deadline)
            if result is None and not eof:
                raise TimeoutError("no original EOF")
            return result

    reader = ActualReader([(1, first), (1, last)])
    listener = NS(close=lambda: None)
    value = supervisor.RunningController(
        NS(cli=NS(_inspect=inspect, _command=command), inventory={}),
        root,
        launch,
        identity,
        100,
        reader,
        listener,
        listener,
    )
    value.child = child
    value.reports.next(101)
    monkeypatch.setattr(supervisor, "_kernel_gone", lambda identity: True)
    monkeypatch.setattr(supervisor, "verify_container", lambda *a, **kw: None)
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: observed)
    monkeypatch.setattr(supervisor.time, "monotonic_ns", lambda: int(observed * 1e9))
    return value, operation


def test_positive_finish_retains_actual_reports_and_stopped_chain(
    monkeypatch, tmp_path
):
    value, operation = running(monkeypatch, tmp_path)
    stop = value.finish()
    completion = read_controller_completion(tmp_path, value.launch.intent.admission)
    assert completion.reports_eof is True
    assert json.loads(completion.quiescent_report)["child_exit_code"] == 0
    assert completion.daemon_exit_code == 124 and completion.daemon_oom_killed is False
    assert completion.original_deadline_monotonic_ns == 100000000000
    assert completion.kernel_absent_monotonic_ns == 100100000000
    assert (
        stop.confirmed_absent and (operation / "control" / "completion.json").is_file()
    )
    assert value.finish() == stop


@pytest.mark.parametrize(
    "changes",
    [
        {"child_exit": 1},
        {"child_exit": -9},
        {"daemon_exit": 0},
        {"oom": True},
        {"observed": 99.9},
        {"observed": 101.1},
        {"eof": False},
    ],
)
def test_absence_does_not_fabricate_completion_on_failed_observation(
    monkeypatch, tmp_path, changes
):
    value, operation = running(monkeypatch, tmp_path, **changes)
    with pytest.raises((ValueError, TimeoutError, ProcessError)):
        value.finish()
    assert (operation / "control" / "stop.json").is_file()
    assert not (operation / "control" / "completion.json").exists()


def test_containment_only_finish_cannot_claim_report_completion(monkeypatch, tmp_path):
    value, operation = running(monkeypatch, tmp_path)
    value.finish(no_reader=True)
    assert (operation / "control" / "stop.json").is_file()
    assert not (operation / "control" / "completion.json").exists()


@pytest.mark.parametrize(
    "field,bad",
    [
        ("reports_eof", False),
        ("reports_eof", 1),
        ("daemon_oom_killed", True),
        ("daemon_exit_code", 0),
        ("kernel_absent_monotonic_ns", 1),
        ("stop_sha256", "f" * 64),
        ("quiescent_report_sha256", "e" * 64),
    ],
)
def test_retained_completion_tamper_rejected(monkeypatch, tmp_path, field, bad):
    value, operation = running(monkeypatch, tmp_path)
    value.finish()
    path = operation / "control" / "completion.json"
    raw = json.loads(path.read_bytes())
    raw[field] = bad
    path.chmod(0o600)
    path.write_bytes(canonical(raw))
    path.chmod(0o600)
    with pytest.raises((ValueError, TypeError)):
        read_controller_completion(tmp_path, value.launch.intent.admission)


def test_duplicate_or_extra_report_fields_rejected(monkeypatch, tmp_path):
    value, _ = running(monkeypatch, tmp_path)
    value.finish()
    data = read_controller_completion(
        tmp_path, value.launch.intent.admission
    ).model_dump()
    data["started_report"] = data["started_report"].replace(
        '"event":', '"extra":true,"event":'
    )
    with pytest.raises(ValueError):
        ControllerCompletion.model_validate(data)


def test_process_receipt_reloads_actual_completion_and_preserves_stop(
    monkeypatch, tmp_path
):
    from backend.app import oe_structured_controller_process_services as service
    from backend.app.factor_native_io import native_storage_guard

    value, operation = running(monkeypatch, tmp_path)
    stop = value.finish()
    (tmp_path / "factors").mkdir()
    admission, intent = value.launch.intent.admission, value.launch.intent
    receipts = {
        kind: NS(
            payload=item.model_dump(mode="json"),
            payload_sha256=digest(item.model_dump(mode="json")),
        )
        for kind, item in {
            "launch_intent": intent,
            "launch_identity": value.identity,
            "controller_child": value.child,
            "stop": stop,
        }.items()
    }
    monkeypatch.setattr(
        service,
        "_persisted",
        lambda *a: (admission, NS(status="stopped"), receipts, intent),
    )
    monkeypatch.setattr(service, "_current", lambda *a: None)

    def persist(session, exact, kind, payload):
        assert exact == admission and kind == "controller_completion"
        receipts[kind] = NS(payload=payload)
        return receipts[kind]

    monkeypatch.setattr(service, "_receipt", persist)
    with native_storage_guard(tmp_path):
        row = service.record_controller_completion(
            NS(), NS(), admission.id, storage_root=tmp_path
        )
    assert row.payload["reports_eof"] is True and receipts[
        "stop"
    ].payload == stop.model_dump(mode="json")
    (operation / "control" / "completion.json").unlink()
    with native_storage_guard(tmp_path), pytest.raises(FileNotFoundError):
        service.record_controller_completion(
            NS(), NS(), admission.id, storage_root=tmp_path
        )
