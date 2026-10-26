"""Structural and protected-FD tests; synthetic records are NOT runtime acceptance."""

import hashlib
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from packages.research_runtime.numeric_process_journal import (
    NumericExtraction,
    NumericIdentity,
    NumericInput,
    NumericInputFile,
    NumericIntent,
    NumericLaunch,
    NumericStop,
    numeric_admission,
    validate_numeric_journal,
)
from packages.research_runtime.process_contracts import ProcessError, canonical, digest
from packages.research_runtime.process_declarations_v2 import (
    NoIPC,
    ProcessDeclarationV2,
    require_v2_launch_authority,
)
from runtimes.research_supervisor.numeric_journal import (
    read_numeric_journal,
    read_opaque_output,
)
from runtimes.research_supervisor.supervisor import operation_path, write_record


def journal(root, role="factor_cutoff_candidate"):
    from packages.rdagent_adapter.numeric_profile import (
        CANDIDATE_LIMITS,
        NORMALIZER_LIMITS,
    )
    from packages.research_runtime.numeric_process_journal import PROFILES

    paths = CANDIDATE_LIMITS if role == "factor_cutoff_candidate" else NORMALIZER_LIMITS
    inputs = NumericInput(
        schema_version="mineagent.numeric-process-input.v2",
        source_object_id=uuid4(),
        source_sha256="a" * 64,
        prefix_sha256="a" * 64,
        cutoff=datetime(2020, 1, 1, tzinfo=UTC),
        files=tuple(
            NumericInputFile(destination=p, sha256="a" * 64, size_bytes=10)
            for p in sorted(paths)
        ),
    )
    admission = ProcessDeclarationV2(
        schema_version="mineagent.process-admission.v2",
        **{
            k: uuid4()
            for k in (
                "id",
                "owner_id",
                "run_id",
                "attempt_id",
                "logical_operation_id",
                "reservation_id",
            )
        },
        generation=1,
        launch_ordinal=1,
        role=role,
        profile=PROFILES[role],
        input_sha256=digest(inputs.model_dump(mode="json")),
        image_id="sha256:" + "b" * 64,
        ipc=NoIPC(ipc_policy="none", ticket_sha256=None),
    )
    now = datetime.now(UTC)
    intent = NumericIntent(
        schema_version="mineagent.numeric-process-intent.v2",
        id=uuid4(),
        admission=admission,
        inputs=inputs,
        issued_at=now,
        not_after=now + timedelta(seconds=8),
    )
    st = root.stat()
    launch = NumericLaunch(
        schema_version="mineagent.numeric-process-launch.v2",
        intent=intent,
        runtime_name="mineagent-numeric-" + str(admission.id),
        root_device=st.st_dev,
        root_inode=st.st_ino,
    )
    identity = NumericIdentity(
        schema_version="mineagent.numeric-process-identity.v2",
        admission_sha256=digest(admission.model_dump(mode="json")),
        launch_sha256=digest(launch.model_dump(mode="json")),
        container_id="c" * 64,
        image_id=admission.image_id,
        root_device=launch.root_device,
        root_inode=launch.root_inode,
        init_pid=100,
        init_start_ticks=200,
        boot_id=uuid4(),
        pid_namespace_inode=300,
    )
    stop = NumericStop(
        schema_version="mineagent.numeric-process-stop.v2",
        launch_sha256=identity.launch_sha256,
        identity_sha256=digest(identity.model_dump(mode="json")),
        container_id=identity.container_id,
        confirmed_absent=True,
        kernel_identity_absent=True,
        observed_at=launch.intent.not_after,
        deadline_bound_met=True,
    )
    payload = b"opaque native bytes, never parsed"
    extraction = NumericExtraction(
        schema_version="mineagent.numeric-process-extraction.v2",
        launch_sha256=identity.launch_sha256,
        identity_sha256=stop.identity_sha256,
        protocol="MANU2",
        basename="result.h5" if role == "factor_cutoff_candidate" else "values.parquet",
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        protected_frame_complete=True,
        producer_quiesced=True,
    )
    path = operation_path(root, admission)
    (path / "control").mkdir(parents=True, mode=0o700)
    (path / "output").mkdir(mode=0o700)
    for name, value in (
        ("launch", launch),
        ("identity", identity),
        ("stop", stop),
        ("extraction", extraction),
    ):
        write_record(path / "control" / (name + ".json"), value.model_dump(mode="json"))
    output = path / "output" / extraction.basename
    output.write_bytes(payload)
    output.chmod(0o600)
    return admission, launch, identity, stop, extraction, payload, path


