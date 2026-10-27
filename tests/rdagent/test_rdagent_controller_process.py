"""Real bounded journal reads plus persistence doubles; no PG/Docker claim."""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app import rdagent_controller_process as service
from packages.research_runtime.process_contracts import ProcessError, canonical, digest
from packages.research_runtime.process_declarations_v2 import (
    HostTicketIPC,
    ProcessDeclarationV2,
)
from runtimes.rdagent_operational import process_journal as module
from runtimes.rdagent_operational.peer import KernelIdentity
from runtimes.rdagent_operational.trusted_report import report_bytes


def records(root):
    admission = ProcessDeclarationV2(
        schema_version="mineagent.process-admission.v2",
        id=uuid4(),
        owner_id=uuid4(),
        run_id=uuid4(),
        attempt_id=uuid4(),
        generation=1,
        role="rd_orchestrator",
        logical_operation_id=uuid4(),
        launch_ordinal=1,
        profile=module.PROFILE,
        input_sha256="a" * 64,
        reservation_id=uuid4(),
        image_id="sha256:" + "b" * 64,
        ipc=HostTicketIPC(ipc_policy="host_ticket", ticket_sha256="c" * 64),
    )
    now = datetime.now(UTC)
    intent = module.ControllerIntent(
        schema_version="mineagent.rd-controller-intent.v1",
        id=uuid4(),
        admission=admission,
        bootstrap_sha256="a" * 64,
        bootstrap_size_bytes=512,
        input_layout="single_bootstrap_json_no_other_mounts",
        issued_at=now,
        not_after=now + timedelta(seconds=60),
    )
    info = root.stat()
    launch = module.ControllerLaunch(
        schema_version="mineagent.rd-controller-launch.v1",
        intent=intent,
        runtime_name="mineagent-rd-controller-" + str(admission.id),
        root_device=info.st_dev,
        root_inode=info.st_ino,
    )
    leader = KernelIdentity(100, 1000, "pid:[42]", "0::/unit\n", 1, (0,) * 4, (0,) * 4)
    identity = module.ControllerIdentity(
        schema_version="mineagent.rd-controller-identity.v1",
        admission_sha256=digest(admission.model_dump(mode="json")),
        launch_sha256=digest(launch.model_dump(mode="json")),
        container_id="d" * 64,
        image_id=admission.image_id,
        root_device=info.st_dev,
        root_inode=info.st_ino,
        boot_id=uuid4(),
        leader=leader,
    )
    kernel = KernelIdentity(
        101, 1001, "pid:[42]", "0::/unit\n", 100, (65533,) * 4, (65533,) * 4
    )
    child = module.ControllerChild(
        schema_version="mineagent.rd-controller-child.v1",
        identity_sha256=digest(identity.model_dump(mode="json")),
        admission_sha256=identity.admission_sha256,
        controller=kernel,
        namespace_pids=(101, 2),
        namespace_pid=2,
        started_report_sha256=hashlib.sha256(
            report_bytes("started", 2, 1001)
        ).hexdigest(),
        peer_pid=101,
        peer_uid=65533,
        peer_gid=65533,
    )
    stop = module.ControllerStop(
        schema_version="mineagent.rd-controller-stop.v1",
        launch_sha256=identity.launch_sha256,
        identity_sha256=child.identity_sha256,
        child_sha256=digest(child.model_dump(mode="json")),
        container_id=identity.container_id,
        confirmed_absent=True,
        kernel_identity_absent=True,
        child_kernel_identity_absent=True,
    )
    return launch, identity, child, stop


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


def test_actual_files_full_identity_child_stop(tmp_path):
    values = records(tmp_path)
    write(tmp_path, values)
    result = module.read_controller_journal(
        tmp_path, values[0].intent.admission, with_child=True, with_stop=True
    )
    assert result == values
    # Stop reader discovers already-retained child even if DB ingestion lagged.
    assert (
        module.read_controller_journal(
            tmp_path, values[0].intent.admission, with_stop=True
        )
        == values
    )


