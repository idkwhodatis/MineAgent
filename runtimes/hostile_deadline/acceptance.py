"""Explicit hosted Docker lifecycle acceptance. Never executes candidates locally."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import tempfile
import time
from pathlib import Path

from runtimes.hostile_deadline.contract import (
    BASE_IMAGE,
    MASKED_PATHS,
    MOBY_PROFILES_COMMIT,
    SECCOMP_SHA256,
    image_inventory,
    seccomp_policy,
    verify_inspect,
)

WALL_SECONDS = 8.0
STOP_TOLERANCE_SECONDS = 1.0
COMPLETED_MARKER = b"mineagent-fixed-hostile-checks-complete-v1\n"


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
status = dict(line.rstrip().split(':\t', 1) for line in open('/proc/self/status') if ':\t' in line)
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
open('/output/checks-complete','wb').write(b'mineagent-fixed-hostile-checks-complete-v1\n')
while True: time.sleep(.05)
"""
EARLY_EXIT = "import os,time\nopen('/output/result.bin','wb').write(b'fixed retained fixture')\ntry: open('/status/forged','w')\nexcept PermissionError: pass\nelse: raise AssertionError('status writable')\nif os.fork()==0:\n os.setsid()\n while True: time.sleep(.05)\ntime.sleep(.5)\n"


def command(*args: str) -> str:
    return subprocess.run(
        args, check=True, capture_output=True, text=True, timeout=30
    ).stdout.strip()


def inspect(cid: str) -> dict:
    return json.loads(command("docker", "inspect", cid))[0]


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


