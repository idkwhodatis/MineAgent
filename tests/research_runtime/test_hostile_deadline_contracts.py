"""Pure policy/control-flow tests. Never launch or execute hostile candidates."""

import ctypes
import json

import pytest

from runtimes.hostile_deadline import contract, deadline
from runtimes.hostile_deadline.acceptance import (
    assert_stop_timing,
    require_process_observation,
)


def document():
    return {
        "Image": "sha256:" + "a" * 64,
        "Config": {
            "User": "0:0",
            "Tty": False,
            "OpenStdin": False,
            "Entrypoint": list(contract.ENTRYPOINT),
            "Cmd": ["9000"],
            "Env": ["PATH=/fixed"],
            "WorkingDir": "/",
        },
        "HostConfig": {
            "Privileged": False,
            "LogConfig": {"Type": "none", "Config": {}},
            "ReadonlyRootfs": True,
            "NetworkMode": "none",
            "PidMode": "",
            "IpcMode": "none",
            "UTSMode": "",
            "UsernsMode": "",
            "Memory": 256 * 1024 * 1024,
            "MemorySwap": 256 * 1024 * 1024,
            "PidsLimit": 16,
            "NanoCpus": 1_000_000_000,
            "AutoRemove": False,
            "CapAdd": list(contract.BOOTSTRAP_CAPS),
            "CapDrop": ["ALL"],
            "SecurityOpt": [
                "no-new-privileges",
                "seccomp=" + json.dumps(contract.seccomp_policy()),
            ],
            "MaskedPaths": list(contract.MASKED_PATHS),
            "ReadonlyPaths": list(contract.READONLY_PATHS),
            "Tmpfs": {
                "/tmp": "rw,noexec,nosuid,nodev,size=4m,uid=65533,gid=65533,mode=0700",
                "/output": "rw,noexec,nosuid,nodev,size=16m,uid=0,gid=0,mode=0733",
                "/status": "rw,noexec,nosuid,nodev,size=64k,uid=0,gid=0,mode=0700",
            },
        },
        "Mounts": [
            {
                "Type": "bind",
                "Source": "/fixed/candidate.py",
                "Destination": "/input/candidate.py",
                "RW": False,
            }
        ],
    }


def check(value):
    contract.verify_inspect(
        value,
        image="sha256:" + "a" * 64,
        candidate_path="/fixed/candidate.py",
        deadline_ms="9000",
        inventory={
            "base_ref": contract.BASE_IMAGE,
            "image_id": "sha256:" + "a" * 64,
            "environment": ["PATH=/fixed"],
            "working_directory": "/",
        },
        masked_paths=list(contract.MASKED_PATHS),
    )


def test_disabled_even_with_valid_projection():
    check(document())
    with pytest.raises(RuntimeError, match="not completed"):
        contract.require_launch_authority()


def test_docker_canonical_capability_prefix_preserves_exact_authority():
    data = document()
    data["HostConfig"]["CapAdd"] = ["CAP_SETUID", "CAP_KILL", "CAP_SETGID"]
    check(data)


@pytest.mark.parametrize(
    "caps",
    [
        ["CAP_SETUID", "CAP_KILL", "CAP_SYS_ADMIN"],
        ["CAP_SETUID", "CAP_KILL", "CAP_SETGID", "CAP_NET_RAW"],
        ["CAP_SETUID", "CAP_KILL"],
        ["CAP_SETUID", "SETUID", "CAP_KILL"],
        ["CAP_CAP_SETUID", "CAP_KILL", "CAP_SETGID"],
        ["cap_setuid", "CAP_KILL", "CAP_SETGID"],
        ["ALL", "CAP_KILL", "CAP_SETGID"],
        [True, "CAP_KILL", "CAP_SETGID"],
        "CAP_SETUID,CAP_KILL,CAP_SETGID",
        None,
    ],
)
def test_capability_aliases_never_hide_extra_or_missing_authority(caps):
    data = document()
    data["HostConfig"]["CapAdd"] = caps
    with pytest.raises(ValueError, match="capability policy drift"):
        check(data)


@pytest.mark.parametrize("value", ["", "-1", "1.5", "١٢٣", "1" * 17, "1000", "11001"])
def test_deadline_rejects_ambiguous_expired_or_excessive(value):
    with pytest.raises(ValueError):
        contract.remaining_seconds(value, 1000)


