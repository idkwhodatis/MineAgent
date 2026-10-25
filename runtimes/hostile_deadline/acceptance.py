"""Explicit hosted Docker lifecycle acceptance. Never executes candidates locally."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import struct
import subprocess
import tempfile
import time
from pathlib import Path

from runtimes.hostile_deadline.attach_stream import AttachStream
from runtimes.hostile_deadline.contract import (
    BASE_IMAGE,
    MASKED_PATHS,
    MOBY_PROFILES_COMMIT,
    SECCOMP_SHA256,
    image_inventory,
    seccomp_policy,
    verify_inspect,
)
from runtimes.hostile_deadline.deadline import parse_status
from runtimes.hostile_deadline.protected_output import (
    MARKER,
    parse_frame,
    validate_export,
)

WALL_SECONDS = 8.0
STOP_TOLERANCE_SECONDS = 1.0
COMPLETED_MARKER = b"mineagent-fixed-hostile-checks-complete-v1\n"


def parse_bootstrap_diagnostic(raw: bytes) -> dict | None:
    """Discard anything except a closed, bounded trusted failure location."""
    if type(raw) is not bytes or not 0 < len(raw) <= 256:
        return None
    try:
        item = json.loads(raw)
    except (ValueError, UnicodeError):
        return None
    if (
        type(item) is not dict
        or set(item) != {"schema", "location", "errno"}
        or item["schema"] != "mineagent.trusted-bootstrap-failure.v1"
        or type(item["location"]) is not str
        or re.fullmatch(
            r"unknown|(?:main|candidate|require_caps|drop_capabilities|prctl|status|close_fds|quiesce_and_hold):[1-9][0-9]{0,5}",
            item["location"],
        )
        is None
        or (
            item["errno"] is not None
            and (type(item["errno"]) is not int or not 1 <= item["errno"] <= 4095)
        )
    ):
        return None
    return item


def assert_stop_timing(start: float, stopped: float) -> float:
    elapsed = stopped - start
    if not 0 <= elapsed <= WALL_SECONDS + STOP_TOLERANCE_SECONDS:
        raise AssertionError(
            "positive stop exceeded original absolute deadline tolerance"
        )
    return elapsed


ATTACK = r"""
import ctypes, errno, os, signal, time
assert os.getuid() == 65533 and os.getgid() == 65533
status = {key: value.rstrip() for line in open('/proc/self/status') if ':\t' in line for key, value in [line.split(':\t', 1)]}
assert all(int(status[k], 16) == 0 for k in ('CapEff', 'CapPrm', 'CapInh', 'CapAmb'))
assert status['NoNewPrivs'] == '1' and status['Seccomp'] == '2'
for change in (lambda: os.setuid(0), lambda: os.setgid(0)):
    try: change()
    except PermissionError: pass
    else: raise AssertionError('regained privileged identity')
for sig in (signal.SIGSTOP, signal.SIGKILL, signal.SIGTERM):
    try: os.kill(1, sig)
    except PermissionError: pass
    else: raise AssertionError('signalled trusted PID1')
