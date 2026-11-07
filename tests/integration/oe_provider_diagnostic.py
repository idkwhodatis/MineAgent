"""Closed test-only SDK timing and callback failures, before transport sanitization.

Observes the existing call and receipt callbacks exactly once. No extra queries,
process inspection, payload reads, retry, deadline refresh or settlement occurs.
The original exception still reaches the transport and queue unchanged.
"""

import time
from contextlib import contextmanager
from unittest.mock import patch


def remaining_ms(deadline_ns, now_ns):
    if type(deadline_ns) is not int or type(now_ns) is not int:
        return None
    return max(0, min(600000, (deadline_ns - now_ns) // 1_000_000))


@contextmanager
def diagnose_provider(emit, failure):
    from backend.app import oe_structured_provider_broker as broker

    original_complete = broker.complete_chat
    original_retain = broker.retain_physical_receipt
    records, count = [], 0

    def observe(phase, deadline_ns, call):
        nonlocal count
        record, started = None, None
        try:
            started = time.monotonic_ns()
            record = {
                "phase": phase,
                "remaining_ms": remaining_ms(deadline_ns, started),
                "completed": False,
            }
            count = min(count + 1, 10000)
            if len(records) < 16:
                records.append(record)
            else:
                records[-1] = record
        except Exception:  # noqa: BLE001, S110 -- observation must not prevent the call
            pass
        try:
            result = call()
        except BaseException as error:
            try:
                if record is not None:
                    record["failure"] = failure(error)
            except Exception:  # noqa: BLE001, S110 -- preserve the exact original failure
                pass
            raise
        else:
            if record is not None:
                record["completed"] = True
            return result
        finally:
            try:
                if record is not None and started is not None:
                    record["elapsed_ms"] = max(
                        0, min(600000, (time.monotonic_ns() - started) // 1_000_000)
                    )
            except Exception:  # noqa: BLE001, S110 -- never mask the original result
                pass

    def retain(*args, **kwargs):
        phase, deadline_ns = "other_receipt", None
        try:
            document = args[4] if len(args) > 4 else kwargs.get("document")
            kind = document.kind
            if type(kind) is str and kind in {"intent", "started", "stopped"}:
                phase = kind + "_receipt"
                deadline_ns = (
                    document.deadline_monotonic_ns
                    if kind == "intent"
                    else document.identity.deadline_monotonic_ns
                )
        except Exception:  # noqa: BLE001, S110 -- malformed evidence is the callee's error
            pass
        return observe(phase, deadline_ns, lambda: original_retain(*args, **kwargs))

    def complete(*args, **kwargs):
        deadline_ns = None
        try:
            deadline = kwargs.get("deadline_monotonic")
            if type(deadline) in (int, float):
                deadline_ns = int(deadline * 1_000_000_000)
        except (ValueError, OverflowError):
            pass
        boundary = kwargs.get("identity_boundary")
        if boundary is not None:

            def observed_boundary(event, evidence):
                phase = (
                    event + "_boundary"
                    if type(event) is str and event in {"started", "stopped"}
                    else "other_boundary"
                )
                return observe(phase, deadline_ns, lambda: boundary(event, evidence))

            kwargs = {**kwargs, "identity_boundary": observed_boundary}
        return observe("sdk", deadline_ns, lambda: original_complete(*args, **kwargs))

    try:
        with (
            patch.object(broker, "complete_chat", complete),
            patch.object(broker, "retain_physical_receipt", retain),
        ):
            yield
    finally:
        if records:
            try:
                emit(
                    "OE_PROVIDER_PROGRESS_V1:",
                    {"records": records, "count": count, "overflow": count > 16},
                )
            except Exception:  # noqa: BLE001, S110 -- diagnostic sink cannot mask the task
                pass