def exercise(
    image: str, folder: Path, *, early: bool, inventory: dict, masked_paths: list[str]
) -> dict:
    source = folder / ("early.py" if early else "attack.py")
    source.write_text(EARLY_EXIT if early else ATTACK)
    source.chmod(0o444)
    started_monotonic = time.monotonic()
    original_monotonic_deadline = started_monotonic + WALL_SECONDS
    deadline = str(time.time_ns() // 1_000_000 + int(WALL_SECONDS * 1000))
    args = [
        "docker",
        "run",
        "--detach",
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
        "/output:rw,noexec,nosuid,nodev,size=16m,uid=65533,gid=65533,mode=0700",
        "--tmpfs",
        "/status:rw,noexec,nosuid,nodev,size=64k,uid=0,gid=0,mode=0700",
        "--mount",
        f"type=bind,src={source},dst=/input/candidate.py,readonly",
        image,
        deadline,
    ]
    cid = ""
    sibling = subprocess.Popen(["sleep", "60"])
    attach = owner = None
    try:
        # A disposable host-side launch owner creates the container and waits.
        # Killing it cannot be the mechanism that enforces the container deadline.
        cidfile = folder / ("early.cid" if early else "attack.cid")
        owner = subprocess.Popen(
            [
                "python",
                "-c",
                "import pathlib,subprocess,sys,time; r=subprocess.run(sys.argv[2:],check=True,capture_output=True,text=True); pathlib.Path(sys.argv[1]).write_text(r.stdout.strip()); time.sleep(60)",
                str(cidfile),
                *args,
            ]
        )
        until = time.monotonic() + 5
        while not cidfile.exists() and time.monotonic() < until:
            if owner.poll() is not None:
                raise AssertionError("launch owner failed")
            time.sleep(0.02)
        cid = cidfile.read_text().strip()
        if len(cid) != 64 or any(c not in "0123456789abcdef" for c in cid):
            raise AssertionError("invalid container identity")
        document = inspect(cid)
        verify_inspect(
            document,
            image=image,
            candidate_path=str(source),
            deadline_ms=deadline,
            inventory=inventory,
            masked_paths=masked_paths,
        )
        owner.kill()
        owner.wait(timeout=3)
        attach = subprocess.Popen(
            ["docker", "attach", "--no-stdin", "--sig-proxy=false", cid],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.15)
        attach.kill()
        attach.wait(timeout=3)
        if not early and not inspect(cid)["State"]["Running"]:
            raise AssertionError("candidate failed bootstrap or protection assertions")
        readiness = time.monotonic() + 2
        while True:
            rows = command("docker", "top", cid, "-eo", "pid").splitlines()[1:]
            observed = {
                int(row.strip()): process_identity(int(row.strip())) for row in rows
            }
            if early or len(observed) >= 5:
                break
            if time.monotonic() >= readiness:
                raise AssertionError(
                    "hostile fixture did not reach all protection checks/forks"
                )
            time.sleep(0.02)
        init_pid = document["State"]["Pid"]
        init_status = dict(
            line.rstrip().split(":\t", 1)
            for line in Path(f"/proc/{init_pid}/status").read_text().splitlines()
            if ":\t" in line
        )
        require_process_observation(
            observed, init_pid, [int(pid) for pid in init_status["NSpid"].split()]
        )
        if not early:
            marker = folder / "attack-checks-complete"
            marker_until = min(time.monotonic() + 1, original_monotonic_deadline)
            while time.monotonic() < marker_until:
                copied = subprocess.run(
                    ["docker", "cp", f"{cid}:/output/checks-complete", str(marker)],
                    capture_output=True,
                    check=False,
                    timeout=1,
                )
                if copied.returncode == 0 and marker.read_bytes() == COMPLETED_MARKER:
                    break
                time.sleep(0.02)
            if not marker.is_file() or marker.read_bytes() != COMPLETED_MARKER:
                raise AssertionError("missing required completed attack marker")
        if early:
            receipt = folder / "quiescent.json"
            until = time.monotonic() + 3
            while time.monotonic() < until:
                copied = subprocess.run(
                    ["docker", "cp", f"{cid}:/status/quiescent.json", str(receipt)],
                    capture_output=True,
                    check=False,
                    timeout=3,
                )
                if copied.returncode == 0:
                    break
                time.sleep(0.02)
            value = json.loads(receipt.read_text())
            assert value == {
                "schema": "mineagent.hostile-quiescence.v1",
                "child_exit_code": 0,
                "descendants_reaped": True,
            }
            assert len(command("docker", "top", cid, "-eo", "pid").splitlines()) == 2
            artifact = folder / "retained-result.bin"
            command("docker", "cp", f"{cid}:/output/result.bin", str(artifact))
            assert artifact.read_bytes() == b"fixed retained fixture"
            assert inspect(cid)["State"]["Running"]
        expected_code = 124
        until = original_monotonic_deadline + STOP_TOLERANCE_SECONDS
        while inspect(cid)["State"]["Running"] and time.monotonic() < until:
            time.sleep(0.05)
        final = inspect(cid)
        if final["State"]["Running"] or final["State"]["ExitCode"] != expected_code:
            raise AssertionError(f"wrong terminal lifecycle: {final['State']}")
        stopped_monotonic = time.monotonic()
        elapsed = assert_stop_timing(started_monotonic, stopped_monotonic)
        if sibling.poll() is not None:
            raise AssertionError("unrelated host sibling was killed")
        for pid, start in observed.items():
            if process_identity(pid) == start:
                raise AssertionError("namespace descendant survived trusted PID1 exit")
        return {
            "case": "early_completion" if early else "hostile_deadline",
            "exit_code": expected_code,
            "deadline_epoch_ms": int(deadline),
            "configured_wall_seconds": WALL_SECONDS,
            "positive_stop_elapsed_seconds": elapsed,
            "stop_tolerance_seconds": STOP_TOLERANCE_SECONDS,
            "original_deadline_verified": True,
            "completed_attack_marker_verified": not early,
            "positive_stop_verified": True,
            "observed_processes": len(observed),
            "complete_local_pid_identities_verified": True,
            "inspected_private_pid1_verified": True,
            "launch_owner_killed": True,
            "attach_client_killed": True,
            "host_sibling_survived": True,
            "all_observed_descendants_stopped": True,
            "root_only_quiescence_and_bounded_output_verified": early,
        }
    finally:
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
                early=False,
                inventory=inventory,
                masked_paths=masked_paths,
            ),
            exercise(
                args.image,
                folder,
                early=True,
                inventory=inventory,
                masked_paths=masked_paths,
            ),
        ]
    args.output.write_text(
        json.dumps(
            {
                "scope": "disabled_hostile_deadline_lifecycle_only",
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
