"""Test-only closed SDK boundary observations; never retains wire contents."""

import math
import time
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock
from unittest.mock import patch

ACTIVE = ContextVar("oe_provider_diagnostic", default=None)


def safe(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except Exception:  # noqa: BLE001 -- diagnostics cannot change task behavior
        return None


def milliseconds(end, start, *, signed=False):
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in (end, start)):
        return "unknown"
    return int(max(-600 if signed else 0, min(600, end - start)) * 1000)


def exception_family(error):
    from packages.rdagent_provider.sdk_process import RejectedResponse

    return {
        TimeoutError: "timeout",
        BrokenPipeError: "broken_pipe",
        EOFError: "eof",
        OSError: "os_error",
        ValueError: "value",
        TypeError: "type",
        RejectedResponse: "response_rejected",
        KeyboardInterrupt: "cancelled",
        SystemExit: "cancelled",
    }.get(type(error), "other")


def wire_failure(error):
    # Exact static literals raised by _wire_exchange, never arbitrary messages.
    if (
        type(error) is ValueError
        and len(error.args) == 1
        and type(error.args[0]) is str
    ):
        known = {
            "child_input_closed": "input_closed",
            "child_output_limit": "output_limit",
        }
        if error.args[0] in known:
            return known[error.args[0]]
    return exception_family(error)


def envelope(raw):
    from packages.rdagent_provider.sdk_process import MAX_PIPE, strict_json

    if type(raw) is not bytes or len(raw) > MAX_PIPE:
        return "other"
    if not raw:
        return "empty"
    try:
        value = strict_json(raw)
    except Exception:  # noqa: BLE001 -- no raw JSON/error leaves the observer
        return "invalid"
    if type(value) is dict:
        if set(value) == {"ok", "response"} and value["ok"] is True:
            return "success_envelope"
        if (
            set(value) == {"ok", "error_class"}
            and value["ok"] is False
            and value["error_class"] == "provider_outcome_unknown"
        ):
            return "child_rejected"
    return "other"


def synthetic_count(calls):
    return min(len(calls), 17) if type(calls) is list else "unknown"


@contextmanager
def diagnose_provider(emit, synthetic_calls=None):
    from packages.openevolve_structured import provider_transport as oe
    from packages.rdagent_provider import transport

    execute, wire, validate = (
        oe.execute,
        transport._wire_exchange,
        transport._validated_result,
    )
    records, total, lock = [], 0, Lock()

    def begin(deadline):
        nonlocal total
        with lock:
            total = min(total + 1, 9)
            if len(records) == 8:
                return None
            row = {
                "entry_remaining_ms": milliseconds(
                    deadline, safe(time.monotonic), signed=True
                ),
                "wire": "not_entered",
                "validation": "not_entered",
            }
            records.append(row)
            return row

    def observed_execute(*args, **kwargs):
        row = safe(begin, kwargs.get("deadline_monotonic"))
        token = ACTIVE.set(row)
        try:
            return execute(*args, **kwargs)
        finally:
            ACTIVE.reset(token)

    def record(row, values):
        if row is not None:
            row.update(values)

    def observed_wire(*args, **kwargs):
        deadline = args[2] if len(args) > 2 else kwargs.get("deadline")
        row = ACTIVE.get()
        if row is None:
            return wire(*args, **kwargs)
        started = safe(time.monotonic)
        safe(
            record,
            row,
            {
                "wire": "entered",
                "wire_remaining_ms": safe(milliseconds, deadline, started, signed=True),
            },
        )
        try:
            result = wire(*args, **kwargs)
        except BaseException as error:
            finished = safe(time.monotonic)
            safe(
                record,
                row,
                {
                    "wire": "failed",
                    "wire_failure": safe(wire_failure, error) or "other",
                },
            )
            raise
        else:
            finished = safe(time.monotonic)
            safe(
                record,
                row,
                {"wire": "returned", "envelope": safe(envelope, result) or "other"},
            )
            return result
        finally:
            safe(
                record,
                row,
                {"wire_elapsed_ms": safe(milliseconds, finished, started)},
            )

    def observed_validate(*args, **kwargs):
        row = ACTIVE.get()
        if row is None:
            return validate(*args, **kwargs)
        safe(record, row, {"validation": "entered"})
        try:
            result = validate(*args, **kwargs)
        except BaseException as error:
            safe(
                record,
                row,
                {
                    "validation": "failed",
                    "validation_failure": safe(exception_family, error) or "other",
                },
            )
            raise
        safe(record, row, {"validation": "accepted"})
        return result

    try:
        with (
            patch.object(oe, "execute", observed_execute),
            patch.object(transport, "_wire_exchange", observed_wire),
            patch.object(transport, "_validated_result", observed_validate),
        ):
            yield
    finally:
        if total or synthetic_calls is not None:
            safe(
                emit,
                "OE_PROVIDER_PROGRESS_V1:",
                {
                    "count": total,
                    "overflow": total > 8,
                    "calls": records,
                    "synthetic_http_count": safe(synthetic_count, synthetic_calls),
                },
            )