libc = ctypes.CDLL(None, use_errno=True)
assert libc.ptrace(16, 1, 0, 0) == -1 and ctypes.get_errno() == errno.EPERM
try: open('/proc/1/mem', 'rb')
except PermissionError: pass
else: raise AssertionError('opened trusted PID1 memory')
try: os.listdir('/proc/1/fd')
except PermissionError: pass
else: raise AssertionError('inspected trusted inherited descriptors')
assert all(int(fd) < 4 for fd in os.listdir('/proc/self/fd'))
# Detached grandchildren escape process groups but cannot escape PID namespace.
for _ in range(3):
    child = os.fork()
    if child == 0:
        os.setsid()
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        while True: time.sleep(.05)
open('/output/marker.tmp','wb').write(b'mineagent-fixed-hostile-checks-complete-v1\n')
os.replace('/output/marker.tmp','/output/checks-complete')
while True: time.sleep(.05)
"""
EARLY_EXIT = "import os,time\nopen('/output/result.bin','wb').write(b'fixed retained fixture')\ntry: open('/status/forged','w')\nexcept PermissionError: pass\nelse: raise AssertionError('status writable')\nif os.fork()==0:\n os.setsid()\n while True: time.sleep(.05)\ntime.sleep(.5)\n"


def command(*args: str, timeout: float = 30) -> str:
    return subprocess.run(
        args, check=True, capture_output=True, text=True, timeout=timeout
    ).stdout.strip()


def inspect(cid: str, *, timeout: float = 30) -> dict:
    return json.loads(command("docker", "inspect", cid, timeout=timeout))[0]


def process_identity(pid: int) -> str | None:
    try:
        # Field22 begins at index19 after the final ')' of the comm field.
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except FileNotFoundError:
        return None


def require_local_docker() -> None:
    if os.environ.get("DOCKER_HOST") not in (
        None,
        "unix:///var/run/docker.sock",
    ) or os.environ.get("DOCKER_CONTEXT") not in (None, "default"):
        raise RuntimeError(
            "proof requires local default Docker, not remote PID observations"
        )
    context = json.loads(command("docker", "context", "inspect"))[0]
    if context["Endpoints"]["docker"]["Host"] != "unix:///var/run/docker.sock":
        raise RuntimeError("proof requires local Docker socket")
    if not stat.S_ISSOCK(Path("/var/run/docker.sock").stat().st_mode):
        raise RuntimeError("local Docker endpoint is not a socket")


def require_process_observation(
    observed: dict[int, str | None], init_pid: int, namespace_pids: list[int]
) -> None:
    if type(init_pid) is not int or init_pid <= 1 or init_pid not in observed:
        raise AssertionError("inspect PID1 missing from local process observation")
    if (
        not namespace_pids
        or namespace_pids[0] != init_pid
        or namespace_pids[-1] != 1
        or len(namespace_pids) < 2
    ):
        raise AssertionError(
            "local process is not the inspected private-namespace PID1"
        )
    if not observed or any(identity is None for identity in observed.values()):
        raise AssertionError("incomplete local host PID identity observation")


def read_live_frame(reader: AttachStream, deadline: float) -> tuple[int, bytes]:
    from runtimes.hostile_deadline.protected_output import MAGIC, MAX_FRAME

    data = bytearray()
    while True:
        item = reader.read_frame(deadline=deadline)
        if item is None:
            raise AssertionError("EOF before live protected frame")
        channel, chunk = item
        if channel != 1:
            diagnostics = [
                parse_bootstrap_diagnostic(line) for line in chunk.splitlines()
            ]
            raise AssertionError(f"trusted failure diagnostics={diagnostics!r}")
        data.extend(chunk)
        if len(data) > MAX_FRAME + 11:
            raise AssertionError("oversized protected frame")
        if len(data) >= 11:
            if data[:6] != MAGIC:
                raise AssertionError("invalid protected magic")
            size = struct.unpack(">I", data[7:11])[0]
            if size > MAX_FRAME:
                raise AssertionError("oversized protected payload")
            if len(data) >= 11 + size:
                return parse_frame(bytes(data))


def live_process(pid: int, expected_start: str) -> bool:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except FileNotFoundError:
        return False
    return fields[19] == expected_start and fields[0] not in {"Z", "X", "x"}


def require_exporter_observation(rows: list[str], init_pid: int) -> tuple[int, str]:
    """Exact daemon-owned docker-top unit inventory, not candidate helper names."""
    if not 1 <= len(rows) <= 16:
        raise AssertionError("invalid fixed unit process bound")
    parsed = {}
    for row in rows:
        fields = row.split()
        if len(fields) != 5 or not all(
            field.isascii() and field.isdecimal() for field in fields[:4]
        ):
            raise AssertionError("invalid kernel process inventory")
        pid, uid, ppid, pidns = map(int, fields[:4])
        if pid <= 1 or pid in parsed or pidns <= 0:
            raise AssertionError("ambiguous kernel process inventory")
        parsed[pid] = (uid, ppid, pidns, fields[4])
    if init_pid not in parsed or parsed[init_pid][0] != 0:
        raise AssertionError("missing inspected root PID1")
    if any(row[0] == 65533 for row in parsed.values()):
        raise AssertionError("candidate remains before exporter observation")
    matches = [
        pid
        for pid, row in parsed.items()
        if row == (0, init_pid, parsed[init_pid][2], "ma-export-v2")
    ]
    if len(matches) != 1:
        raise AssertionError("missing uniquely scoped trusted exporter")
    pid = matches[0]
    start = process_identity(pid)
    if start is None or not live_process(pid, start):
        raise AssertionError("exporter is not live")

    def cgroup(process):
        with open(f"/proc/{process}/cgroup", "rb") as stream:
            raw = stream.read(4097)
        if not raw or len(raw) > 4096:
            raise AssertionError("invalid bounded cgroup identity")
        return raw

    if cgroup(pid) != cgroup(init_pid) or not live_process(pid, start):
        raise AssertionError("exporter identity changed or wrong cgroup")
    return pid, start


def remaining_command_budget(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise AssertionError("original stop observation budget exhausted")
    return min(30.0, remaining)


def kernel_exited(pid: int, expected_start: str) -> bool:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except FileNotFoundError:
        return True
    # A pinned zombie has exited and cannot execute; it is not yet reaped.
    # Final acceptance separately requires identity absence and Docker stopped.
    return fields[19] != expected_start or fields[0] in {"Z", "X", "x"}


def wait_kernel_exit(observed: dict[int, str | None], deadline: float) -> None:
    """Observe exit independently of Docker's possibly blocked IO finalization."""
    if not observed or any(start is None for start in observed.values()):
        raise AssertionError("missing pinned kernel identities")
    while True:
        if time.monotonic() >= deadline:
            raise AssertionError("kernel exit exceeded original absolute deadline")
        if all(kernel_exited(pid, start) for pid, start in observed.items()):
            return
        time.sleep(min(0.01, remaining_command_budget(deadline)))