def test_deadline_never_extends_or_silently_clamps():
    assert contract.remaining_seconds("11000", 1000) == 10
    assert contract.remaining_seconds("1001", 1000) == 0.001


@pytest.mark.parametrize(
    "key,value",
    [
        ("Privileged", True),
        ("ReadonlyRootfs", False),
        ("NetworkMode", "host"),
        ("PidMode", "host"),
        ("IpcMode", "host"),
        ("UsernsMode", "host"),
        ("UTSMode", "host"),
        ("PidsLimit", 0),
        ("Memory", 0),
        ("MemorySwap", -1),
        ("NanoCpus", 0),
        ("AutoRemove", True),
        ("Init", True),
        ("CapAdd", ["SYS_ADMIN"]),
        ("CapDrop", []),
        ("SecurityOpt", []),
        ("Devices", [{}]),
        ("DeviceRequests", [{}]),
        ("Binds", ["/var/run/docker.sock"]),
        ("VolumesFrom", ["peer"]),
        ("Links", ["peer"]),
    ],
)
def test_inspect_rejects_scope_drift(key, value):
    data = document()
    data["HostConfig"][key] = value
    with pytest.raises(ValueError):
        check(data)


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("Config", "User", "65533:65533"),
        ("Config", "Entrypoint", ["python", "/input/candidate.py"]),
        ("Config", "Cmd", ["10000"]),
    ],
)
def test_invocation_drift(section, key, value):
    data = document()
    data[section][key] = value
    with pytest.raises(ValueError):
        check(data)


def test_candidate_mount_and_status_ownership_drift():
    data = document()
    data["Mounts"][0]["RW"] = True
    with pytest.raises(ValueError):
        check(data)
    data = document()
    data["HostConfig"]["Tmpfs"]["/status"] += ",mode=0777"
    with pytest.raises(ValueError):
        check(data)


def test_capset_retains_only_requested_kill_bit():
    seen = []

    class Libc:
        def capset(self, header, data):
            values = ctypes.cast(data, ctypes.POINTER(deadline.CapData))
            seen.append(
                [
                    (row.effective, row.permitted, row.inheritable)
                    for row in (values[0], values[1])
                ]
            )
            return 0

    deadline.drop_capabilities(Libc(), deadline.KILL_MASK)
    deadline.drop_capabilities(Libc())
    assert seen == [[(32, 32, 0), (0, 0, 0)], [(0, 0, 0), (0, 0, 0)]]


