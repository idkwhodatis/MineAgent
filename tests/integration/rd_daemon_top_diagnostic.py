"""Test-only closed projection of rejected daemon rows, never identity or commands."""

import json
import re
from collections import deque
from contextlib import contextmanager
from unittest.mock import patch


def top_shape(raw):
    from runtimes.rdagent_operational import daemon_identity as daemon

    if type(raw) is not bytes or not 1 <= len(raw) <= daemon.MAX_TOP_BYTES:
        return {"category": "type_or_size"}
    try:
        lines = raw.decode("ascii").splitlines()
    except UnicodeError:
        return {"category": "non_ascii"}
    if not 2 <= len(lines) <= daemon.MAX_PROCESSES + 1:
        return {"category": "line_count"}
    if lines[0].split() != ["PID", "UID", "PPID", "PIDNS", "COMMAND"]:
        return {"category": "header"}
    # Only fixed field labels and classifications leave this function. No values,
    # PIDs, command names, raw output, exception text or namespace IDs are retained.
    for line in lines[1:]:
        fields = line.split()
        if len(fields) != 5:
            return {
                "category": "row_width",
                "width": "short" if len(fields) < 5 else "long",
            }
        for label, value in zip(
            ("pid", "uid", "ppid", "pidns"), fields[:4], strict=True
        ):
            if re.fullmatch(r"[0-9]{1,20}", value) is None:
                return {
                    "category": "numeric_field",
                    "field": label,
                    "kind": "dash" if value == "-" else "other",
                }
    return {"category": "other_identity_check"}


@contextmanager
def diagnose_daemon_top():
    from packages.research_runtime.process_contracts import ProcessError
    from runtimes.rdagent_operational import daemon_identity as daemon

    original = daemon.parse_top
    failures = deque(maxlen=8)

    def observed(raw):
        try:
            return original(raw)
        except ProcessError:
            try:
                failures.append(top_shape(raw))
            except Exception:  # noqa: BLE001 - diagnostics never replace failure
                failures.append({"category": "unavailable"})
            raise

    try:
        with patch.object(daemon, "parse_top", observed):
            yield
    finally:
        if failures:
            try:
                print(
                    "RD_DAEMON_TOP_SHAPE_V1:"
                    + json.dumps(list(failures), sort_keys=True),
                    flush=True,
                )
            except Exception:  # noqa: BLE001,S110 - preserve original failure
                pass