@pytest.mark.parametrize("role", ["factor_cutoff_candidate", "factor_normalizer"])
def test_protected_journal_roundtrip_does_not_enable_launch(tmp_path, role):
    admission, launch, identity, stop, extraction, payload, _path = journal(
        tmp_path, role
    )
    assert read_numeric_journal(tmp_path, admission, require_output=True) == (
        launch,
        identity,
        stop,
        extraction,
    )
    assert read_opaque_output(tmp_path, admission, extraction) == payload
    with pytest.raises(ProcessError, match="not_activated"):
        require_v2_launch_authority(admission)


@pytest.mark.parametrize(
    "kind", ["symlink", "hardlink", "wrong_mode", "changed", "extra", "directory"]
)
def test_opaque_output_rejects_substitution(tmp_path, kind):
    admission, _, _, _, extraction, payload, path = journal(tmp_path)
    output = path / "output" / extraction.basename
    if kind == "symlink":
        target = path / "target"
        output.rename(target)
        output.symlink_to(target)
    elif kind == "hardlink":
        os.link(output, path / "other")
    elif kind == "wrong_mode":
        output.chmod(0o644)
    elif kind == "changed":
        output.write_bytes(b"x" * len(payload))
    elif kind == "extra":
        output.write_bytes(payload + b"x")
    else:
        output.unlink()
        output.mkdir()
    with pytest.raises((ProcessError, OSError)):
        read_opaque_output(tmp_path, admission, extraction)


@pytest.mark.parametrize(
    "field",
    [
        "admission_sha256",
        "launch_sha256",
        "container_id",
        "image_id",
        "init_pid",
        "extra",
    ],
)
def test_identity_substitution_fails(tmp_path, field):
    _, launch, identity, stop, extraction, _, _ = journal(tmp_path)
    value = identity.model_dump(mode="json")
    value[field] = 0 if field == "init_pid" else "d" * 64
    with pytest.raises((ValueError, ProcessError)):
        other = NumericIdentity.model_validate_json(canonical(value))
        validate_numeric_journal(launch, other, stop, extraction)


@pytest.mark.parametrize(
    "change",
    [
        {"confirmed_absent": False},
        {"kernel_identity_absent": False},
        {"container_id": "d" * 64},
        {"identity_sha256": "d" * 64},
    ],
)
def test_stop_is_not_inferred_from_output(tmp_path, change):
    _, launch, identity, stop, extraction, _, _ = journal(tmp_path)
    value = {**stop.model_dump(mode="json"), **change}
    with pytest.raises((ValueError, ProcessError)):
        validate_numeric_journal(
            launch,
            identity,
            NumericStop.model_validate_json(canonical(value)),
            extraction,
        )


def test_wrong_root_or_admission_fails(tmp_path):
    admission, _, _, _, _, _, _ = journal(tmp_path)
    with pytest.raises((ProcessError, OSError)):
        read_numeric_journal(
            tmp_path, admission.model_copy(update={"id": uuid4()}), require_output=True
        )