def test_quiescence_marker_only_after_echild_and_deadline_unchanged(monkeypatch):
    events = []
    ticks = iter([1, 1, 1, 2, 11])
    children = iter([(0, 0), (2, 9), ChildProcessError()])

    def wait(*args):
        item = next(children)
        events.append("wait")
        if isinstance(item, Exception):
            raise item
        return item

    def leave(code):
        raise SystemExit(code)

    monkeypatch.setattr(deadline.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(deadline.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        deadline.os, "kill", lambda pid, sig: events.append(("kill", pid, sig))
    )
    monkeypatch.setattr(deadline.os, "waitpid", wait)
    monkeypatch.setattr(
        deadline.os, "fork", lambda: events.append("fork-exporter") or 99
    )
    monkeypatch.setattr(
        deadline.os, "write", lambda *_: pytest.fail("PID1 must never write")
    )
    monkeypatch.setattr(deadline.os, "_exit", leave)
    with pytest.raises(SystemExit) as error:
        deadline.quiesce_and_hold(10, 0)
    assert error.value.code == 124
    assert events[: events.index("fork-exporter")].count("wait") == 3
    assert events.count("fork-exporter") == 1


def test_quiescence_expiration_never_publishes(monkeypatch):
    monkeypatch.setattr(deadline.time, "monotonic", lambda: 10)
    monkeypatch.setattr(
        deadline.os, "_exit", lambda code: (_ for _ in ()).throw(SystemExit(code))
    )
    monkeypatch.setattr(
        deadline.os, "fork", lambda *_: pytest.fail("expired publication")
    )
    with pytest.raises(SystemExit) as error:
        deadline.quiesce_and_hold(10, 0)
    assert error.value.code == 124


@pytest.mark.parametrize(
    "field,value",
    [
        ("CapEff", "20"),
        ("CapPrm", "20"),
        ("CapInh", "1"),
        ("CapAmb", "1"),
        ("CapBnd", "ffffffff"),
        ("NoNewPrivs", "0"),
    ],
)
def test_privilege_observation_rejects_drift(monkeypatch, field, value):
    observed = {
        "CapEff": "0",
        "CapPrm": "0",
        "CapInh": "0",
        "CapAmb": "0",
        "CapBnd": format(deadline.BOOTSTRAP_MASK, "x"),
        "NoNewPrivs": "1",
        "Seccomp": "2",
    }
    observed[field] = value
    monkeypatch.setattr(deadline, "status", lambda: observed)
    with pytest.raises(RuntimeError):
        deadline.require_caps(0)


@pytest.mark.parametrize("elapsed", [9.0001, 10, 15, -1])
def test_stop_timing_rejects_new_relative_wait_or_extended_deadline(elapsed):
    with pytest.raises(AssertionError, match="original absolute"):
        assert_stop_timing(100, 100 + elapsed)


def test_stop_timing_uses_inclusive_explicit_one_second_tolerance():
    assert assert_stop_timing(100, 108.25) == 8.25
    assert assert_stop_timing(100, 109) == 9


@pytest.mark.parametrize(
    "field,value",
    [
        ("Env", ["PYTHONPATH=/input"]),
        ("WorkingDir", "/tmp"),
        ("Healthcheck", {"Test": ["CMD", "evil"]}),
        ("Volumes", {"/host": {}}),
    ],
)
def test_effective_image_configuration_drift(field, value):
    data = document()
    data["Config"][field] = value
    with pytest.raises(ValueError):
        check(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("MaskedPaths", []),
        ("ReadonlyPaths", []),
        ("SecurityOpt", ["no-new-privileges", "seccomp=unconfined"]),
    ],
)
def test_effective_host_policy_drift(field, value):
    data = document()
    data["HostConfig"][field] = value
    with pytest.raises(ValueError):
        check(data)


def test_image_inventory_requires_immutable_base_and_exact_inheritance():
    base = {
        "Id": "sha256:base",
        "RepoDigests": ["python@" + contract.BASE_IMAGE.split("@")[1]],
        "Config": {"Env": ["PATH=/fixed", "LANG=C.UTF-8"]},
    }
    image = {
        "Id": "sha256:image",
        "Config": {
            "Env": list(base["Config"]["Env"]),
            "WorkingDir": "/",
            "User": "0:0",
            "Entrypoint": list(contract.ENTRYPOINT),
        },
    }
    inventory = contract.image_inventory(base, image)
    assert inventory["environment"] == base["Config"]["Env"]
    image["Config"]["Env"].append("LD_PRELOAD=/input/evil")
    with pytest.raises(ValueError):
        contract.image_inventory(base, image)
    base["RepoDigests"] = []
    with pytest.raises(ValueError):
        contract.image_inventory(base, image)


@pytest.mark.parametrize(
    "observed,init_pid,namespace",
    [
        ({500: None}, 500, [500, 1]),
        ({501: "1"}, 500, [500, 1]),
        ({500: "1"}, 500, [500]),
        ({500: "1"}, 500, [500, 2]),
        ({500: "1"}, 500, [499, 1]),
    ],
)
def test_missing_remote_or_mismatched_pid_observation_fails(
    observed, init_pid, namespace
):
    with pytest.raises(AssertionError):
        require_process_observation(observed, init_pid, namespace)


def test_complete_local_namespace_pid_observation():
    require_process_observation({500: "100", 501: "101"}, 500, [500, 1])


