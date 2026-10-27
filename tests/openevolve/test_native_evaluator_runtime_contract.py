"""Disabled numeric role contracts using inspect dictionaries; never launch Docker."""

import copy
import json

import pytest

from runtimes.openevolve_evaluator import runtime_contract as contract

ROLES = ("native",)
IMAGE = "sha256:" + "a" * 64


def inputs(role):
    return {
        name: "/frozen" + name
        for name in (
            "/input/strategy.py",
            "/input/request.json",
            "/input/stage-report.json",
            "/input/data/INDEX.csv",
        )
    }


def document(role):
    return {
        "Image": IMAGE,
        "Config": {
            "User": "0:0",
            "Tty": False,
            "OpenStdin": False,
            "Entrypoint": list(contract.ENTRYPOINTS[role]),
            "Cmd": ["9000"],
            "Env": ["PATH=/fixed"],
            "WorkingDir": "/",
        },
        "HostConfig": {
            "Privileged": False,
            "ReadonlyRootfs": True,
            "NetworkMode": "none",
            "PidMode": "",
            "IpcMode": "none",
            "UTSMode": "",
            "UsernsMode": "",
            "Memory": 1024 * 1024 * 1024,
            "MemorySwap": 1024 * 1024 * 1024,
            "PidsLimit": 64,
            "NanoCpus": 1_000_000_000,
            "AutoRemove": False,
            "CapAdd": list(contract.BOOTSTRAP_CAPS),
            "CapDrop": ["ALL"],
            "LogConfig": {"Type": "none", "Config": {}},
            "SecurityOpt": [
                "no-new-privileges",
                "seccomp=" + json.dumps(contract.seccomp_policy()),
            ],
            "MaskedPaths": list(contract.MASKED_PATHS),
            "ReadonlyPaths": list(contract.READONLY_PATHS),
            "Tmpfs": {
                "/tmp": "rw,noexec,nosuid,nodev,size=16m,uid=65533,gid=65533,mode=0700",
                "/output": "rw,noexec,nosuid,nodev,size=8m,uid=0,gid=0,mode=0733",
                "/status": "rw,noexec,nosuid,nodev,size=20m,uid=0,gid=0,mode=0700",
            },
        },
        "Mounts": [
            {"Type": "bind", "Source": source, "Destination": destination, "RW": False}
            for destination, source in inputs(role).items()
        ],
    }


def inventory():
    return {
        "base_ref": contract.BASE_IMAGE,
        "image_id": IMAGE,
        "environment": ["PATH=/fixed"],
        "working_directory": "/",
    }


def check(value, role, *, expected_inputs=None, expected_inventory=None):
    contract.verify_inspect(
        value,
        image=IMAGE,
        inputs=inputs(role) if expected_inputs is None else expected_inputs,
        role=role,
        deadline_ms="9000",
        inventory=inventory() if expected_inventory is None else expected_inventory,
        masked_paths=list(contract.MASKED_PATHS),
    )


def test_exact_native_projection_is_still_closed():
    check(document("native"), "native")
    with pytest.raises(RuntimeError):
        contract.require_launch_authority()


@pytest.mark.parametrize(
    "field,value",
    [
        ("IpcMode", "private"),
        ("Memory", 512 * 1024**2),
        ("PidsLimit", 65),
        ("NetworkMode", "bridge"),
        ("ReadonlyRootfs", False),
        ("CapAdd", ["ALL"]),
    ],
)
def test_native_policy_drift_rejected(field, value):
    state = document("native")
    state["HostConfig"][field] = value
    with pytest.raises(ValueError):
        check(state, "native")


def test_no_undeclared_or_writable_mounts():
    state = document("native")
    state["Mounts"][0]["RW"] = True
    with pytest.raises(ValueError):
        check(state, "native")
    state = document("native")
    state["Mounts"].append(copy.deepcopy(state["Mounts"][0]))
    with pytest.raises(ValueError):
        check(state, "native")


def test_measured_native_parent_is_not_a_claimed_qlib_or_tag_parent():
    base = {
        "Id": "sha256:" + "a" * 64,
        "RootFS": {"Layers": ["layer1"]},
        "Config": {
            "Env": ["PATH=/fixed"],
            "Labels": {"io.tradegrad.environment_digest": "c" * 64},
            "Entrypoint": ["python", "/opt/tradegrad/entrypoint.py"],
            "WorkingDir": "/opt/tradegrad",
            "User": "65532:65532",
        },
    }
    child = {
        "Id": "sha256:" + "b" * 64,
        "RootFS": {"Layers": ["layer1", "layer2"]},
        "Config": {
            "Env": ["PATH=/fixed"],
            "Labels": {
                "io.mineagent.managed": "mineagent-disabled-openevolve-native-evaluator-v1"
            },
            "Entrypoint": list(contract.ENTRYPOINTS["native"]),
            "WorkingDir": "/",
            "User": "0:0",
            "Cmd": [],
        },
    }
    result = contract.image_inventory(base, child, role="native")
    assert result["base_id"] == base["Id"]
    bad = copy.deepcopy(base)
    bad["Config"]["Labels"]["io.tradegrad.environment_digest"] = "unversioned"
    with pytest.raises(ValueError):
        contract.image_inventory(bad, child, role="native")
    bad = copy.deepcopy(child)
    bad["RootFS"]["Layers"] = ["different", "layer2"]
    with pytest.raises(ValueError):
        contract.image_inventory(base, bad, role="native")
