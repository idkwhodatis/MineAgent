"""Isolated inert-fixture differential proof; never a production transport switch."""

import json
import math
import os
import re
import stat
import sys
import time
from pathlib import Path
from unittest.mock import patch

from packages.research_runtime.process_contracts import ProcessError
from runtimes.openevolve_operational import daemon_identity as observer
from runtimes.openevolve_operational.docker_read_transport import DockerReadTransport
from runtimes.research_supervisor.supervisor import DockerSupervisor

IMAGE = "python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e"
LABEL = "io.mineagent.oe-transport-proof"
SLEEPER = "import subprocess,sys,time; children=[subprocess.Popen([sys.executable,'-I','-S','-c','import time; time.sleep(180)']) for _ in range(2)]; time.sleep(180)"
CODES = {
    "oe_controller_daemon_identity",
    "research_process_uncertain",
    "research_process_output",
    "research_process_identity",
}


def fail():
    raise ProcessError("oe_controller_daemon_identity")


def owner():
    run, attempt = (
        os.environ.get("GITHUB_RUN_ID", ""),
        os.environ.get("GITHUB_RUN_ATTEMPT", ""),
    )
    if (
        re.fullmatch(r"[0-9]{1,20}", run) is None
        or re.fullmatch(r"[0-9]{1,6}", attempt) is None
    ):
        fail()
    return run + "-" + attempt


def names(token):
    return [
        "mineagent-oe-transport-" + token + "-" + mode
        for mode in ("normal", "oversize")
    ]


def image_id(cli):
    _, raw = cli._command(["image", "inspect", "--format", "{{.Id}}", IMAGE])
    value = raw.decode("ascii").strip()
    if re.fullmatch(r"sha256:[a-f0-9]{64}", value) is None:
        fail()
    return value


def owned(cli, name, image, token):
    # Format projection stays bounded even for the deliberate oversized label.
    code, raw = cli._command(
        [
            "container",
            "inspect",
            "--format",
            '{{.Id}}|{{.Image}}|{{index .Config.Labels "' + LABEL + '"}}',
            name,
        ],
        check=False,
    )
    if code == 1 and (b"No such container:" in raw or b"No such object:" in raw):
        return None
    if code:
        fail()
    fields = raw.decode("ascii").strip().split("|")
    if (
        len(fields) != 3
        or re.fullmatch(r"[a-f0-9]{64}", fields[0]) is None
        or fields[1:] != [image, token]
    ):
        fail()
    return fields[0]


def claim_path(name, token):
    expected = names(token)
    if name not in expected:
        fail()
    directory = Path("var/numeric-private/oe-transport-claims")
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = directory.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        fail()
    return directory / ("normal" if name == expected[0] else "oversize")


def claim(name, token):
    path = claim_path(name, token)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        if os.write(fd, b"owned-inert-fixture-v1\n") != len(
            b"owned-inert-fixture-v1\n"
        ):
            fail()
        os.fsync(fd)
    finally:
        os.close(fd)
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def claimed(name, token):
    path = claim_path(name, token)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return False
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & 0o077
            or os.read(fd, 64) != b"owned-inert-fixture-v1\n"
        ):
            fail()
        return True
    finally:
        os.close(fd)


def cleanup(cli, name, image, token):
    if not claimed(name, token):
        return
    cid = owned(cli, name, image, token)
    if cid is not None:
        cli._command(["rm", "--force", cid])
        if owned(cli, cid, image, token) is not None:
            fail()
    if owned(cli, name, image, token) is not None:
        fail()
    claim_path(name, token).unlink()


def launch(cli, name, image, token, oversized=False):
    if owned(cli, name, image, token) is not None:
        fail()
    claim(name, token)
    args = [
        "run",
        "--detach",
        "--pull",
        "never",
        "--name",
        name,
        "--label",
        LABEL + "=" + token,
        "--network",
        "none",
        "--ipc",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        "65533:65533",
        "--pids-limit",
        "8",
        "--memory",
        "64m",
        "--memory-swap",
        "64m",
        "--cpus",
        "1",
    ]
    if oversized:
        args += ["--label", "io.mineagent.proof-padding=" + "x" * 65536]
    args += ["--entrypoint", "python", image, "-I", "-S", "-c", SLEEPER]
    _, raw = cli._command(args)
    cid = raw.decode("ascii").strip()
    if (
        re.fullmatch(r"[a-f0-9]{64}", cid) is None
        or owned(cli, name, image, token) != cid
    ):
        fail()
    return cid