def test_manipulated_model_copy_is_revalidated(tmp_path):
    admission, launch, _, _, _, _, _ = journal(tmp_path)
    with pytest.raises((ValueError, ProcessError)):
        numeric_admission(admission.model_copy(update={"profile": "arbitrary.v2"}))
    with pytest.raises(ValueError):
        NumericIntent.model_validate(
            launch.intent.model_copy(
                update={"not_after": launch.intent.issued_at + timedelta(seconds=11)}
            )
        )


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "old_receipt",
        "owner",
        "admission",
        "output",
        "identity",
        "missing_stop",
        "missing_output",
        "under_reserved",
    ],
)
def test_numeric_cold_closure_cross_checks_real_fields_not_v1_assumptions(
    tmp_path, mutation
):
    import json

    from backend.app.numeric_process_backup import validate_numeric_closure_unit
    from packages.research_runtime.process_persistence_codec import encode_row

    admission, launch, identity, stop, extraction, _payload, path = journal(tmp_path)
    row = {
        key: str(value) if type(value).__name__ == "UUID" else value
        for key, value in encode_row(admission).items()
    }
    row["created_at"] = "2026-01-01T00:00:00Z"
    scope = {
        key: row[key] for key in ("owner_id", "run_id", "attempt_id", "generation")
    }
    reservation = {
        "id": row["reservation_id"],
        **scope,
        "purpose": "process_unit",
        "amounts": {
            "process_slots": 1,
            "wall_milliseconds": 60000,
            "storage_bytes": 67_174_400,
        },
    }
    receipts = {}
    for kind, value in (
        ("launch_intent", launch.intent),
        ("launch_identity", identity),
        ("stop", stop),
        ("extraction", extraction),
    ):
        item = {
            "id": str(uuid4()),
            "admission_id": row["id"],
            **scope,
            "kind": kind,
            "payload": value.model_dump(mode="json"),
            "payload_sha256": digest(value.model_dump(mode="json")),
        }
        receipts[item["id"]] = item
    indexes = {
        "research_budget_reservations": {row["reservation_id"]: reservation},
        "research_process_states": {row["id"]: {"status": "stopped", **scope}},
        "research_process_receipts": receipts,
    }
    files = {
        str(p.relative_to(tmp_path)): p.read_bytes()
        for p in path.rglob("*")
        if p.is_file()
    }
    if mutation == "old_receipt":
        next(iter(receipts.values()))["payload_sha256"] = "f" * 64
    elif mutation == "owner":
        next(iter(receipts.values()))["owner_id"] = str(uuid4())
    elif mutation == "admission":
        row["input_sha256"] = "f" * 64
    elif mutation == "output":
        files[str((path / "output" / extraction.basename).relative_to(tmp_path))] = (
            b"substitution"
        )
    elif mutation == "identity":
        target = next(r for r in receipts.values() if r["kind"] == "launch_identity")
        target["payload"]["container_id"] = "f" * 64
        target["payload_sha256"] = digest(target["payload"])
    elif mutation == "missing_stop":
        receipts = {key: r for key, r in receipts.items() if r["kind"] != "stop"}
        indexes["research_process_receipts"] = receipts
    elif mutation == "missing_output":
        del files[str((path / "output" / extraction.basename).relative_to(tmp_path))]
    elif mutation == "under_reserved":
        reservation["amounts"]["storage_bytes"] -= 1

    def check(key, sha, size):
        assert len(files[key]) == size and hashlib.sha256(files[key]).hexdigest() == sha

    def validate():
        allowed = set()
        validate_numeric_closure_unit(
            row,
            indexes,
            {key: {} for key in files},
            check,
            lambda key: json.loads(files[key]),
            allowed,
            set(),
        )
        assert allowed == set(files)

    if mutation is None:
        validate()
    else:
        with pytest.raises((ValueError, ProcessError, AssertionError, KeyError)):
            validate()


def test_control_growth_after_stat_exceeds_hard_cap(tmp_path, monkeypatch):
    import runtimes.research_supervisor.numeric_journal as reader

    path = tmp_path / "control.json"
    original = b'{"a":""}'
    path.write_bytes(original)
    path.chmod(0o600)
    real_read = reader.os.read
    grew = False

    def growing_read(fd, size):
        nonlocal grew
        if not grew:
            grew = True
            path.write_bytes(b'{"a":"' + b"x" * 16377 + b'"}')
        return real_read(fd, size)

    monkeypatch.setattr(reader.os, "read", growing_read)
    with pytest.raises(ProcessError, match="numeric_process_journal"):
        reader.read_numeric_record(path)


