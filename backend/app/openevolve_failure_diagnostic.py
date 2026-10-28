"""Finite failure observations only; never peer, completion or stop authority."""

import json
import math

from packages.research_runtime.process_contracts import ProcessError
from runtimes.openevolve_operational.transport import EVENTS, UPLOADS


def event_label(value):
    return (
        value
        if type(value) is str and value in (*EVENTS, *UPLOADS, "broker", "none")
        else "unknown"
    )


def count(value, maximum):
    return min(maximum, max(0, value)) if type(value) is int else None


def seconds_bucket(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        return "unknown"
    if value < 0:
        return "negative"
    for limit in (5, 30, 95, 300, 600):
        if value <= limit:
            return f"le_{limit}"
    return "over_600"


def exception_label(error):
    # Identity only: no error properties, args, repr or metaclass hash/equality.
    for cls, label in (
        (ValueError, "value"),
        (ProcessError, "process"),
        (TimeoutError, "timeout"),
        (OSError, "os"),
    ):
        if type(error) is cls:
            return label
    return "other"


def project_progress(value):
    """Sink reprojects closed scalar fields; never print an arbitrary mapping."""
    expected = {
        "elapsed_seconds_bucket",
        "remaining_seconds_bucket",
        "requests",
        "acknowledgements",
        "active_handlers",
        "authenticated_last_event",
        "first_handler_failure",
    }
    if (
        type(value) is not dict
        or any(type(k) is not str for k in value)
        or set(value) != expected
    ):
        return {"service": "invalid_diagnostic"}
    buckets = (
        "unknown",
        "negative",
        "le_5",
        "le_30",
        "le_95",
        "le_300",
        "le_600",
        "over_600",
    )

    def bucket(v):
        return v if type(v) is str and v in buckets else "unknown"

    failure = value["first_handler_failure"]
    projected = None
    if failure is not None:
        if (
            type(failure) is not dict
            or any(type(k) is not str for k in failure)
            or set(failure)
            != {
                "phase",
                "untrusted_event_kind",
                "exception",
                "elapsed_seconds_bucket",
                "remaining_seconds_bucket",
            }
        ):
            return {"service": "invalid_diagnostic"}
        phase, error = failure["phase"], failure["exception"]
        projected = {
            "elapsed_seconds_bucket": bucket(failure["elapsed_seconds_bucket"]),
            "remaining_seconds_bucket": bucket(failure["remaining_seconds_bucket"]),
            "phase": phase
            if type(phase) is str and phase in ("frame", "auth", "dispatch", "reply")
            else "unknown",
            "untrusted_event_kind": event_label(failure["untrusted_event_kind"]),
            "exception": error
            if type(error) is str
            and error in ("value", "process", "timeout", "os", "other")
            else "other",
        }
    return {
        "elapsed_seconds_bucket": bucket(value["elapsed_seconds_bucket"]),
        "remaining_seconds_bucket": bucket(value["remaining_seconds_bucket"]),
        "requests": count(value["requests"], 512),
        "acknowledgements": count(value["acknowledgements"], 512),
        "active_handlers": count(value["active_handlers"], 4),
        "authenticated_last_event": event_label(value["authenticated_last_event"]),
        "first_handler_failure": projected,
    }


def emit_failure(stage, service, running, error):
    """Best effort, bounded output; diagnostics cannot replace the original error."""
    try:
        document = {
            "stage": "before_cleanup" if stage == "before_cleanup" else "after_cleanup"
        }
        if stage == "before_cleanup":
            document.update(
                project_progress(service.failure_diagnostic())
                if service is not None
                else {"service": "not_started"}
            )
            document["exception"] = exception_label(error)
        else:
            value = getattr(running, "measured_exit_code", None)
            document["exit_code"] = (
                value if type(value) is int and 0 <= value <= 255 else None
            )
            value = getattr(running, "measured_oom_killed", None)
            document["oom_killed"] = value if type(value) is bool else None
            # Physical supervisor completion only, not callback/ledger success.
            document["stop_confirmed"] = getattr(running, "finished", None) is True
        print(
            "OE_INITIAL_FAILURE_DIAGNOSTIC_V1:" + json.dumps(document, sort_keys=True),
            flush=True,
        )
    except BaseException:  # noqa: BLE001, S110 - diagnostics must never replace the original failure.
        pass
