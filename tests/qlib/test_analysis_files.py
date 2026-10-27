"""Real bounded private-file reads with synthetic journals; not Docker proof."""

import hashlib
import os

import pytest

from packages.research_runtime.process_contracts import ProcessError, digest
from runtimes.research_supervisor.analysis_journal import (
    read_analysis_journal,
    read_opaque_output,
)
from runtimes.research_supervisor.supervisor import operation_path, write_record
from tests.qlib.test_analysis_journal import records


def fixture(root):
    launch, identity, stop, extraction = records()
    launch = launch.model_copy(
        update={"root_device": root.stat().st_dev, "root_inode": root.stat().st_ino}
    )
    identity = identity.model_copy(
        update={
            "root_device": launch.root_device,
            "root_inode": launch.root_inode,
            "launch_sha256": digest(launch.model_dump(mode="json")),
        }
    )
    stop = stop.model_copy(
        update={
            "launch_sha256": identity.launch_sha256,
            "identity_sha256": digest(identity.model_dump(mode="json")),
        }
    )
    raw = b'{"synthetic":true}'
    extraction = extraction.model_copy(
        update={
            "launch_sha256": identity.launch_sha256,
            "identity_sha256": stop.identity_sha256,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
        }
    )
    path = operation_path(root, launch.intent.admission)
    (path / "control").mkdir(parents=True, mode=0o700)
    (path / "output").mkdir(mode=0o700)
    for name, value in (
        ("launch", launch),
        ("identity", identity),
        ("stop", stop),
        ("extraction", extraction),
    ):
        write_record(path / "control" / (name + ".json"), value.model_dump(mode="json"))
    output = path / "output" / "analysis.json"
    output.write_bytes(raw)
    output.chmod(0o600)
    return launch, identity, stop, extraction, raw, output


def test_exact_private_file_and_chain(tmp_path):
    values = fixture(tmp_path)
    assert (
        read_analysis_journal(tmp_path, values[0].intent.admission, require_output=True)
        == values[:4]
    )
    assert (
        read_opaque_output(tmp_path, values[0].intent.admission, values[3]) == values[4]
    )


@pytest.mark.parametrize("change", ["missing", "bytes", "mode", "symlink", "hardlink"])
def test_changed_result_rejected(tmp_path, change):
    launch, _, _, extraction, raw, path = fixture(tmp_path)
    if change == "missing":
        path.unlink()
    elif change == "bytes":
        path.write_bytes(b"x" * len(raw))
    elif change == "mode":
        path.chmod(0o644)
    elif change == "hardlink":
        os.link(path, tmp_path / "alias")
    else:
        path.unlink()
        other = tmp_path / "substitute"
        other.write_bytes(raw)
        other.chmod(0o600)
        path.symlink_to(other)
    with pytest.raises((ProcessError, OSError)):
        read_opaque_output(tmp_path, launch.intent.admission, extraction)
