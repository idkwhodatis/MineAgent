"""Fixed bounded analysis observer; no caller-selected transport or namespace guess.

Reuse frozen controller parsing and before/after host identity checks, but every
CLI operation uses the numeric client's original absolute monotonic deadline.
"""

import re
import time
from pathlib import Path
from uuid import UUID

from packages.research_runtime.process_contracts import ProcessError
from runtimes.rdagent_operational.daemon_identity import (
    TOP_FIELDS,
    _host_snapshot,
    _inspect,
    parse_top,
)
from runtimes.research_supervisor.numeric_supervisor import NumericDockerClient


def observe_leader(container_id, image_id, leader_pid, deadline):
    if (
        type(container_id) is not str
        or re.fullmatch(r"[a-f0-9]{64}", container_id) is None
        or type(image_id) is not str
        or re.fullmatch(r"sha256:[a-f0-9]{64}", image_id) is None
        or type(leader_pid) is not int
        or leader_pid <= 1
        or type(deadline) not in (int, float)
        or not 0 < deadline - time.monotonic() <= 60
    ):
        raise ProcessError("analysis_process_kernel_identity")
    cli = NumericDockerClient()
    cli.deadline = deadline
    _inspect(cli, container_id, image_id, leader_pid)
    before = _host_snapshot(leader_pid)
    _, raw = cli._command(["top", container_id, "-eo", TOP_FIELDS])
    rows = parse_top(raw)
    after = _host_snapshot(leader_pid)
    _inspect(cli, container_id, image_id, leader_pid)
    if (
        before != after
        or leader_pid not in rows
        or rows[leader_pid][:2] != (after[4][1], after[3])
        or after[4] != (0, 0, 0, 0)
        or after[5] != (0, 0, 0, 0)
        or time.monotonic() > deadline
    ):
        raise ProcessError("analysis_process_kernel_identity")
    # Daemon-supplied real namespace inode, independently retained host start tick.
    return (
        after[1],
        rows[leader_pid][2],
        UUID(Path("/proc/sys/kernel/random/boot_id").read_text().strip()),
    )
