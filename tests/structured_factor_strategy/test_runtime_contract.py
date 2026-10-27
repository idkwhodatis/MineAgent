"""Disabled numeric role contracts using inspect dictionaries; never launch Docker."""

import copy
import json

import pytest

from runtimes.structured_factor_engine import runtime_contract as contract

ROLES = ("engine",)
IMAGE = "sha256:" + "a" * 64


def inputs(role):
    return {name: "/frozen" + name for name in sorted(contract.INPUTS[role])}


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
                "/tmp": "rw,noexec,nosuid,nodev,size=32m,uid=65533,gid=65533,mode=0700",
                "/output": "rw,noexec,nosuid,nodev,size=20m,uid=0,gid=0,mode=0733",
                "/status": "rw,noexec,nosuid,nodev,size=64k,uid=0,gid=0,mode=0700",
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
    check(document("engine"), "engine")
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
    state = document("engine")
    state["HostConfig"][field] = value
    with pytest.raises(ValueError):
        check(state, "engine")


def test_no_undeclared_or_writable_mounts():
    state = document("engine")
    state["Mounts"][0]["RW"] = True
    with pytest.raises(ValueError):
        check(state, "engine")
    state = document("engine")
    state["Mounts"].append(copy.deepcopy(state["Mounts"][0]))
    with pytest.raises(ValueError):
        check(state, "engine")


def test_measured_native_parent_is_not_a_claimed_qlib_or_tag_parent():
    base = {
        "Id": "sha256:" + "a" * 64,
        "RootFS": {"Layers": ["layer1"]},
        "Config": {
            "Env": ["PATH=/fixed"],
            "Labels": {
                "io.tradegrad.environment_digest": "c" * 64,
                "io.tradegrad.portable_role": "engine",
            },
            "Entrypoint": ["python", "-m", "runtimes.portable_engine.entrypoint"],
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
                "io.mineagent.managed": "mineagent-disabled-structured-factor-engine-v1"
            },
            "Entrypoint": list(contract.ENTRYPOINTS["engine"]),
            "WorkingDir": "/",
            "User": "0:0",
            "Cmd": [],
        },
    }
    result = contract.image_inventory(base, child, role="engine")
    assert result["base_id"] == base["Id"]
    bad = copy.deepcopy(base)
    bad["Config"]["Labels"]["io.tradegrad.environment_digest"] = "unversioned"
    with pytest.raises(ValueError):
        contract.image_inventory(bad, child, role="engine")
    bad = copy.deepcopy(child)
    bad["RootFS"]["Layers"] = ["different", "layer2"]
    with pytest.raises(ValueError):
        contract.image_inventory(base, bad, role="engine")
