"""Analysis issuer/transport doubles only; no Docker result or admission claim."""

import hashlib
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest

from packages.qlib_adapter.direct_wire import write_input
from packages.research_runtime.analysis_process_journal import AnalysisIntent
from packages.research_runtime.process_contracts import ProcessError, digest
from runtimes.qlib.analysis_transport import read_analysis_frame
from runtimes.qlib.protected_output import frame
from runtimes.research_supervisor.analysis_supervisor import (
    AnalysisAuthority,
    AnalysisSupervisor,
    _mint_analysis_authority,
    check_input_bytes,
)
from tests.qlib.test_analysis_journal import records
from tests.qlib.test_direct_wire import inputs


def bound():
    launch, *_ = records()
    value = inputs()
    raw = write_input(value)
    binding = launch.intent.inputs.model_copy(
        update={
            "snapshot_id": value.protocol.snapshot_id,
            "protocol_sha256": value.protocol.sha256,
            "analysis_input_sha256": hashlib.sha256(raw).hexdigest(),
            "analysis_input_size_bytes": len(raw),
        }
    )
    admission = launch.intent.admission.model_copy(
        update={"input_sha256": digest(binding.model_dump(mode="json"))}
    )
    now = datetime.now(UTC)
    intent = AnalysisIntent(
        id=admission.id,
        admission=admission,
        inputs=binding,
        issued_at=now,
        not_after=now + timedelta(seconds=8),
    )
    return intent, {"/input/analysis.json": raw}


def test_input_identity_and_no_detached_authority():
    intent, files = bound()
    check_input_bytes(intent, files)
    with pytest.raises(ProcessError):
        AnalysisAuthority(object(), intent, files, {})
    with pytest.raises(ProcessError):
        check_input_bytes(
            intent, {"/input/analysis.json": files["/input/analysis.json"] + b" "}
        )
    with pytest.raises(ProcessError):
        check_input_bytes(intent, {**files, "/input/source.py": b"evil"})


def test_one_shot_private_authority():
    intent, files = bound()
    authority = _mint_analysis_authority(
        intent, files, {"image_id": intent.admission.image_id}
    )
    assert authority.consume()["image_id"] == intent.admission.image_id
    with pytest.raises(ProcessError):
        authority.consume()


def test_closed_create_profile():
    intent, _ = bound()
    launch = records()[0].model_copy(
        update={
            "intent": intent,
            "runtime_name": "mineagent-analysis-" + str(intent.id),
        }
    )
    supervisor = AnalysisSupervisor(intent.admission.image_id, "sha256:" + "f" * 64)
    args = supervisor._create_args(launch, {"/input/analysis.json": "/private/input"})
    assert args[args.index("--network") + 1] == "none"
    assert args[args.index("--ipc") + 1] == "none"
    assert args[args.index("--memory") + 1] == "512m"
    assert args[args.index("--pids-limit") + 1] == "32"
    assert "type=bind,src=/private/input,dst=/input/analysis.json,readonly" in args
    assert not any("socket" in item for item in args)


def _require_precreate_with_private_umask(root):
    import os
    import stat

    from runtimes.research_supervisor.supervisor import operation_path

    os.umask(0o077)  # Isolated in the test subprocess, as in the hosted gate.
    intent, files = bound()
    authority = _mint_analysis_authority(intent, files, {})
    supervisor = AnalysisSupervisor(intent.admission.image_id, "sha256:" + "f" * 64)
    operation = operation_path(root, intent.admission)
    input_path = operation / "inputs" / "analysis.json"
    calls = []

    class BeforeDockerCreate(Exception):
        pass

    def command(args):
        assert args[0] == "create"
        assert input_path.read_bytes() == files["/input/analysis.json"]
        assert stat.S_IMODE(input_path.stat().st_mode) == 0o444
        assert (
            stat.S_IMODE((operation / "control" / "launch.json").stat().st_mode)
            == 0o600
        )
        assert f"type=bind,src={input_path},dst=/input/analysis.json,readonly" in args
        calls.append("create")
        raise BeforeDockerCreate()

    def stop(storage, admission, *, original_deadline):
        assert storage == root and admission == intent.admission
        assert original_deadline == authority.deadline + 1
        calls.append("cleanup")

    def unexpected_identity(_):
        raise AssertionError("must not observe an identity before Docker create")

    supervisor.client._command = command
    supervisor.stop_and_verify = stop
    with pytest.raises(BeforeDockerCreate):
        supervisor.execute(root, authority, on_identity=unexpected_identity)
    assert calls == ["create", "cleanup"]


def test_private_umask_preserves_evaluator_mount_mode_before_create(tmp_path):
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; from pathlib import Path; "
                "from tests.qlib.test_analysis_supervisor import "
                "_require_precreate_with_private_umask; "
                "_require_precreate_with_private_umask(Path(sys.argv[1]))"
            ),
            str(tmp_path),
        ],
        check=True,
        timeout=20,
    )


class Reader:
    def __init__(self, chunks):
        self.chunks = iter(chunks)

    def read_frame(self, **_):
        return next(self.chunks, None)


def test_protected_stream_cannot_accept_legacy_or_duplicates():
    data = frame(2, b"opaque")
    assert read_analysis_frame(Reader([(1, data[:7]), (1, data[7:])]), 0) == (
        2,
        b"opaque",
    )
    for invalid in (b"MANU2\0" + data[6:], data + data):
        with pytest.raises(ValueError):
            read_analysis_frame(Reader([(1, invalid)]), 0)
