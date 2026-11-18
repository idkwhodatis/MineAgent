"""Test-only, bounded observations of unchanged asynchronous lease pulses.

No database query, extra pulse, thread, wait, join or lease mutation occurs.
Successful returns include throttled no-ops, not proved durable renewals. Sampling
uses a nonblocking observer lock and may omit overlapping/in-flight observations.
Only closed data is retained; output occurs after the diagnostic scope closes.
"""

import json
import math
from contextlib import contextmanager
from threading import Lock
from time import monotonic
from unittest.mock import patch

MAXIMUM = 3_600_000


def _clock():
    try:
        value = monotonic()
        return value if type(value) in (int, float) and math.isfinite(value) else None
    except BaseException:  # noqa: BLE001 -- observations cannot change execution
        return None


def _elapsed(start, end):
    if start is None or end is None:
        return None
    return max(0, min(MAXIMUM, round((end - start) * 1000)))


@contextmanager
def diagnose_attempt_lease():
    from rd_rpc_diagnostic import _queue_failure

    from workers.research.lease import AttemptLease

    original = AttemptLease.pulse
    lock = Lock()
    samples = []
    counts = {"successful_pulse_returns": 0, "failed_pulse_calls": 0}
    totals = {"observed_total_ms": 0, "observed_max_ms": 0}

    def record(lease, start, error):
        end = _clock()
        elapsed = _elapsed(start, end)
        if not lock.acquire(blocking=False):
            return
        try:
            field = (
                "successful_pulse_returns" if error is None else "failed_pulse_calls"
            )
            counts[field] = min(1_000_000, counts[field] + 1)
            if elapsed is not None:
                totals["observed_total_ms"] = min(
                    MAXIMUM, totals["observed_total_ms"] + elapsed
                )
                totals["observed_max_ms"] = max(totals["observed_max_ms"], elapsed)
            if error is not None and len(samples) < 8:
                diagnostic = _queue_failure(error)
                last = lease.last_pulse
                age = (
                    _elapsed(last, end)
                    if type(last) in (int, float) and math.isfinite(last) and last > 0
                    else None
                )
                samples.append(
                    {
                        "family": diagnostic["family"],
                        "code": diagnostic["code"],
                        "sqlstate": diagnostic["sqlstate"],
                        "lost": lease.lost.is_set() is True,
                        "cancelled": lease.cancelled.is_set() is True,
                        "elapsed_ms": elapsed,
                        "last_success_age_ms": age,
                        "successful_pulse_returns": counts["successful_pulse_returns"],
                    }
                )
        finally:
            lock.release()

    def pulse(lease, *args, **kwargs):
        start = _clock()
        try:
            result = original(lease, *args, **kwargs)
        except BaseException as error:
            try:
                record(lease, start, error)
            except BaseException:  # noqa: BLE001, S110 -- preserve exact worker error
                pass
            raise
        else:
            try:
                record(lease, start, None)
            except BaseException:  # noqa: BLE001, S110 -- preserve exact worker result
                pass
            return result

    try:
        with patch.object(AttemptLease, "pulse", pulse):
            yield
    finally:
        try:
            report = {"status": "unavailable"}
            if lock.acquire(blocking=False):
                try:
                    report = {
                        "status": "best_effort_nonblocking",
                        **counts,
                        **totals,
                        "failures": list(samples),
                    }
                finally:
                    lock.release()
            print(
                "RD_LEASE_PULSES_V1:" + json.dumps(report, sort_keys=True), flush=True
            )
        except BaseException:  # noqa: BLE001, S110 -- output cannot replace worker error
            pass
