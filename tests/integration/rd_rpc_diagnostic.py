"""Test-only bounded timings around unchanged actual RD RPC/commit boundaries."""

import json
import math
from collections import deque
from contextlib import contextmanager
from time import monotonic
from unittest.mock import patch
from weakref import WeakKeyDictionary

from sqlalchemy import event
from sqlalchemy.orm import Session


def _milliseconds(value):
    if not math.isfinite(value):
        return 0
    return max(-3_600_000, min(3_600_000, round(value * 1000)))


@contextmanager
def diagnose_rd_rpc():
    from runtimes.rdagent_operational import rpc
    from workers.research import rdagent_operational as worker

    original_serve, original_receive, original_send = (
        worker.serve_one,
        rpc.receive,
        rpc.send,
    )
    tail = deque(maxlen=64)
    totals = {}
    current = {"method": "unknown", "sequence": 0, "deadline": None}
    commits = WeakKeyDictionary()

    def record(stage, start, ok):
        deadline = current["deadline"]
        if deadline is None:
            return
        now = monotonic()
        elapsed = max(0, _milliseconds(now - start))
        key = (current["method"], stage)
        count, total, maximum = totals.get(key, (0, 0, 0))
        totals[key] = (
            count + 1,
            min(3_600_000, total + elapsed),
            max(maximum, elapsed),
        )
        tail.append(
            {
                "method": current["method"],
                "sequence": current["sequence"],
                "stage": stage,
                "elapsed_ms": elapsed,
                "remaining_ms": _milliseconds(deadline - now),
                "ok": ok,
            }
        )

    def timed(stage, call, *args, **kwargs):
        start, ok = monotonic(), False
        try:
            result = call(*args, **kwargs)
            if stage == "read" and type(result) is dict:
                name = result.get("method")
                current["method"] = (
                    name if type(name) is str and name in rpc.METHODS else "invalid"
                )
            ok = True
            return result
        finally:
            record(stage, start, ok)

    def receive(*args, **kwargs):
        return timed("read", original_receive, *args, **kwargs)

    def send(*args, **kwargs):
        return timed("send", original_send, *args, **kwargs)

    def serve(channel, *, sequence, deadline, authenticate, handle):
        current.update(method="unknown", sequence=sequence, deadline=deadline)
        try:
            return original_serve(
                channel,
                sequence=sequence,
                deadline=deadline,
                authenticate=lambda *a: timed("auth", authenticate, *a),
                handle=lambda *a: timed("handle", handle, *a),
            )
        finally:
            current["deadline"] = None

    def before_commit(session):
        if current["deadline"] is not None:
            commits[session] = monotonic()

    def finish_commit(session, ok):
        start = commits.pop(session, None)
        if start is not None:
            record("commit", start, ok)

    def after_commit(session):
        finish_commit(session, True)

    def after_rollback(session):
        finish_commit(session, False)

    callbacks = (
        ("before_commit", before_commit),
        ("after_commit", after_commit),
        ("after_rollback", after_rollback),
    )
    for name, callback in callbacks:
        event.listen(Session, name, callback)
    try:
        with (
            patch.object(worker, "serve_one", serve),
            patch.object(rpc, "receive", receive),
            patch.object(rpc, "send", send),
        ):
            yield
    finally:
        for name, callback in callbacks:
            event.remove(Session, name, callback)
        if totals:
            summary = [
                {
                    "method": method,
                    "stage": stage,
                    "count": count,
                    "total_ms": total,
                    "max_ms": maximum,
                }
                for (method, stage), (count, total, maximum) in sorted(totals.items())
            ]
            print(
                "RD_RPC_TIMING:"
                + json.dumps(
                    {"summary": summary, "tail": list(tail)},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                flush=True,
            )
