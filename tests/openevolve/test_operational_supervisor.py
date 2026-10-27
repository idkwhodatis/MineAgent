"""Closed-launch contract/transport doubles; no local Docker/listener execution."""

import json
from pathlib import Path

import pytest

from packages.research_runtime.process_contracts import ProcessError
from runtimes.openevolve_operational import supervisor as module
from runtimes.openevolve_operational.trusted_report import report_bytes
from tests.openevolve.operational_fixtures import records


class Reader:
    def __init__(self, frames):
        self.frames = iter(frames)
        self.closed = False

    def read_frame(self, *, deadline):
        return next(self.frames, None)

    def close(self):
        self.closed = True


def test_report_stream_accepts_fragmented_exact_two_reports():
    first = report_bytes("started", 2, 123)
    last = report_bytes("quiescent", 2, 123, 0)
    reader = Reader([(1, first[:5]), (1, first[5:] + last[:8]), (1, last[8:])])
    stream = module.ReportStream(reader)
    assert stream.next(10)["event"] == "started"
    assert len(stream.finish(10)) == 2


@pytest.mark.parametrize(
    "frames",
    [
        [(2, b"error")],
        [(1, b"x" * 2049)],
        [(1, b"{")],
        [(1, report_bytes("quiescent", 2, 123, 0))],
        [(1, report_bytes("started", 2, 123) * 2)],
        [(1, report_bytes("started", 2, 123) + report_bytes("quiescent", 3, 123, 0))],
        [
            (
                1,
                report_bytes("started", 2, 123)
                + report_bytes("quiescent", 2, 123, 0)
                + b"\n",
            )
        ],
        [(1, report_bytes("started", 2, 123) + b' {"schema":null}\n')],
    ],
)
def test_report_stream_rejects_wrong_stream_duplicate_trailing_or_changed_child(frames):
    with pytest.raises((ValueError, KeyError)):
        module.ReportStream(Reader(frames)).finish(10)


def test_authority_is_private_single_use_and_readonly(tmp_path):
    intent = records(tmp_path)[0].intent
    with pytest.raises(ProcessError):
        module.SingleUseControllerAuthority(intent)
    authority = module._issue_committed_authority(intent)
    with pytest.raises(AttributeError):
        authority.intent = intent
    authority.consume()
    with pytest.raises(ProcessError):
        authority.consume()


@pytest.fixture
def contract(tmp_path):
    launch = records(tmp_path)[0]
    a = launch.intent.admission
    operation = tmp_path / "operation"
    inventory = {
        "base_ref": module.BASE_IMAGE,
        "image_id": a.image_id,
        "environment": ["PATH=/usr/bin"],
        "working_directory": "/tmp",
    }
    host = {
        "Privileged": False,
        "ReadonlyRootfs": True,
        "NetworkMode": "none",
        "PidMode": "",
        "IpcMode": "none",
        "UTSMode": "",
        "UsernsMode": "",
        "Memory": 2 * 1024**3,
        "MemorySwap": 2 * 1024**3,
        "PidsLimit": 64,
        "NanoCpus": 1_000_000_000,
        "AutoRemove": False,
        "ShmSize": 65536,
        "CapAdd": ["CAP_KILL", "CAP_SETUID", "CAP_SETGID"],
        "CapDrop": ["ALL"],
        "SecurityOpt": [
            "no-new-privileges",
            "seccomp=" + json.dumps(module.seccomp_policy()),
        ],
        "MaskedPaths": [
            *module.MASKED_PATHS,
            *(
                str(p)
                for p in Path("/sys/devices/system/cpu").glob(
                    "cpu[0-9]*/thermal_throttle"
                )
            ),
        ],
        "ReadonlyPaths": list(module.READONLY_PATHS),
        "Tmpfs": module.TMPFS.copy(),
        "LogConfig": {"Type": "none", "Config": {}},
        "Ulimits": [
            {"Name": "nofile", "Hard": 256, "Soft": 256},
            {"Name": "fsize", "Hard": 8388608, "Soft": 8388608},
        ],
    }
    document = {
        "Id": "d" * 64,
        "Image": a.image_id,
        "Name": "/" + launch.runtime_name,
        "HostConfig": host,
        "Config": {
            "Env": inventory["environment"],
            "WorkingDir": "/tmp",
            "User": "0:0",
            "Entrypoint": list(module.ENTRYPOINT),
            "Cmd": [str(int(launch.intent.not_after.timestamp() * 1000))],
            "Labels": module._labels(launch),
            "Tty": False,
            "OpenStdin": False,
        },
        "Mounts": [
            {
                "Source": str(operation / "input"),
                "Destination": "/bootstrap",
                "Type": "bind",
                "RW": False,
                "Propagation": "rprivate",
            },
            {
                "Source": str(operation / "ipc"),
                "Destination": "/ipc",
                "Type": "bind",
                "RW": False,
                "Propagation": "rprivate",
            },
        ],
    }
    return launch, operation, inventory, document


