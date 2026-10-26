"""Maintained numeric fixture only, explicit Docker opt-in; not engine admission."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import subprocess
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from packages.factors.contracts import ExposureHistory
from packages.factors.fixtures import demo_inputs
from packages.rdagent_adapter.contracts import FactorResearchConfig
from packages.rdagent_adapter.host_evidence import research_snapshot_input
from packages.rdagent_adapter.numeric_profile import (
    CANDIDATE_LOCK_SHA256,
    NORMALIZER_LOCK_SHA256,
    candidate_inputs,
    normalizer_inputs,
)
from packages.rdagent_adapter.prefix import MarketObservation, project_prefix
from runtimes.factor_execution.attach_stream import AttachStream
from runtimes.factor_execution.protected_output import (
    MAGIC,
    MAX_FRAME,
    parse_frame,
    validate_export,
    validate_rejection,
)
from runtimes.factor_execution.runtime_contract import (
    BASE_IMAGE,
    MASKED_PATHS,
    SECCOMP_SHA256,
    image_inventory,
    verify_inspect,
)
from runtimes.factor_execution.verify_inventory import (
    verify as verify_runtime_inventory,
)
from runtimes.hostile_deadline.acceptance import (
    command,
    inspect,
    process_identity,
    remaining_command_budget,
    require_local_docker,
    require_process_observation,
    wait_kernel_exit,
)
from runtimes.hostile_deadline.deadline import parse_status
from runtimes.rdagent.probe_fixture import research_dict

WALL_SECONDS = 8.0
STOP_TOLERANCE_SECONDS = 1.0
EXPECTED_PARQUET_SHA256 = (
    "64238ad59bf679e43eae98c24e211966a5b016ed557b212905a0b53f4e18eca2"
)


def read_numeric_frame(reader, deadline):
    data = bytearray()
    while True:
        received = reader.read_frame(deadline=deadline)
        if received is None:
            raise ValueError("numeric output missing or truncated")
        stream, chunk = received
        if stream != 1 or len(data) + len(chunk) > MAX_FRAME + 11:
            raise ValueError("numeric output channel violation")
        data.extend(chunk)
        if len(data) >= 6 and data[:6] != MAGIC:
            raise ValueError("wrong numeric export version")
        if len(data) >= 11:
            expected = 11 + struct.unpack(">I", data[7:11])[0]
            if expected > MAX_FRAME + 11 or len(data) > expected:
                raise ValueError("numeric export bound/duplicate violation")
            if len(data) == expected:
                return parse_frame(bytes(data))


def require_live_numeric_frame(document, now, original_deadline):
    if document.get("State", {}).get("Running") is not True or now >= original_deadline:
        raise AssertionError("numeric frame did not arrive while original unit alive")


def require_complete_numeric_stream(reader, deadline):
    channels = reader.collect_until_eof(deadline=deadline)
    if channels != {1: b"", 2: b""}:
        raise AssertionError("duplicate or trailing numeric output bytes")


def frozen_inputs(candidate_image, normalizer_image, *, source_bytes=None):
    source = (
        Path(__file__).with_name("abi_fixture.py").read_bytes()
        if source_bytes is None
        else source_bytes
    )
    sha = hashlib.sha256(source).hexdigest()
    raw = research_dict()
    raw["runtime"]["candidate_image"] = candidate_image
    raw["runtime"]["normalizer_image"] = normalizer_image
    for lock in raw["runtime"]["dependency_locks"]:
        if lock["role"] in ("candidate", "normalizer"):
            lock["sha256"] = {
                "candidate": CANDIDATE_LOCK_SHA256,
                "normalizer": NORMALIZER_LOCK_SHA256,
            }[lock["role"]]
    config = FactorResearchConfig.model_validate(raw)
    snapshot = research_snapshot_input(
        config,
        factor_version_id=UUID(int=1),
        code_sha256=sha,
        exposure_history=ExposureHistory(),
    )
    calendar, prices = demo_inputs()
    days = {day.session_date: day for day in calendar.sessions}
    observations = tuple(
        MarketObservation(
            listing_id=row.listing_id,
            session=row.session_date,
            event_time=days[row.session_date].close_time,
            available_at=row.available_at,
            values=(row.close,),
        )
        for row in prices
    )
    day = [
        item
        for item in calendar.sessions
        if snapshot.window.start <= item.session_date < snapshot.window.end
    ][-1]
    prefix, projected = project_prefix(
        snapshot,
        host_cutoff=day.close_time,
        output_event_time=day.close_time,
        output_session=day.session_date,
        calendar_sessions=tuple(days),
        observations=observations,
        candidate_image=candidate_image,
        normalizer_image=normalizer_image,
        lag_available_at=day.close_time,
        max_output_rows=3,
        max_output_bytes=10000,
    )
    _, inputs, _ = candidate_inputs(
        prefix,
        snapshot_input=snapshot,
        source=source,
        expected_source_sha256=sha,
        projected_files=projected,
    )
    # This expected digest was independently produced by the installed fixed
    # fixture/normalizer and 3 hand-calculated momentum rows. Host never parses HDF.
    ns = (
        (day.close_time - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1)
    ) * 1000
    return prefix, dict(inputs), ns


def exercise(image, folder, *, role, inventory, input_bytes, rejected_exit_code=None):
    folder.mkdir(mode=0o755)
    inputs, hashes = {}, {}
    for destination, data in sorted(input_bytes.items()):
        target = folder / Path(destination).name
        target.write_bytes(data)
        target.chmod(0o444)
        inputs[destination] = str(target.resolve())
        hashes[destination] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        }
    commitment = hashlib.sha256(
        json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    started = time.monotonic()
    original = started + WALL_SECONDS
    limit = original + STOP_TOLERANCE_SECONDS
    deadline = str(time.time_ns() // 1000000 + int(WALL_SECONDS * 1000))
    seccomp = Path(__file__).parents[1] / "hostile_deadline" / "seccomp.default.json"
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
        f"seccomp={seccomp.resolve()}",
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
    ]
    for destination, source in inputs.items():
        args.extend(["--mount", f"type=bind,src={source},dst={destination},readonly"])
    args.extend([image, deadline])
    cid, reader, owner = "", None, None
    try:
        cid = command(*args, timeout=remaining_command_budget(limit))
        verify_inspect(
            inspect(cid, timeout=remaining_command_budget(limit)),
            image=image,
            inputs=inputs,
            role=role,
            deadline_ms=deadline,
            inventory=inventory,
            masked_paths=sorted(
                [
                    *MASKED_PATHS,
                    *(
                        str(path)
                        for path in Path("/sys/devices/system/cpu").glob(
                            "cpu[0-9]*/thermal_throttle"
                        )
                    ),
                ]
            ),
        )
        reader = AttachStream.connect(cid, deadline=original)
        marker = folder / "started"
        owner = subprocess.Popen(
            [
                "python",
                "-c",
                "import pathlib,subprocess,sys,time;subprocess.run(['docker','start',sys.argv[1]],check=True,capture_output=True);pathlib.Path(sys.argv[2]).write_text('started');time.sleep(60)",
                cid,
                str(marker),
            ]
        )
        while not marker.exists() and time.monotonic() < min(started + 3, original):
            if owner.poll() is not None:
                raise AssertionError("numeric launch owner failed")
            time.sleep(0.01)
        if not marker.exists():
            raise AssertionError("numeric launcher missed original deadline")
        document = inspect(cid, timeout=remaining_command_budget(limit))
        leader = document["State"]["Pid"]
        rows = command(
            "docker", "top", cid, "-eo", "pid", timeout=remaining_command_budget(limit)
        ).splitlines()[1:]
        observed = {
            int(row.strip()): process_identity(int(row.strip())) for row in rows
        }
        status = parse_status(Path(f"/proc/{leader}/status").read_text().splitlines())
        require_process_observation(
            observed, leader, [int(item) for item in status["NSpid"].split()]
        )
        owner.kill()
        owner.wait(timeout=1)
        kind, payload = read_numeric_frame(reader, original)
        require_live_numeric_frame(
            inspect(cid, timeout=remaining_command_budget(limit)),
            time.monotonic(),
            original,
        )
        basename = "result.h5" if role == "candidate" else "values.parquet"
        if rejected_exit_code is None:
            if kind != 2:
                raise AssertionError("maintained numeric fixture failed")
            content = validate_export(payload, expected_basename=basename)
            observed_child_exit_code = 0
        else:
            if kind != 3:
                raise AssertionError("adverse numeric case exported output")
            observed_child_exit_code = validate_rejection(
                payload,
                expected_basename=basename,
                expected_exit_code=rejected_exit_code,
            )
            content = b""
        rows = command(
            "docker",
            "top",
            cid,
            "-eo",
            "pid,uid",
            timeout=remaining_command_budget(limit),
        ).splitlines()[1:]
        if any(row.split()[1] == "65533" for row in rows):
            raise AssertionError("numeric child survived protected quiescence")
        observed.update(
            {int(row.split()[0]): process_identity(int(row.split()[0])) for row in rows}
        )
        if any(identity is None for identity in observed.values()):
            raise AssertionError("incomplete numeric process identity")
        # Normal-reading case drains the entire channel and rejects later frames.
        # The separate no-reader adversarial profile uses kernel-exit-before-close;
        # this normal case deliberately makes no such ordering claim.
        require_complete_numeric_stream(reader, limit)
        wait_kernel_exit(observed, limit)
        while True:
            stopped = inspect(cid, timeout=remaining_command_budget(limit))
            if not stopped["State"]["Running"]:
                break
            time.sleep(0.01)
        elapsed = time.monotonic() - started
        if stopped["State"]["ExitCode"] != 124 or not 0 < elapsed <= 9:
            raise AssertionError("numeric original deadline/positive stop mismatch")
        if any(process_identity(pid) == identity for pid, identity in observed.items()):
            raise AssertionError("observed numeric PID survived")
        for destination, source in inputs.items():
            current = Path(source).read_bytes()
            assert {
                "sha256": hashlib.sha256(current).hexdigest(),
                "size": len(current),
            } == hashes[destination]
        return content, {
            "role": role,
            "expected_rejection_exit_code": rejected_exit_code,
            "observed_child_exit_code": observed_child_exit_code,
            "image": image,
            "input_inventory": hashes,
            "input_inventory_sha256": commitment,
            "output_basename": basename,
            "output_sha256": hashlib.sha256(content).hexdigest(),
            "output_bytes": len(content),
            "positive_stop_verified": True,
            "original_deadline_verified": True,
            "complete_output_stream_verified": True,
            "frame_received_while_unit_alive": True,
            "launch_owner_killed": True,
            "all_observed_descendants_stopped": True,
            "protected_quiescence_frame_verified": True,
            "elapsed_seconds": elapsed,
            "exit_code": 124,
        }
    finally:
        if owner is not None and owner.poll() is None:
            owner.kill()
            owner.wait(timeout=1)
        if reader is not None:
            reader.close()
        if cid:
            subprocess.run(
                ["docker", "rm", "-f", cid],
                capture_output=True,
                timeout=10,
                check=False,
            )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run-maintained-docker-fixture", action="store_true", required=True
    )
    parser.add_argument("--candidate-image", required=True)
    parser.add_argument("--normalizer-image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    runtime_inventory = verify_runtime_inventory()
    require_local_docker()
    base = json.loads(command("docker", "image", "inspect", BASE_IMAGE))[0]
    inventories = {
        role: image_inventory(
            base, json.loads(command("docker", "image", "inspect", image))[0], role=role
        )
        for role, image in (
            ("candidate", args.candidate_image),
            ("normalizer", args.normalizer_image),
        )
    }
    prefix, inputs, _ = frozen_inputs(args.candidate_image, args.normalizer_image)
    with tempfile.TemporaryDirectory(prefix="mineagent-numeric-") as directory:
        folder = Path(directory)
        folder.chmod(0o755)
        hdf, candidate = exercise(
            args.candidate_image,
            folder / "candidate",
            role="candidate",
            inventory=inventories["candidate"],
            input_bytes=inputs,
        )
        _, normalization, _ = normalizer_inputs(
            prefix,
            hdf=hdf,
            expected_hdf_sha256=hashlib.sha256(hdf).hexdigest(),
            listings=inputs["/input/listings.json"],
        )
        parquet, normalizer = exercise(
            args.normalizer_image,
            folder / "normalizer",
            role="normalizer",
            inventory=inventories["normalizer"],
            input_bytes=dict(normalization),
        )
        assert hashlib.sha256(parquet).hexdigest() == EXPECTED_PARQUET_SHA256
        proof = {
            "scope": "disabled_maintained_numeric_fixture_only",
            "runtime_inventory": runtime_inventory,
            "image_inventories": inventories,
            "arbitrary_candidate_admission": False,
            "seccomp_sha256": SECCOMP_SHA256,
            "cases": [candidate, normalizer],
            "hand_checked_rows": 3,
            "expected_parquet_sha256": EXPECTED_PARQUET_SHA256,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
