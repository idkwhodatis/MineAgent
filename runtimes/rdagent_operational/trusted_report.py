"""Bounded trusted metadata frames, not controller replies or positive-stop proof.

Only fixed root/caps-zero report children write these frames. The controller's
stdio is /dev/null. Host must map namespace PID/start to independently observed
SO_PEERCRED PID and direct protected-leader parentage before persisting evidence.
"""

import json
import os

SCHEMA = "mineagent.rdagent-controller-report.v1"
MAX_REPORT_BYTES = 1024
MAX_STREAM_BYTES = 2 * MAX_REPORT_BYTES


def report_bytes(event, child_namespace_pid, child_start_ticks, child_exit_code=None):
    if (
        event not in {"started", "quiescent"}
        or type(child_namespace_pid) is not int
        or child_namespace_pid <= 1
        or type(child_start_ticks) is not int
        or child_start_ticks <= 0
    ):
        raise ValueError("Exact trusted controller identity required")
    result = {
        "schema": SCHEMA,
        "event": event,
        "child_namespace_pid": child_namespace_pid,
        "child_start_ticks": child_start_ticks,
    }
    if event == "quiescent":
        if type(child_exit_code) is not int or not -64 <= child_exit_code <= 255:
            raise ValueError("Exact waitpid exit result required")
        result.update(child_exit_code=child_exit_code, descendants_reaped=True)
    elif child_exit_code is not None:
        raise ValueError("Started report cannot assert exit")
    raw = (
        json.dumps(
            result, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
        + b"\n"
    )
    if len(raw) > MAX_REPORT_BYTES:
        raise ValueError("Trusted report exceeds bound")
    return raw


def emit_report(event, child_namespace_pid, child_start_ticks, child_exit_code=None):
    # One <=PIPE_BUF write. The helper may block; PID1 never waits for it and
    # original namespace deadline still terminates it. No retry or stream flush.
    raw = report_bytes(event, child_namespace_pid, child_start_ticks, child_exit_code)
    if os.write(1, raw) != len(raw):
        raise OSError("Incomplete trusted report")