@pytest.mark.parametrize("failure", [None, "set_blocking", "write"])
def test_bootstrap_diagnostic_is_nonblocking_bounded_and_always_exits(
    monkeypatch, failure
):
    calls = []

    class Exited(BaseException):
        pass

    def set_blocking(fd, blocking):
        calls.append(("blocking", fd, blocking))
        if failure == "set_blocking":
            raise OSError(9, "private diagnostic error")

    def write(fd, payload):
        calls.append(("write", fd, payload))
        assert len(payload) <= 256
        assert b"private" not in payload
        if failure == "write":
            raise BlockingIOError(11, "private diagnostic error")
        return len(payload)

    def exit_(code):
        calls.append(("exit", code))
        raise Exited

    monkeypatch.setattr(deadline.os, "set_blocking", set_blocking)
    monkeypatch.setattr(deadline.os, "write", write)
    monkeypatch.setattr(deadline.os, "_exit", exit_)
    with pytest.raises(Exited):
        deadline.fail_closed(RuntimeError("private candidate text"))
    assert calls[0] == ("blocking", 2, False)
    assert calls[-1] == ("exit", 125)
    assert sum(call[0] == "write" for call in calls) <= 1


@pytest.mark.parametrize(
    "location,number",
    [
        ("unknown", None),
        ("main:1", 1),
        ("quiesce_and_hold:999999", 4095),
        ("candidate:100", None),
    ],
)
def test_closed_bootstrap_diagnostic(location, number):
    from runtimes.hostile_deadline.acceptance import parse_bootstrap_diagnostic

    item = {
        "schema": "mineagent.trusted-bootstrap-failure.v1",
        "location": location,
        "errno": number,
    }
    assert parse_bootstrap_diagnostic(json.dumps(item).encode()) == item


@pytest.mark.parametrize(
    "key,value",
    [
        ("location", "secret-password:1"),
        ("location", "main:0"),
        ("location", "main:1000000"),
        ("location", "main:01"),
        ("location", "main:1\nprivate"),
        ("location", {"secret": "private"}),
        ("location", ["main:1"]),
        ("location", None),
        ("errno", True),
        ("errno", 0),
        ("errno", -1),
        ("errno", 4096),
        ("errno", "private"),
        ("errno", {}),
        ("schema", []),
    ],
)
def test_malformed_bootstrap_diagnostic_rejected(key, value):
    from runtimes.hostile_deadline.acceptance import parse_bootstrap_diagnostic

    item = {
        "schema": "mineagent.trusted-bootstrap-failure.v1",
        "location": "main:1",
        "errno": None,
    }
    item[key] = value
    assert parse_bootstrap_diagnostic(json.dumps(item).encode()) is None


@pytest.mark.parametrize(
    "raw", [b"", b"null", b"[]", b"private", b"\xff", b" " * 257, "wrong-type"]
)
def test_bootstrap_diagnostic_rejects_nonrecord_and_oversized(raw):
    from runtimes.hostile_deadline.acceptance import parse_bootstrap_diagnostic

    assert parse_bootstrap_diagnostic(raw) is None


def test_bootstrap_diagnostic_construction_failure_still_exits(monkeypatch):
    class Exited(BaseException):
        pass

    def broken(*args, **kwargs):
        raise RuntimeError("private")

    def exit_(code):
        assert code == 125
        raise Exited

    monkeypatch.setattr(deadline.json, "dumps", broken)
    monkeypatch.setattr(deadline.os, "_exit", exit_)
    with pytest.raises(Exited):
        deadline.fail_closed(RuntimeError("original private"))


def test_proc_status_empty_groups_preserves_field_separator():
    parsed = deadline.parse_status(
        ["Name:\tpython\n", "Groups:\t\n", "CapEff:\t0000000000000000\n"]
    )
    assert parsed == {"Name": "python", "Groups": "", "CapEff": "0000000000000000"}


def test_actual_local_proc_status_is_harmless_readable_metadata():
    observed = deadline.status()
    assert int(observed["Pid"]) > 0
    assert int(observed["CapEff"], 16) >= 0
    assert "Groups" in observed


def test_attack_fixture_status_parser_handles_empty_groups_without_running_attack():
    import ast

    from runtimes.hostile_deadline.acceptance import ATTACK

    # Execute only the isolated status expression with a harmless proc fixture.
    assignment = next(
        node
        for node in ast.parse(ATTACK).body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "status"
    )
    expression = ast.Expression(assignment.value)
    parsed = eval(
        compile(expression, "<fixed-status-expression>", "eval"),
        {"open": lambda _: ["Groups:\t\n", "CapEff:\t0000\n"]},
    )
    assert parsed == {"Groups": "", "CapEff": "0000"}