def test_bootstrap_failure_stop_has_no_invented_child(tmp_path):
    launch, identity, _, stop = records(tmp_path)
    stop = stop.model_copy(
        update={"child_sha256": None, "child_kernel_identity_absent": None}
    )
    write(tmp_path, (launch, identity, None, stop), include_child=False)
    result = module.read_controller_journal(
        tmp_path, launch.intent.admission, with_stop=True
    )
    assert result[2] is None and result[3].child_sha256 is None


def test_stop_cannot_omit_retained_child(tmp_path):
    launch, identity, child, stop = records(tmp_path)
    stop = stop.model_copy(
        update={"child_sha256": None, "child_kernel_identity_absent": None}
    )
    write(tmp_path, (launch, identity, child, stop))
    with pytest.raises(ProcessError, match="omits_retained_child"):
        module.read_controller_journal(
            tmp_path, launch.intent.admission, with_stop=True
        )


@pytest.mark.parametrize(
    "change", ["symlink", "mode", "duplicate", "oversize", "hash", "root"]
)
def test_actual_journal_drift_rejected(tmp_path, change):
    values = records(tmp_path)
    directory = write(tmp_path, values)
    path = directory / "identity.json"
    if change == "symlink":
        path.unlink()
        path.symlink_to(directory / "launch.json")
    elif change == "mode":
        path.chmod(0o644)
    elif change == "duplicate":
        path.write_bytes(b'{"a":1,"a":1}')
    elif change == "oversize":
        path.write_bytes(b" " * 16385)
    else:
        payload = json.loads(path.read_bytes())
        payload["launch_sha256" if change == "hash" else "root_inode"] = (
            "e" * 64 if change == "hash" else 1
        )
        path.write_bytes(canonical(payload))
    with pytest.raises((ProcessError, OSError, ValueError)):
        module.read_controller_journal(
            tmp_path, values[0].intent.admission, with_child=True, with_stop=True
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("namespace_pids", (999, 2)),
        ("namespace_pid", 3),
        ("peer_pid", 999),
        ("peer_uid", 0),
        ("started_report_sha256", "f" * 64),
    ],
)
def test_child_mapping_substitutions_rejected(tmp_path, field, value):
    child = records(tmp_path)[2]
    payload = child.model_dump(mode="json")
    payload[field] = value
    with pytest.raises(ValueError):
        module.ControllerChild.model_validate_json(json.dumps(payload))


def test_same_namespace_sibling_is_not_controller(tmp_path):
    launch, identity, child, _stop = records(tmp_path)
    child = child.model_copy(update={"controller": replace(child.controller, ppid=999)})
    with pytest.raises(ProcessError):
        module.validate_records(launch, identity, child)


@pytest.mark.parametrize("size,seconds", [(262145, 60), (1, 61), (1, 0)])
def test_bootstrap_and_original_deadline_bounds(tmp_path, size, seconds):
    intent = records(tmp_path)[0].intent
    payload = intent.model_dump(mode="json")
    payload["bootstrap_size_bytes"] = size
    payload["not_after"] = (intent.issued_at + timedelta(seconds=seconds)).isoformat()
    with pytest.raises(ValueError):
        module.ControllerIntent.model_validate_json(json.dumps(payload))


def test_persistence_ingestion_reads_files_not_caller_receipts(tmp_path, monkeypatch):
    values = records(tmp_path)
    write(tmp_path, values)
    launch, _identity, child, stop = values
    state = SimpleNamespace(status="launch_intended")
    receipts = {
        "launch_intent": SimpleNamespace(payload=launch.intent.model_dump(mode="json"))
    }
    monkeypatch.setattr(
        service, "_persisted", lambda *args: (launch.intent.admission, state, receipts)
    )
    import backend.app.research_process_services as shared

    def receipt(session, admission, kind, payload):
        receipts[kind] = SimpleNamespace(payload=payload)

    monkeypatch.setattr(shared, "_receipt", receipt)
    session = SimpleNamespace(flush=lambda: None)
    service.record_controller_identity(
        session, object(), launch.intent.admission.id, storage_root=tmp_path
    )
    assert state.status == "running" and "controller_child" not in receipts
    service.record_controller_child(
        session, object(), launch.intent.admission.id, storage_root=tmp_path
    )
    assert receipts["controller_child"].payload == child.model_dump(mode="json")
    service.record_controller_stopped(
        session, object(), launch.intent.admission.id, storage_root=tmp_path
    )
    assert state.status == "stopped" and receipts["stop"].payload == stop.model_dump(
        mode="json"
    )
    service.record_controller_stopped(
        session, object(), launch.intent.admission.id, storage_root=tmp_path
    )


