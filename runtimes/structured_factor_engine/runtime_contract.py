"""Disabled closed factor strategy engine profile; no caller launch grant."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

MAX_WALL_MS = 60_000
CANDIDATE_UID = CANDIDATE_GID = 65533
BOOTSTRAP_CAPS = ("KILL", "SETGID", "SETUID")
ENTRYPOINTS = {
    "engine": (
        "/usr/local/bin/python",
        "-I",
        "-m",
        "runtimes.structured_factor_engine.protected_deadline",
    )
}
INPUTS = {"engine": {"/input/strategy.json", "/input/market.json"}}


BASE_IMAGE = "measured-unchanged-portable-engine-parent"
SECCOMP_SHA256 = "6416b47770785a41ac59073cdc77d9fe98517df2799dc83ef207e622de3053f6"
MOBY_PROFILES_COMMIT = "4b8214fc4971e06172938a1f1554ccff6647ee80"
MASKED_PATHS = (
    "/proc/acpi",
    "/proc/asound",
    "/proc/interrupts",
    "/proc/kcore",
    "/proc/keys",
    "/proc/latency_stats",
    "/proc/sched_debug",
    "/proc/scsi",
    "/proc/timer_list",
    "/proc/timer_stats",
    "/sys/devices/virtual/powercap",
    "/sys/firmware",
)
READONLY_PATHS = (
    "/proc/bus",
    "/proc/fs",
    "/proc/irq",
    "/proc/sys",
    "/proc/sysrq-trigger",
)


def seccomp_policy() -> dict:
    raw = (
        Path(__file__).parents[1] / "hostile_deadline" / "seccomp.default.json"
    ).read_bytes()
    if hashlib.sha256(raw).hexdigest() != SECCOMP_SHA256:
        raise ValueError("pinned seccomp policy drift")
    return json.loads(raw)


def image_inventory(base: dict, image: dict, *, role: str) -> dict:
    """Record exact installed parent and successor; not installation authority."""
    if (
        role != "engine"
        or base["Config"].get("Labels", {}).get("io.tradegrad.portable_role")
        != "engine"
        or re.fullmatch(
            r"[0-9a-f]{64}",
            base["Config"].get("Labels", {}).get("io.tradegrad.environment_digest", ""),
        )
        is None
        or base["Config"].get("Entrypoint")
        != ["python", "-m", "runtimes.portable_engine.entrypoint"]
        or base["Config"].get("User") != "65532:65532"
        or base["Config"].get("WorkingDir") != "/opt/tradegrad"
    ):
        raise ValueError("Expected measured unchanged trusted engine parent")
    if any(
        re.fullmatch(r"sha256:[0-9a-f]{64}", document.get("Id", "")) is None
        for document in (base, image)
    ):
        raise ValueError("Exact measured image IDs required")
    parent_layers = base.get("RootFS", {}).get("Layers", [])
    child_layers = image.get("RootFS", {}).get("Layers", [])
    if (
        not parent_layers
        or len(child_layers) <= len(parent_layers)
        or child_layers[: len(parent_layers)] != parent_layers
    ):
        raise ValueError("Direct image does not retain installed parent")
    expected_env = base["Config"]["Env"]
    if not isinstance(expected_env, list) or len(
        {entry.split("=", 1)[0] for entry in expected_env}
    ) != len(expected_env):
        raise ValueError("Invalid installed parent environment")
    config = image["Config"]
    if (
        config.get("Env") != expected_env
        or config.get("WorkingDir") != "/"
        or config.get("User") != "0:0"
        or config.get("Entrypoint") != list(ENTRYPOINTS[role])
        or config.get("Cmd") not in (None, [])
    ):
        raise ValueError("Direct image execution policy differs")
    if (
        config.get("Volumes")
        or config.get("Healthcheck")
        or config.get("Labels", {}).get("io.mineagent.managed")
        != "mineagent-disabled-structured-factor-engine-v1"
    ):
        raise ValueError("Direct image implicit authority or identity differs")
    return {
        "base_ref": BASE_IMAGE,
        "base_id": base["Id"],
        "image_id": image["Id"],
        "environment": list(expected_env),
        "working_directory": "/",
    }


def remaining_seconds(deadline_ms: str, now_ms: int) -> float:
    if (
        not deadline_ms.isascii()
        or not deadline_ms.isdecimal()
        or len(deadline_ms) > 16
    ):
        raise ValueError("invalid absolute deadline")
    remaining = int(deadline_ms) - now_ms
    if not 0 < remaining <= MAX_WALL_MS:
        raise ValueError("expired or excessive deadline")
    return remaining / 1000


def require_launch_authority() -> None:
    raise RuntimeError(
        "hostile deadline profile has not completed hosted acceptance or admission"
    )


def bootstrap_capabilities(value) -> frozenset[str]:
    """Accept only Docker's bare or canonical CAP_ spelling, not more authority.

    Moby NormalizeLegacyCapabilities adds CAP_ to non-ALL capabilities:
    https://github.com/moby/moby/blob/v28.3.2/oci/caps/utils.go
    Preserve duplicate/unknown rejection rather than hiding them in a set.
    """
    if type(value) is not list or len(value) != len(BOOTSTRAP_CAPS):
        raise ValueError("capability policy drift")
    if any(type(cap) is not str for cap in value):
        raise ValueError("capability policy drift")
    normalized = tuple(cap.removeprefix("CAP_") for cap in value)
    if len(set(normalized)) != len(normalized) or set(normalized) != set(
        BOOTSTRAP_CAPS
    ):
        raise ValueError("capability policy drift")
    return frozenset(normalized)


def verify_inspect(
    document: dict,
    *,
    image: str,
    inputs: dict[str, str],
    role: str,
    deadline_ms: str,
    inventory: dict,
    masked_paths: list[str],
) -> None:
    """Exact proof-launch checks, not a reusable production admission check."""
    if role not in INPUTS or set(inputs) != INPUTS[role]:
        raise ValueError("closed numeric input inventory required")
    host, config = document["HostConfig"], document["Config"]
    expected = {
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
    }
    if any(host.get(key) != value for key, value in expected.items()):
        raise ValueError("host namespace/resource policy drift")
    bootstrap_capabilities(host.get("CapAdd"))
    if host.get("CapDrop") != ["ALL"]:
        raise ValueError("capability policy drift")
    options = host.get("SecurityOpt") or []
    if len(options) != 2 or "no-new-privileges" not in options:
        raise ValueError("security option drift")
    policies = [
        value.removeprefix("seccomp=")
        for value in options
        if value.startswith("seccomp=")
    ]
    if len(policies) != 1 or json.loads(policies[0]) != seccomp_policy():
        raise ValueError("seccomp policy drift")
    if sorted(host.get("MaskedPaths") or []) != sorted(masked_paths) or sorted(
        host.get("ReadonlyPaths") or []
    ) != sorted(READONLY_PATHS):
        raise ValueError("masked/readonly path policy drift")
    if inventory["image_id"] != image or inventory["base_ref"] != BASE_IMAGE:
        raise ValueError("image inventory identity drift")
    if (
        config.get("Env") != inventory["environment"]
        or config.get("WorkingDir") != inventory["working_directory"]
    ):
        raise ValueError("effective environment/workdir drift")
    if config.get("Volumes") or config.get("Healthcheck"):
        raise ValueError("implicit process/mount authority")
    if (
        host.get("Init") not in (None, False)
        or host.get("Devices")
        or host.get("DeviceRequests")
    ):
        raise ValueError("init/device authority drift")
    if (
        config.get("Tty") is not False
        or config.get("OpenStdin") is not False
        or host.get("LogConfig") != {"Type": "none", "Config": {}}
    ):
        raise ValueError("protected attach channel policy drift")
    if host.get("Binds") or host.get("VolumesFrom") or host.get("Links"):
        raise ValueError("unexpected mount or peer authority")
    if (
        config.get("User") != "0:0"
        or config.get("Entrypoint") != list(ENTRYPOINTS[role])
        or config.get("Cmd") != [deadline_ms]
    ):
        raise ValueError("entrypoint/credential drift")
    if document.get("Image") != image:
        raise ValueError("image drift")
    mounts = document.get("Mounts", [])
    expected_mounts = {(src, dst) for dst, src in inputs.items()}
    if (
        len(mounts) != len(inputs)
        or {(mount.get("Source"), mount.get("Destination")) for mount in mounts}
        != expected_mounts
        or any(
            mount.get("Type") != "bind" or mount.get("RW") is not False
            for mount in mounts
        )
    ):
        raise ValueError("numeric mounted input inventory drift")
    if host.get("Tmpfs") != {
        "/tmp": "rw,noexec,nosuid,nodev,size=32m,uid=65533,gid=65533,mode=0700",
        "/output": "rw,noexec,nosuid,nodev,size=20m,uid=0,gid=0,mode=0733",
        "/status": "rw,noexec,nosuid,nodev,size=64k,uid=0,gid=0,mode=0700",
    }:
        raise ValueError("tmpfs drift")
