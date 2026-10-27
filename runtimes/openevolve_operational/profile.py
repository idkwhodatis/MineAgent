"""Fixed OE physical profile; neither a mutable config nor launch authority.

Copying RD lifecycle mechanics does not inherit its 60-second acceptance. This
600-second, checkpoint-writing, one-pool-worker profile needs its own actual
host-loss/deadline/no-reader and process identity gates before registration.
"""

from packages.research_runtime.broker_contracts import digest

PROFILE = "mineagent.openevolve-orchestrator.v1"
MAX_WALL_MS = 600_000
MEMORY_BYTES = 2 * 1024**3
PIDS = 64
NOFILE = 256
FSIZE = 8 * 1024**2
TMP_BYTES = 16 * 1024**2
WORK_BYTES = 64 * 1024**2
SHM_BYTES = 64 * 1024
BOOTSTRAP_BYTES = 262_144
SEED_BYTES = 32_768
MAX_CHECKPOINT_BYTES = 8 * 1024**2
# Includes tmpfs maxima, two complete bootstrap/seed staging copies, and a
# conservative 1MiB IPC/journal allowance. Evidence objects have separate holds.
STORAGE_FLOOR = (
    TMP_BYTES + WORK_BYTES + SHM_BYTES + 2 * (BOOTSTRAP_BYTES + SEED_BYTES) + 1024**2
)


def inventory():
    return {
        "schema_version": "mineagent.openevolve-physical-profile.v1",
        "profile": PROFILE,
        "wall_milliseconds": MAX_WALL_MS,
        "memory_bytes": MEMORY_BYTES,
        "pids": PIDS,
        "nofile": NOFILE,
        "fsize": FSIZE,
        "tmp_bytes": TMP_BYTES,
        "work_bytes": WORK_BYTES,
        "shm_bytes": SHM_BYTES,
        "bootstrap_bytes": BOOTSTRAP_BYTES,
        "seed_bytes": SEED_BYTES,
        "storage_floor": STORAGE_FLOOR,
        "leader_uid": 0,
        "leader_gid": 0,
        "controller_uid": 65533,
        "controller_gid": 65533,
        "bootstrap_files": ["bootstrap.json", "seed.py"],
        "ipc_files": ["broker.sock", "events.sock", "ticket.json"],
        "checkpoint_transfer": "bounded_host_object_upload_not_host_mount",
        "network": "none",
        "readonly_root": True,
        "tmpfs_noexec": True,
    }


def profile_sha256():
    return digest(inventory())