def test_storage_floor_preserves_existing_conservative_reservation():
    assert module.STORAGE_FLOOR == 67_174_400
    assert (
        module.STORAGE_FLOOR > 16 * 1024**2 + 65536 + 2 * module.BOOTSTRAP_CAP + 1024**2
    )


@pytest.mark.parametrize(
    "field",
    ["confirmed_absent", "kernel_identity_absent", "child_kernel_identity_absent"],
)
def test_integer_cannot_impersonate_stop_boolean(tmp_path, field):
    payload = records(tmp_path)[3].model_dump(mode="json")
    payload[field] = 1
    with pytest.raises(ValueError):
        module.ControllerStop.model_validate_json(json.dumps(payload))


def test_admission_requires_fenced_resources_exact_bootstrap_and_floor(
    tmp_path, monkeypatch
):
    from pydantic import BaseModel

    import backend.app.rdagent_controller_services as controller
    import backend.app.research_broker_services as broker
    import backend.app.research_lifecycle as lifecycle
    import backend.app.research_process_services as shared
    from packages.factors.contracts import canonical_bytes

    class Runtime(BaseModel):
        orchestrator_image: str

    class Config(BaseModel):
        runtime: Runtime

    class Bootstrap(BaseModel):
        config: Config

    launch = records(tmp_path)[0]
    bootstrap = Bootstrap(
        config=Config(
            runtime=Runtime(orchestrator_image=launch.intent.admission.image_id)
        )
    )
    raw = canonical_bytes(bootstrap)
    admission = launch.intent.admission.model_copy(
        update={"input_sha256": hashlib.sha256(raw).hexdigest()}
    )
    intent = launch.intent.model_copy(
        update={
            "admission": admission,
            "bootstrap_sha256": admission.input_sha256,
            "bootstrap_size_bytes": len(raw),
        }
    )
    fence = SimpleNamespace(
        **{
            k: getattr(admission, k)
            for k in ("owner_id", "run_id", "attempt_id", "generation")
        }
    )
    events = []
    monkeypatch.setattr(
        lifecycle,
        "require_neutral_transaction",
        lambda s, f, resource_scope: events.append(("fence", resource_scope)),
    )
    monkeypatch.setattr(
        controller,
        "expected_operational_bootstrap",
        lambda *a: (SimpleNamespace(id=admission.logical_operation_id), bootstrap),
    )
    monkeypatch.setattr(
        broker,
        "require_reservation",
        lambda *a, **kw: events.append(("reservation", kw["minimum"])),
    )
    monkeypatch.setattr(shared, "_receipt", lambda *a: events.append(("receipt", a[2])))
    session = SimpleNamespace(
        scalar=lambda statement: None,
        add=lambda row: events.append(("add", type(row).__name__)),
        flush=lambda: None,
    )
    assert service.persist_controller_intent(session, fence, intent) == admission
    assert events[0] == ("fence", "processes")
    assert events[1][0] == "reservation"
    assert events[1][1].storage_bytes == module.STORAGE_FLOOR
    assert events[1][1].process_slots == 1 and events[1][1].wall_milliseconds == 60000
    assert events[-1] == ("receipt", "launch_intent")
    events.clear()
    with pytest.raises(ProcessError, match="bootstrap_identity"):
        service.persist_controller_intent(
            session,
            fence,
            intent.model_copy(update={"bootstrap_size_bytes": len(raw) + 1}),
        )
    assert events == [("fence", "processes")]