def test_control_same_length_rewrite_after_read_fails(tmp_path, monkeypatch):
    import runtimes.research_supervisor.numeric_journal as reader

    path = tmp_path / "control.json"
    path.write_bytes(b'{"a":1}')
    path.chmod(0o600)
    real_read = reader.os.read
    changed = False

    def changing_read(fd, size):
        nonlocal changed
        raw = real_read(fd, size)
        if not changed:
            changed = True
            path.write_bytes(b'{"a":2}')
            os.utime(path, ns=(1, 2))
        return raw

    monkeypatch.setattr(reader.os, "read", changing_read)
    with pytest.raises(ProcessError, match="numeric_process_journal"):
        reader.read_numeric_record(path)


@pytest.mark.parametrize("mutate", [False, True])
def test_terminal_barrier_binds_full_launch_not_only_mutual_hashes(
    tmp_path, monkeypatch, mutate
):
    from types import SimpleNamespace

    import backend.app.numeric_process_services as service

    admission, launch, identity, stop, _, _, _ = journal(tmp_path)
    if mutate:
        identity = identity.model_copy(update={"launch_sha256": "f" * 64})
        stop = stop.model_copy(
            update={
                "launch_sha256": "f" * 64,
                "identity_sha256": digest(identity.model_dump(mode="json")),
            }
        )
    receipts = {
        kind: SimpleNamespace(payload=value.model_dump(mode="json"))
        for kind, value in (
            ("launch_intent", launch.intent),
            ("launch_identity", identity),
            ("stop", stop),
        )
    }
    monkeypatch.setattr(
        service,
        "_persisted",
        lambda *_: (admission, SimpleNamespace(status="stopped"), receipts),
    )
    assert service.numeric_stop_committed(None, SimpleNamespace(id=admission.id)) is (
        not mutate
    )


def test_resolver_rejects_removed_retained_output(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import backend.app.numeric_process_services as service

    admission, _, _, _, extraction, _, path = journal(tmp_path)
    receipt = SimpleNamespace(admission=admission)
    monkeypatch.setattr(service, "_resolve", lambda *_: (receipt, extraction))
    (path / "output" / extraction.basename).unlink()
    with pytest.raises((OSError, ProcessError)):
        service.resolve_numeric_output(None, None, admission.id, storage_root=tmp_path)


@pytest.mark.parametrize("role", ["factor_cutoff_candidate", "factor_normalizer"])
def test_normalizer_manifest_is_not_the_original_candidate_prefix(tmp_path, role):
    _, launch, _, _, _, _, _ = journal(tmp_path, role)
    inputs = launch.intent.inputs.model_copy(
        update={
            "files": tuple(
                item.model_copy(update={"sha256": "f" * 64})
                if item.destination == "/input/manifest.json"
                else item
                for item in launch.intent.inputs.files
            )
        }
    )
    admission = launch.intent.admission.model_copy(
        update={"input_sha256": digest(inputs.model_dump(mode="json"))}
    )
    intent = launch.intent.model_copy(update={"inputs": inputs, "admission": admission})
    if role == "factor_cutoff_candidate":
        with pytest.raises(ValueError, match="prefix commitment"):
            NumericIntent.model_validate(intent)
    else:
        assert NumericIntent.model_validate(intent).inputs.prefix_sha256 != "f" * 64


def test_overdue_observation_is_containment_not_timely_result(tmp_path):
    _, launch, identity, stop, _, _, _ = journal(tmp_path)
    late = stop.model_copy(
        update={"observed_at": launch.intent.not_after + timedelta(seconds=2)}
    )
    with pytest.raises(ProcessError, match="deadline_observation"):
        validate_numeric_journal(launch, identity, late)
    containment = late.model_copy(update={"deadline_bound_met": False})
    assert (
        validate_numeric_journal(launch, identity, containment)[2].deadline_bound_met
        is False
    )