def test_exact_controller_container_contract(contract):
    launch, operation, inventory, document = contract
    module.verify_container(
        document, launch, operation, inventory, container_id="d" * 64
    )


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("HostConfig", "Privileged", True),
        ("HostConfig", "ReadonlyRootfs", False),
        ("HostConfig", "NetworkMode", "host"),
        ("HostConfig", "PidMode", "host"),
        ("HostConfig", "IpcMode", "host"),
        ("HostConfig", "Memory", 0),
        ("HostConfig", "MemorySwap", -1),
        ("HostConfig", "PidsLimit", 0),
        ("HostConfig", "ShmSize", 1048576),
        ("HostConfig", "CapAdd", ["SYS_ADMIN"]),
        ("HostConfig", "CapDrop", []),
        ("HostConfig", "Tmpfs", {}),
        ("HostConfig", "LogConfig", {"Type": "json-file", "Config": {}}),
        ("HostConfig", "Init", True),
        ("HostConfig", "Devices", [{}]),
        ("HostConfig", "DeviceRequests", [{}]),
        ("HostConfig", "Binds", ["/etc:/etc"]),
        ("HostConfig", "VolumesFrom", ["other"]),
        ("HostConfig", "Links", ["other"]),
        ("HostConfig", "Ulimits", []),
        ("HostConfig", "SecurityOpt", ["seccomp=unconfined"]),
        ("HostConfig", "MaskedPaths", []),
        ("Config", "Env", ["PATH=/evil"]),
        ("Config", "WorkingDir", "/"),
        ("Config", "User", "65533:65533"),
        ("Config", "Entrypoint", ["sh"]),
        ("Config", "Cmd", ["999"]),
        ("Config", "Labels", {}),
        ("Config", "Tty", True),
        ("Config", "OpenStdin", True),
        ("Config", "Volumes", {"/evil": {}}),
        ("Config", "Healthcheck", {"Test": ["CMD", "sh"]}),
    ],
)
def test_resource_mount_entrypoint_negative_matrix(contract, section, key, value):
    launch, operation, inventory, document = contract
    document[section][key] = value
    with pytest.raises(ValueError):
        module.verify_container(document, launch, operation, inventory)


@pytest.mark.parametrize(
    "change", ["writable", "extra", "ipcsource", "image", "container"]
)
def test_exact_mount_and_runtime_binding(contract, change):
    launch, operation, inventory, document = contract
    if change == "writable":
        document["Mounts"][1]["RW"] = True
    elif change == "extra":
        document["Mounts"].append(document["Mounts"][0].copy())
    elif change == "ipcsource":
        document["Mounts"][1]["Source"] = "/tmp/public"
    elif change == "image":
        document["Image"] = "sha256:" + "f" * 64
    else:
        document["Id"] = "f" * 64
    with pytest.raises(ValueError):
        module.verify_container(
            document, launch, operation, inventory, container_id="d" * 64
        )