def context(cli, cid, image):
    end = time.monotonic() + 8
    while time.monotonic() < end:
        doc = cli._inspect(cid)
        if type(doc) is not dict or doc.get("Image") != image or doc.get("Id") != cid:
            fail()
        leader = doc["State"]["Pid"]
        _, raw = cli._command(["top", cid, "-eo", observer.TOP_FIELDS])
        rows = observer.parse_top(raw)
        children = sorted(
            pid for pid, row in rows.items() if row[:2] == (65533, leader)
        )
        if len(children) == 2:
            return leader, children[0], children[1]
        time.sleep(0.02)
    fail()


def sequence(mode, cid, image, pids):
    end = time.monotonic() + 8
    counts = {"inspect": 0, "top": 0, "host": 0}
    original_host = observer._host_snapshot
    client = (
        DockerSupervisor(image)
        if mode == "cli"
        else DockerReadTransport(image, deadline=end)
    )

    class Counted:
        def _inspect(self, value):
            counts["inspect"] += 1
            return client._inspect(value)

        def _command(self, args):
            counts["top"] += 1
            return client._command(args)

    def host(pid):
        counts["host"] += 1
        return original_host(pid)

    started = time.monotonic()
    error = None
    result = None
    try:
        with (
            patch.object(observer, "DockerSupervisor", lambda _: Counted()),
            patch.object(observer, "_host_snapshot", host),
        ):
            unit = observer.DaemonIdentityContext(cid, image, pids[0], end)
            result = (
                unit.observe_pair(pids[1]),
                unit.observe(pids[2]),
                unit.observe_pair(pids[1]),
            )
    except ProcessError as exc:
        if exc.code not in CODES:
            fail()
        error = exc.code
    elapsed = time.monotonic() - started
    if not math.isfinite(elapsed) or not 0 <= elapsed <= 30:
        fail()
    return result, {"counts": counts, "seconds": round(elapsed, 6), "error": error}


def empty_report():
    return {
        "schema": 1,
        "scope": "inert_same_order_transport",
        "production_acceptance": False,
        "complete": False,
        "cleanup": False,
        "api_compatible": False,
        "authority_equal": False,
        "strict_bounds_disclosed": True,
        "peer_authentication_exercised": False,
        "samples": {"cli": [], "api": []},
        "negatives": {
            name: {mode: None for mode in ("cli", "api")}
            for name in ("paused", "stopped", "missing", "oversize")
        },
    }


def run():
    report = empty_report()
    token = owner()
    cli = DockerSupervisor("sha256:" + "0" * 64)
    image = image_id(cli)
    normal, oversize = names(token)
    # Refuse collisions before taking responsibility for any cleanup.
    if any(owned(cli, name, image, token) is not None for name in (normal, oversize)):
        return report
    try:
        cid = launch(cli, normal, image, token)
        pids = context(cli, cid, image)
        baseline = None
        equal = True
        for _ in range(3):
            for mode in ("cli", "api"):
                identities, sample = sequence(mode, cid, image, pids)
                report["samples"][mode].append(sample)
                if sample["error"] is not None or sample["counts"] != {
                    "inspect": 6,
                    "top": 3,
                    "host": 12,
                }:
                    fail()
                if baseline is None:
                    baseline = identities
                equal &= identities == baseline
        report["authority_equal"] = bool(equal)
        report["api_compatible"] = True
        for state in ("paused", "stopped", "missing"):
            if state == "paused":
                cli._command(["pause", cid])
            elif state == "stopped":
                cli._command(["unpause", cid])
                cli._command(["stop", "--time", "1", cid])
            else:
                cleanup(cli, normal, image, token)
            for mode in ("cli", "api"):
                _, sample = sequence(mode, cid, image, pids)
                report["negatives"][state][mode] = sample["error"] is not None
        cid = launch(cli, oversize, image, token, oversized=True)
        for mode in ("cli", "api"):
            client = (
                cli
                if mode == "cli"
                else DockerReadTransport(image, deadline=time.monotonic() + 8)
            )
            try:
                client._inspect(cid)
                rejected = False
            except ProcessError as exc:
                rejected = exc.code == (
                    "research_process_output"
                    if mode == "cli"
                    else "oe_controller_daemon_identity"
                )
            report["negatives"]["oversize"][mode] = rejected
        report["complete"] = equal and all(
            all(value is True for value in case.values())
            for case in report["negatives"].values()
        )
    except Exception:  # noqa: BLE001 - closed result only, never raw daemon/errors
        report["complete"] = False
    finally:
        clean = True
        for name in (normal, oversize):
            try:
                cleanup(cli, name, image, token)
            except Exception:  # noqa: BLE001
                clean = False
        report["cleanup"] = clean
        report["complete"] &= clean
    return report


