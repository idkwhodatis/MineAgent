"""Disabled numeric role contracts using inspect dictionaries; never launch Docker."""

import copy
import json

import pytest

from runtimes.factor_execution import runtime_contract as contract

ROLES = ("candidate", "normalizer")
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
            "Memory": 256 * 1024 * 1024,
            "MemorySwap": 256 * 1024 * 1024,
            "PidsLimit": 16,
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
                "/tmp": "rw,noexec,nosuid,nodev,size=4m,uid=65533,gid=65533,mode=0700",
                "/output": "rw,noexec,nosuid,nodev,size=16m,uid=0,gid=0,mode=0733",
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


@pytest.mark.parametrize("role", ROLES)
def test_complete_role_projection_remains_disabled(role):
    value = document(role)
    assert len(value["Mounts"]) == (4 if role == "candidate" else 3)
    check(value, role)
    value["Mounts"].reverse()
    check(value, role)
    with pytest.raises(RuntimeError):
        contract.require_launch_authority()


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "extra",
        "duplicate",
        "source",
        "destination",
        "rw",
        "rw_zero",
        "type",
        "ipc",
    ],
)
def test_exact_role_mount_inventory_rejects_drift(role, change):
    value = document(role)
    mounts = value["Mounts"]
    if change == "missing":
        mounts.pop()
    elif change == "extra":
        mounts.append(
            {"Type": "bind", "Source": "/secret", "Destination": "/extra", "RW": False}
        )
    elif change == "duplicate":
        mounts[1] = copy.deepcopy(mounts[0])
    elif change == "ipc":
        mounts.append(
            {"Type": "bind", "Source": "/socket", "Destination": "/ipc", "RW": False}
        )
    else:
        field, new = {
            "source": ("Source", "/other"),
            "destination": ("Destination", "/other"),
            "rw": ("RW", True),
            "rw_zero": ("RW", 0),
            "type": ("Type", "volume"),
        }[change]
        mounts[0][field] = new
    with pytest.raises(ValueError, match="mounted input inventory"):
        check(value, role)


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("change", ["missing", "extra", "other_role"])
def test_expected_inventory_is_closed_not_caller_expandable(role, change):
    values = inputs(role)
    if change == "missing":
        values.pop(next(iter(values)))
    elif change == "extra":
        values["/input/credentials"] = "/secret"
    else:
        values = inputs(next(name for name in ROLES if name != role))
    with pytest.raises(ValueError, match="closed numeric input inventory"):
        check(document(role), role, expected_inputs=values)


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize(
    "key,value",
    [
        ("Privileged", True),
        ("ReadonlyRootfs", False),
        ("NetworkMode", "host"),
        ("PidMode", "host"),
        ("IpcMode", "private"),
        ("IpcMode", "host"),
        ("UTSMode", "host"),
        ("UsernsMode", "host"),
        ("Memory", 0),
        ("MemorySwap", -1),
        ("PidsLimit", 0),
        ("NanoCpus", 0),
        ("AutoRemove", True),
        ("Init", True),
        ("Devices", [{}]),
        ("DeviceRequests", [{}]),
        ("CapAdd", ["SYS_ADMIN"]),
        ("CapDrop", []),
        ("SecurityOpt", []),
        ("Binds", ["/var/run/docker.sock"]),
        ("VolumesFrom", ["peer"]),
        ("Links", ["peer"]),
        ("LogConfig", {"Type": "json-file", "Config": {}}),
        ("MaskedPaths", []),
        ("ReadonlyPaths", []),
    ],
)
def test_namespace_resource_and_authority_changes_reject(role, key, value):
    data = document(role)
    data["HostConfig"][key] = value
    with pytest.raises(ValueError):
        check(data, role)


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize(
    "key,value",
    [
        ("User", "65533:65533"),
        ("Tty", True),
        ("OpenStdin", True),
        ("Cmd", ["10000"]),
        ("Env", ["SECRET=oops"]),
        ("WorkingDir", "/tmp"),
        ("Volumes", {"/data": {}}),
        ("Healthcheck", {"Test": ["CMD", "true"]}),
        ("Entrypoint", ["python", "-c", "bad"]),
    ],
)
def test_effective_image_invocation_drift_rejects(role, key, value):
    data = document(role)
    data["Config"][key] = value
    with pytest.raises(ValueError):
        check(data, role)


@pytest.mark.parametrize("role", ROLES)
def test_other_role_entrypoint_never_substitutes(role):
    data = document(role)
    other = next(name for name in ROLES if name != role)
    data["Config"]["Entrypoint"] = list(contract.ENTRYPOINTS[other])
    with pytest.raises(ValueError, match="entrypoint"):
        check(data, role)


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("path", ["/tmp", "/output", "/status", "/ipc"])
def test_tmpfs_inventory_and_modes_cannot_expand(role, path):
    data = document(role)
    data["HostConfig"]["Tmpfs"][path] = "rw,size=32m,mode=0777"
    with pytest.raises(ValueError, match="tmpfs"):
        check(data, role)


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("field", ["image", "inventory_image", "base_ref"])
def test_exact_image_identity_required(role, field):
    data, expected = document(role), inventory()
    if field == "image":
        data["Image"] = "sha256:" + "b" * 64
    elif field == "inventory_image":
        expected["image_id"] = "sha256:" + "b" * 64
    else:
        expected["base_ref"] = "python:latest"
    with pytest.raises(ValueError, match="image"):
        check(data, role, expected_inventory=expected)


def built_images(role):
    base = {
        "RepoDigests": [contract.BASE_IMAGE],
        "Id": "sha256:" + "c" * 64,
        "Config": {"Env": ["PATH=/fixed"]},
    }
    image = {"Id": IMAGE, "Config": document(role)["Config"]}
    image["Config"]["Cmd"] = None
    return base, image


@pytest.mark.parametrize("role", ROLES)
def test_image_inventory_pins_exact_role_entrypoint(role):
    base, image = built_images(role)
    actual = contract.image_inventory(base, image, role=role)
    assert actual["image_id"] == IMAGE and actual["base_ref"] == contract.BASE_IMAGE
    other = next(name for name in ROLES if name != role)
    with pytest.raises(ValueError, match="execution policy"):
        contract.image_inventory(base, image, role=other)


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize(
    "change",
    [
        "base_digest",
        "duplicate_env",
        "env",
        "entrypoint",
        "user",
        "volume",
        "healthcheck",
    ],
)
def test_image_inventory_rejects_build_policy_drift(role, change):
    base, image = built_images(role)
    if change == "base_digest":
        base["RepoDigests"] = ["python:latest@sha256:" + "f" * 64]
    elif change == "duplicate_env":
        base["Config"]["Env"] = ["PATH=/fixed", "PATH=/other"]
    else:
        field, value = {
            "env": ("Env", ["BAD=1"]),
            "entrypoint": ("Entrypoint", ["sh"]),
            "user": ("User", "1:1"),
            "volume": ("Volumes", {"/data": {}}),
            "healthcheck": ("Healthcheck", {"Test": ["CMD", "true"]}),
        }[change]
        image["Config"][field] = value
    with pytest.raises(ValueError):
        contract.image_inventory(base, image, role=role)


def test_unknown_role_rejected_before_launch_projection():
    base, image = built_images("candidate")
    with pytest.raises(ValueError, match="unknown numeric role"):
        contract.image_inventory(base, image, role="shell")
    with pytest.raises(ValueError, match="closed numeric input inventory"):
        contract.verify_inspect(
            {},
            image=IMAGE,
            inputs={},
            role="shell",
            deadline_ms="9000",
            inventory={},
            masked_paths=[],
        )
