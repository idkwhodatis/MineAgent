"""Synthetic immutable records plus real bounded files, not Docker/DB acceptance."""

import hashlib
import shutil

import pytest

from packages.research_runtime.process_contracts import ProcessError, digest
from runtimes.openevolve_evaluator.output_reader import (
    read_native_journal,
    read_stopped_native_bytes,
)
from runtimes.openevolve_evaluator.protected_output import export_payload
from runtimes.research_supervisor.openevolve_native_supervisor import NativeSupervisor
from runtimes.research_supervisor.supervisor import write_record
from tests.openevolve.test_native_evaluator_journal import records
from tests.openevolve.test_native_evaluator_supervisor import persisted_launch


def fixture(root):
    admission, path = persisted_launch(root)
    supervisor = NativeSupervisor(admission.image_id, "sha256:" + "f" * 64)
    launch = supervisor._launch(path)
    _, identity, stop, extraction = records()
    identity = identity.model_copy(
        update={
            "launch_sha256": digest(launch.model_dump(mode="json")),
            "admission_sha256": digest(admission.model_dump(mode="json")),
            "image_id": admission.image_id,
            "root_device": launch.root_device,
            "root_inode": launch.root_inode,
        }
    )
    stop = stop.model_copy(
        update={
            "launch_sha256": identity.launch_sha256,
            "identity_sha256": digest(identity.model_dump(mode="json")),
            "observed_at": launch.intent.not_after,
        }
    )
    files = {
        "metrics.json": b"{}",
        "stderr.bin": b"",
        "stdout.bin": b"native observation\n",
    }
    payload = export_payload(files, 0)
    extraction = extraction.model_copy(
        update={
            "launch_sha256": identity.launch_sha256,
            "identity_sha256": stop.identity_sha256,
            "manifest_sha256": launch.intent.inputs.manifest_sha256,
            "native_artifacts_sha256": launch.intent.inputs.native_artifacts_sha256,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
        }
    )
    for name, value in [
        ("identity", identity),
        ("stop", stop),
        ("extraction", extraction),
    ]:
        write_record(path / "control" / (name + ".json"), value.model_dump(mode="json"))
    (path / "output").mkdir(mode=0o700)
    output = path / "output" / "native-export.bin"
    output.write_bytes(payload)
    output.chmod(0o600)
    return admission, path, files


def test_fresh_protected_payload_and_stop_checked_together(tmp_path):
    admission, path, files = fixture(tmp_path)
    value = read_stopped_native_bytes(tmp_path, admission)
    assert dict(value.files) == files
    assert (
        read_native_journal(tmp_path, admission, require_output=True)[3]
        == value.extraction
    )
    with pytest.raises(TypeError):
        value.files["metrics.json"] = b"forged"
    (path / "output" / "native-export.bin").unlink()
    assert read_native_journal(tmp_path, admission)[2] == value.stop
    with pytest.raises(ProcessError):
        read_stopped_native_bytes(tmp_path, admission)


def test_modified_retained_output_is_not_cached_authority(tmp_path):
    admission, path, _ = fixture(tmp_path)
    output = path / "output" / "native-export.bin"
    raw = output.read_bytes()
    output.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    with pytest.raises(ProcessError):
        read_stopped_native_bytes(tmp_path, admission)


def test_old_root_receipts_cannot_authorize_live_read_after_relocation(tmp_path):
    root = tmp_path / "source"
    root.mkdir(mode=0o700)
    admission, _, _ = fixture(root)
    moved = tmp_path / "moved"
    shutil.copytree(root, moved)
    with pytest.raises(ProcessError, match="root_identity"):
        read_stopped_native_bytes(moved, admission)