def terminal_state(
    cid: str,
    reader,
    observed: dict[int, str | None],
    deadline: float,
    *,
    no_reader: bool,
) -> dict:
    if no_reader:
        wait_kernel_exit(observed, deadline)
        progress("no_reader", "kernel_exit_before_reader_close")
        reader.close()
    while True:
        document = inspect(cid, timeout=remaining_command_budget(deadline))
        if not document["State"]["Running"]:
            return document
        time.sleep(min(0.03, remaining_command_budget(deadline)))


def progress(mode: str, phase: str) -> None:
    print(
        json.dumps(
            {
                "schema": "mineagent.hostile-proof-progress.v2",
                "case": mode,
                "phase": phase,
            },
            separators=(",", ":"),
        ),
        flush=True,
    )


def exercise(
    image: str, folder: Path, *, mode: str, inventory: dict, masked_paths: list[str]
) -> dict:
    sources = {
        "hostile_deadline": ATTACK,
        "early_completion": EARLY_EXIT,
        "no_reader": "import time\nopen('/output/result.bin','wb').write(b'x'*4194304)\ntime.sleep(.5)\n",
        "collector_killed": "import time\nopen('/output/result.bin','wb').write(b'x'*4194304)\ntime.sleep(.5)\n",
        "oversized": "import time\nopen('/output/result.bin','wb').write(b'x'*4194305)\ntime.sleep(.5)\n",
        "fifo": "import os,time\nos.mkfifo('/output/result.bin',0o644)\ntime.sleep(.5)\n",
        "symlink": "import os,time\nos.symlink('/etc/passwd','/output/result.bin')\ntime.sleep(.5)\n",
        "unreadable": "import os,time\nopen('/output/result.bin','wb').write(b'fixed')\nos.chmod('/output/result.bin',0o600)\ntime.sleep(.5)\n",
    }
    source = folder / (mode + ".py")
    source.write_text(sources[mode])
    source.chmod(0o444)
    started_monotonic = time.monotonic()
    original_monotonic_deadline = started_monotonic + WALL_SECONDS
    deadline = str(time.time_ns() // 1_000_000 + int(WALL_SECONDS * 1000))
    observation_deadline = original_monotonic_deadline + STOP_TOLERANCE_SECONDS

    def bounded_command(*args):
        return command(*args, timeout=remaining_command_budget(observation_deadline))

    def bounded_inspect(container):
        return inspect(
            container, timeout=remaining_command_budget(observation_deadline)
        )

    progress(mode, "begin")

    args = [
        "docker",
        "create",
        "--log-driver",
        "none",
        "--network",
        "none",
        "--ipc",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--cap-add",
        "SETUID",
        "--cap-add",
        "SETGID",
        "--cap-add",
        "KILL",
        "--security-opt",
        "no-new-privileges",
        "--security-opt",
        f"seccomp={Path(__file__).with_name('seccomp.default.json').resolve()}",
        "--memory",
        "256m",
        "--memory-swap",
        "256m",
        "--pids-limit",
        "16",
        "--cpus",
        "1",
        "--user",
        "0:0",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=4m,uid=65533,gid=65533,mode=0700",
        "--tmpfs",
        "/output:rw,noexec,nosuid,nodev,size=16m,uid=0,gid=0,mode=0733",
        "--tmpfs",
        "/status:rw,noexec,nosuid,nodev,size=64k,uid=0,gid=0,mode=0700",
        "--mount",
        f"type=bind,src={source},dst=/input/candidate.py,readonly",
        image,
        deadline,
    ]
    cid = ""
    sibling = subprocess.Popen(["sleep", "60"])
    attach = owner = reader = None
    try:
        cid = bounded_command(*args)
        document = bounded_inspect(cid)
        verify_inspect(
            document,
            image=image,
            candidate_path=str(source),
            deadline_ms=deadline,
            inventory=inventory,
            masked_paths=masked_paths,
        )
        # Handshake before starting; no log driver, TTY or stdin can forge/miss a
        # trusted frame. This reader is not a deadline/liveness authority.
        reader = AttachStream.connect(cid, deadline=original_monotonic_deadline)
        cidfile = folder / (mode + ".started")
        owner = subprocess.Popen(
            [
                "python",
                "-c",
                "import pathlib,subprocess,sys,time; subprocess.run(['docker','start',sys.argv[1]],check=True,capture_output=True); pathlib.Path(sys.argv[2]).write_text('started'); time.sleep(60)",
                cid,
                str(cidfile),
            ]
        )
        until = min(time.monotonic() + 3, original_monotonic_deadline)
        while not cidfile.exists() and time.monotonic() < until:
            if owner.poll() is not None:
                raise AssertionError("launch owner failed")
            time.sleep(0.01)
        if not cidfile.exists():
            raise AssertionError("launch owner missed original deadline")
        document = bounded_inspect(cid)
        if not document["State"]["Running"]:
            raise AssertionError("trusted bootstrap stopped before observation")
        owner.kill()
        owner.wait(timeout=1)
        attach = subprocess.Popen(
            ["docker", "attach", "--no-stdin", "--sig-proxy=false", cid],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.1)
        attach.kill()
        attach.wait(timeout=1)
        rows = bounded_command("docker", "top", cid, "-eo", "pid").splitlines()[1:]
        observed = {
            int(row.strip()): process_identity(int(row.strip())) for row in rows
        }
        init_pid = document["State"]["Pid"]
        init_status = parse_status(
            Path(f"/proc/{init_pid}/status").read_text().splitlines()
        )
        require_process_observation(
            observed, init_pid, [int(p) for p in init_status["NSpid"].split()]
        )
        exporter_observed = False
        if mode == "no_reader":
            # A deliberately large frame exercises the unread attach channel.
            # Do not read it: observe the protected exporter still alive twice.
            until = min(time.monotonic() + 3, original_monotonic_deadline - 0.5)
            exporter = None
            while time.monotonic() < until:
                rows = bounded_command(
                    "docker", "top", cid, "-eo", "pid,uid,ppid,pidns,comm"
                ).splitlines()[1:]
                if any(row.split()[-1] == "ma-export-v2" for row in rows):
                    exporter, identity = require_exporter_observation(rows, init_pid)
                    break
                time.sleep(0.02)
            if exporter is None:
                raise AssertionError("missing protected backpressure exporter")
            time.sleep(0.25)
            rows = bounded_command(
                "docker", "top", cid, "-eo", "pid,uid,ppid,pidns,comm"
            ).splitlines()[1:]
            if require_exporter_observation(rows, init_pid) != (exporter, identity):
                raise AssertionError("exporter identity changed under unread stream")
            observed[exporter] = identity
            exporter_observed = True
        elif mode == "collector_killed":
            reader.close()
        else:
            kind, payload = read_live_frame(reader, original_monotonic_deadline)
            if (
                time.monotonic() >= original_monotonic_deadline
                or not bounded_inspect(cid)["State"]["Running"]
            ):
                raise AssertionError(
                    "protected frame did not arrive while original unit alive"
                )
            if mode == "hostile_deadline":
                if (kind, payload) != (1, MARKER):
                    raise AssertionError("missing required completed attack marker")
            elif mode == "early_completion":
                if kind != 2 or validate_export(payload) != b"fixed retained fixture":
                    raise AssertionError("missing exact quiescent output")
            elif (kind, payload) != (3, b""):
                raise AssertionError("unsafe output was not explicitly rejected")
            rows = bounded_command("docker", "top", cid, "-eo", "pid,uid").splitlines()[
                1:
            ]
            candidate_pids = [
                int(row.split()[0]) for row in rows if row.split()[1] == "65533"
            ]
            if mode == "hostile_deadline":
                if len(candidate_pids) != 4 or any(
                    not live_process(pid, process_identity(pid))
                    for pid in candidate_pids
                ):
                    raise AssertionError(
                        "fixed hostile candidate tree not alive at marker observation"
                    )
                observed.update({pid: process_identity(pid) for pid in candidate_pids})
            elif candidate_pids:
                raise AssertionError(
                    "candidate processes remain after quiescent export"
                )
            channels = reader.collect_until_eof(
                deadline=original_monotonic_deadline + STOP_TOLERANCE_SECONDS
            )
            if channels[1] or channels[2]:
                raise AssertionError("duplicate or trailing protected channel bytes")
        progress(mode, "evidence_observed")
        final = terminal_state(
            cid, reader, observed, observation_deadline, no_reader=mode == "no_reader"
        )
        if final["State"]["Running"] or final["State"]["ExitCode"] != 124:
            raise AssertionError("wrong terminal lifecycle")
        elapsed = assert_stop_timing(started_monotonic, time.monotonic())
        if sibling.poll() is not None:
            raise AssertionError("unrelated host sibling was killed")
        for pid, start in observed.items():
            if process_identity(pid) == start:
                raise AssertionError("namespace descendant survived trusted PID1 exit")
        progress(mode, "positive_stop_verified")
        return {
            "case": mode,
            "exit_code": 124,
            "deadline_epoch_ms": int(deadline),
            "configured_wall_seconds": WALL_SECONDS,
            "positive_stop_elapsed_seconds": elapsed,
            "stop_tolerance_seconds": STOP_TOLERANCE_SECONDS,
            "original_deadline_verified": True,
            "completed_attack_marker_verified": mode == "hostile_deadline",
            "positive_stop_verified": True,
            "observed_processes": len(observed),
            "complete_local_pid_identities_verified": True,
            "inspected_private_pid1_verified": True,
            "launch_owner_killed": True,
            "attach_client_killed": True,
            "host_sibling_survived": True,
            "all_observed_descendants_stopped": True,
            "root_only_quiescence_and_bounded_output_verified": mode
            == "early_completion",
            "non_reader_live_exporter_observed_and_stopped": exporter_observed,
            "invalid_output_rejected": mode
            in {"oversized", "fifo", "symlink", "unreadable"},
            "primary_collector_killed": mode == "collector_killed",
            "kernel_exit_before_reader_close_verified": mode == "no_reader",
        }
    finally:
        if reader is not None:
            reader.close()
        for process in (attach, owner, sibling):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=3)
        if cid:
            command("docker", "rm", "--force", cid)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-hostile-docker-tests", action="store_true", required=True
    )
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.image.startswith("sha256:") or len(args.image) != 71:
        raise ValueError("use exact locally built image identity")
    require_local_docker()
    # Resolve immutable base metadata before the measured attempt. Inherited ENV
    # is accepted only by equality with this already reviewed digest, not a tag.
    command("docker", "pull", BASE_IMAGE)
    base = json.loads(command("docker", "image", "inspect", BASE_IMAGE))[0]
    built = json.loads(command("docker", "image", "inspect", args.image))[0]
    inventory = image_inventory(base, built)
    masked_paths = sorted(
        [
            *MASKED_PATHS,
            *(
                str(path)
                for path in Path("/sys/devices/system/cpu").glob(
                    "cpu[0-9]*/thermal_throttle"
                )
            ),
        ]
    )
    seccomp_policy()  # Verify checked-in upstream policy bytes before launch.
    with tempfile.TemporaryDirectory(prefix="mineagent-deadline-") as directory:
        # Docker daemon needs parent traversal; inputs are fixed maintained fixtures.
        folder = Path(directory)
        folder.chmod(0o755)
        results = [
            exercise(
                args.image,
                folder,
                mode=mode,
                inventory=inventory,
                masked_paths=masked_paths,
            )
            for mode in (
                "hostile_deadline",
                "early_completion",
                "no_reader",
                "collector_killed",
                "oversized",
                "fifo",
                "symlink",
                "unreadable",
            )
        ]
    args.output.write_text(
        json.dumps(
            {
                "scope": "disabled_hostile_deadline_lifecycle_only",
                "output_abi": "protected_stdout_v2",
                "image": args.image,
                "cases": results,
                "image_config_inventory": inventory,
                "image_config_inventory_sha256": hashlib.sha256(
                    json.dumps(
                        inventory, sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest(),
                "seccomp_upstream_commit": MOBY_PROFILES_COMMIT,
                "seccomp_sha256": SECCOMP_SHA256,
                "masked_paths": masked_paths,
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
