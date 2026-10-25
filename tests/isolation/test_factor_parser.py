"""Actual Docker factor-parser isolation acceptance; no fake pass on missing runtime.

Build runtimes/factor_parser/Dockerfile, resolve its immutable sha256 image ID,
then set MINEAGENT_FACTOR_PARSER_IMAGE and MINEAGENT_FACTOR_ISOLATION_REQUIRED=1.
"""

import json
import os
import shutil
import time
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from packages.factors.storage import read_page, write_partition
from runtimes.factor_parser.supervisor import (
    DockerParser,
    ParserIsolationError,
    assert_quiescent,
    factor_fence,
    prepare_operation,
)


def fixture_row():
    instant = datetime(2026, 1, 2, 21, tzinfo=UTC)
    return {
        "event_time": instant,
        "session_date": date(2026, 1, 2),
        "listing_id": "XNAS:A",
        "factor_version_id": str(uuid4()),
        "available_at": instant,
        "value": 0.0,
        "value_state": "valid",
        "missing_reason": None,
        "input_window_start": date(2026, 1, 1),
        "input_window_end": date(2026, 1, 3),
        "input_evidence_digest": "a" * 64,
        "required_input_available_at": instant,
        "membership_available_at": instant,
        "lag_available_at": instant,
    }


@pytest.fixture
def contained(tmp_path):
    image = os.environ.get("MINEAGENT_FACTOR_PARSER_IMAGE")
    if not image or not shutil.which("docker") or os.getuid() == 0:
        if os.environ.get("MINEAGENT_FACTOR_ISOLATION_REQUIRED") == "1":
            pytest.fail("Required real factor parser Docker image/runtime unavailable")
        pytest.skip("Real factor parser Docker runtime not configured")
    root = tmp_path / "storage"
    operation = root / "factors" / str(uuid4()) / "staging" / str(uuid4())
    partition = write_partition([fixture_row()], tmp_path / "source.parquet")
    (root / "factors").mkdir(parents=True)
    with factor_fence(root) as fence:
        prepare_operation(
            root,
            operation,
            image_id=image,
            input_sha256=partition.sha256,
            fence_fd=fence,
        )
    for name in ("input", "output", "control"):
        (operation / name).mkdir(parents=True)
    (operation / "input" / "input.parquet").write_bytes(partition.path.read_bytes())
    return image, root, operation, partition


def test_real_factor_parser_roundtrip_and_removal(contained, monkeypatch):
    image, root, op, partition = contained
    monkeypatch.setenv("MINEAGENT_TEST_SECRET", "synthetic-supervisor-only")
    parser = DockerParser(image_id=image)
    report = parser.run(
        root, op, input_sha256=partition.sha256, deadline=time.monotonic() + 20
    )
    assert report["input_sha256"] == partition.sha256 and report["row_count"] == 1
    assert (
        read_page(op / "output" / "values.parquet", report["output_sha256"])[0]["value"]
        == 0
    )
    assert_quiescent(root)
    assert (
        json.loads((op / "control" / "stopped.json").read_text())["confirmed_absent"]
        is True
    )


class ProbeParser(DockerParser):
    probe = ""
    observed_exit = None

    def _stop(self, operation, launch):
        state = self._inspect(launch)
        if state is not None and not state["State"]["Running"]:
            self.observed_exit = state["State"].get("ExitCode")
        return super()._stop(operation, launch)

    def _create_args(self, launch, input_directory):
        args = super()._create_args(launch, input_directory)
        return [*args[:-1], "--entrypoint", "python", args[-1], "-c", self.probe]


@pytest.mark.parametrize(
    "probe",
    [
        "import time; time.sleep(60)",
        "import sys; sys.stdout.write('x'*20000); sys.stdout.flush()",
    ],
)
def test_real_hostile_execution_stops_and_cannot_publish(contained, monkeypatch, probe):
    image, root, op, partition = contained
    monkeypatch.setenv("MINEAGENT_TEST_SECRET", "synthetic-supervisor-only")
    parser = ProbeParser(image_id=image)
    parser.probe = probe
    with pytest.raises(ParserIsolationError):
        parser.run(
            root, op, input_sha256=partition.sha256, deadline=time.monotonic() + 20
        )
    assert_quiescent(root)
    assert not (op / "output" / "values.parquet").exists()


def test_real_network_environment_root_and_control_denial(contained, monkeypatch):
    image, root, op, partition = contained
    monkeypatch.setenv("MINEAGENT_TEST_SECRET", "synthetic-supervisor-only")
    parser = ProbeParser(image_id=image)
    parser.probe = """import os,socket
assert 'MINEAGENT_TEST_SECRET' not in os.environ
assert not os.path.exists('/var/run/docker.sock')
assert not os.path.exists('/control')
try:
    open('/root-write','w').write('bad')
except OSError: pass
else: raise AssertionError('root filesystem writable')
s=socket.socket();s.settimeout(.2)
try:
    s.connect(('1.1.1.1',443))
except OSError: pass
else: raise AssertionError('network available')
"""
    with pytest.raises(ParserIsolationError):
        parser.run(
            root, op, input_sha256=partition.sha256, deadline=time.monotonic() + 20
        )
    assert parser.observed_exit == 0, (
        "The sandbox assertions did not complete successfully"
    )
    assert_quiescent(root)


def test_real_supervisor_sigkill_retains_recovery_barrier(contained):
    import signal
    import subprocess
    import sys

    image, root, op, partition = contained
    code = """
import sys,time
from runtimes.factor_parser.supervisor import DockerParser
class Slow(DockerParser):
    def _create_args(self,launch,input_directory):
        args=super()._create_args(launch,input_directory)
        return [*args[:-1],'--entrypoint','python',args[-1],'-c','import time; time.sleep(60)']
Slow(image_id=sys.argv[1]).run(sys.argv[2],sys.argv[3],input_sha256=sys.argv[4],deadline=time.monotonic()+20)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", code, image, str(root), str(op), partition.sha256],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    recovery = DockerParser(image_id=image)
    try:
        deadline = time.monotonic() + 8
        while not (op / "control" / "container.json").exists():
            assert child.poll() is None, (
                "Supervisor exited before launch identity was durable"
            )
            assert time.monotonic() < deadline, "Container creation did not finish"
            time.sleep(0.02)
        # Kill only the dedicated process group that this test launched; Docker
        # daemon/container is deliberately outside it and must be reconciled.
        assert os.getpgid(child.pid) == child.pid
        os.killpg(child.pid, signal.SIGKILL)
        child.wait(timeout=3)
        with pytest.raises(ParserIsolationError):
            assert_quiescent(root)
        assert recovery.reconcile_stop(root, op), (
            "Exact owned container termination was not proven"
        )
        assert_quiescent(root)
    finally:
        if child.poll() is None:
            assert os.getpgid(child.pid) == child.pid
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=3)
        # Never claim absence on failed/uncertain reconciliation.
        assert recovery.reconcile_stop(root, op)
