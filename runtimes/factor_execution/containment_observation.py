"""Actual kernel observations only; no candidate-provided PID or stop claims."""

import time

from runtimes.hostile_deadline.acceptance import (
    command,
    live_process,
    process_identity,
    remaining_command_budget,
    require_exporter_observation,
)


def observe_tree(cid, leader, original, limit):
    until = min(time.monotonic() + 3, original - 0.5)
    while time.monotonic() < until:
        rows = command(
            "docker",
            "top",
            cid,
            "-eo",
            "pid,uid,ppid,pidns,sid",
            timeout=remaining_command_budget(limit),
        ).splitlines()[1:]
        parsed = [row.split() for row in rows]
        if any(
            len(row) != 5
            or any(not value.isascii() or not value.isdecimal() for value in row)
            for row in parsed
        ):
            raise AssertionError("malformed bounded numeric tree inventory")
        if not 1 <= len(parsed) <= 16:
            raise AssertionError("numeric process bound")
        table = {int(row[0]): tuple(map(int, row[1:])) for row in parsed}
        if len(table) != len(parsed) or leader not in table or table[leader][0] != 0:
            raise AssertionError("ambiguous numeric PID1 identity")
        candidates = {pid: row for pid, row in table.items() if row[0] == 65533}
        if len(candidates) == 4:
            parents = [pid for pid, row in candidates.items() if row[1] == leader]
            if len(parents) != 1 or any(
                row[2] != table[leader][2] for row in candidates.values()
            ):
                raise AssertionError("numeric candidate escaped private tree")
            if sum(row[1] == parents[0] for row in candidates.values()) != 3:
                raise AssertionError("missing three real detached descendants")
            if any(row[3] != pid for pid, row in candidates.items()):
                time.sleep(0.02)
                continue
            identities = {pid: process_identity(pid) for pid in candidates}
            if not all(
                start is not None and live_process(pid, start)
                for pid, start in identities.items()
            ):
                raise AssertionError("numeric descendant not actually live")
            return identities
        time.sleep(0.02)
    raise AssertionError("four native-loaded candidate processes not observed")


def observe_exporter(cid, leader, original, limit):
    until = min(time.monotonic() + 3, original - 0.5)
    found = None
    while time.monotonic() < until:
        rows = command(
            "docker",
            "top",
            cid,
            "-eo",
            "pid,uid,ppid,pidns,comm",
            timeout=remaining_command_budget(limit),
        ).splitlines()[1:]
        if any(row.split()[-1] == "ma-export-v2" for row in rows):
            found = require_exporter_observation(rows, leader)
            break
        time.sleep(0.02)
    if found is None:
        raise AssertionError("missing live numeric exporter under unread attach")
    time.sleep(0.25)
    rows = command(
        "docker",
        "top",
        cid,
        "-eo",
        "pid,uid,ppid,pidns,comm",
        timeout=remaining_command_budget(limit),
    ).splitlines()[1:]
    if require_exporter_observation(rows, leader) != found:
        raise AssertionError("numeric exporter identity changed under unread stream")
    return {found[0]: found[1]}