def validate_report(value):
    template = empty_report()
    if type(value) is not dict or set(value) != set(template):
        fail()
    for key in (
        "schema",
        "scope",
        "production_acceptance",
        "strict_bounds_disclosed",
        "peer_authentication_exercised",
    ):
        if type(value[key]) is not type(template[key]) or value[key] != template[key]:
            fail()
    for key in ("complete", "cleanup", "api_compatible", "authority_equal"):
        if type(value[key]) is not bool:
            fail()
    if type(value["samples"]) is not dict or set(value["samples"]) != {"cli", "api"}:
        fail()
    all_passed = True
    for mode in ("cli", "api"):
        samples = value["samples"][mode]
        if type(samples) is not list or len(samples) > 3:
            fail()
        all_passed &= len(samples) == 3
        for sample in samples:
            if type(sample) is not dict or set(sample) != {
                "counts",
                "seconds",
                "error",
            }:
                fail()
            if (
                type(sample["seconds"]) not in (int, float)
                or not math.isfinite(sample["seconds"])
                or not 0 <= sample["seconds"] <= 30
            ):
                fail()
            if sample["error"] is not None and (
                type(sample["error"]) is not str or sample["error"] not in CODES
            ):
                fail()
            counts = sample["counts"]
            if type(counts) is not dict or set(counts) != {"inspect", "top", "host"}:
                fail()
            for key, maximum in (("inspect", 6), ("top", 3), ("host", 12)):
                if type(counts[key]) is not int or not 0 <= counts[key] <= maximum:
                    fail()
            all_passed &= sample["error"] is None and counts == {
                "inspect": 6,
                "top": 3,
                "host": 12,
            }
    negatives = value["negatives"]
    if type(negatives) is not dict or set(negatives) != {
        "paused",
        "stopped",
        "missing",
        "oversize",
    }:
        fail()
    for case in negatives.values():
        if type(case) is not dict or set(case) != {"cli", "api"}:
            fail()
        if any(item is not None and type(item) is not bool for item in case.values()):
            fail()
        all_passed &= all(item is True for item in case.values())
    if value["complete"] and not (
        all_passed
        and value["cleanup"]
        and value["api_compatible"]
        and value["authority_equal"]
    ):
        fail()
    return value


def publish(path):
    with open(path, "rb") as stream:
        raw = stream.read(32769)
    prefix = b"OE_DOCKER_TRANSPORT_PROOF_V1:"
    if (
        len(raw) > 32768
        or not raw.startswith(prefix)
        or raw.count(b"\n") != 1
        or not raw.endswith(b"\n")
    ):
        fail()

    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                fail()
            result[key] = value
        return result

    value = json.loads(raw[len(prefix) :], object_pairs_hook=unique)
    validate_report(value)
    print(json.dumps(value, sort_keys=True, allow_nan=False))
    return 0 if value["complete"] else 1


def main():
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "--publish":
            return publish(sys.argv[2])
        if sys.argv[1:] == ["--cleanup"]:
            token = owner()
            cli = DockerSupervisor("sha256:" + "0" * 64)
            image = image_id(cli)
            clean = True
            for name in names(token):
                try:
                    cleanup(cli, name, image, token)
                except Exception:  # noqa: BLE001
                    clean = False
            if not clean:
                fail()
            print('{"cleanup":true}')
            return 0
        if sys.argv[1:]:
            fail()
        report = validate_report(run())
    except Exception:  # noqa: BLE001
        report = empty_report()
    print(
        (
            ""
            if len(sys.argv) > 1 and sys.argv[1] == "--publish"
            else "OE_DOCKER_TRANSPORT_PROOF_V1:"
        )
        + json.dumps(report, sort_keys=True, allow_nan=False)
    )
    return 0 if report["complete"] and report["cleanup"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
