"""Test-only closed socket progress; never reads payloads or trusted report streams."""

import time
from contextlib import contextmanager
from unittest.mock import patch

from sqlalchemy import select

KINDS = frozenset(
    {
        "iteration_intent",
        "worker_started",
        "worker_outcome",
        "proposal_response",
        "proposal_source",
        "candidate_intent",
        "seed_source",
        "execute_evaluation",
        "evaluation_complete",
        "rendered_prompt",
        "terminal_applied",
        "checkpoint_prepare",
        "checkpoint_publish",
        "worker_placement",
        "checkpoint_upload_begin",
        "checkpoint_upload_chunk",
        "checkpoint_upload_finish",
    }
)


def milliseconds(seconds):
    if type(seconds) not in (int, float) or not 0 <= seconds <= 600:
        return 600000
    return int(seconds * 1000)


def progress(factory, run_id):
    """Six fixed run-scoped projections; 17 means at least 17, not an exact count."""
    from uuid import UUID

    from backend.app.oe_structured_checkpoint_models import (
        OEStructuredCheckpointPublication,
    )
    from backend.app.oe_structured_models import (
        OEStructuredCandidate,
        OEStructuredEngineOutcome,
        OEStructuredProviderExchange,
    )
    from backend.app.oe_structured_sampling_models import OEStructuredSamplingIntent
    from backend.app.oe_structured_terminal_models import (
        OEStructuredTerminalApplication,
    )

    identity = UUID(str(run_id))
    tables = (
        ("candidates", OEStructuredCandidate),
        ("outcomes", OEStructuredEngineOutcome),
        ("sampling", OEStructuredSamplingIntent),
        ("exchanges", OEStructuredProviderExchange),
        ("terminal", OEStructuredTerminalApplication),
        ("checkpoints", OEStructuredCheckpointPublication),
    )
    with factory() as session:
        return {
            name: min(
                17,
                len(
                    session.scalars(
                        select(model.id).where(model.run_id == identity).limit(17)
                    ).all()
                ),
            )
            for name, model in tables
        }


def measured(running):
    code = running.measured_exit_code
    oom = running.measured_oom_killed
    return {
        "exit_code": code if type(code) is int and 0 <= code <= 255 else None,
        "oom": oom if type(oom) is bool else None,
        "original_deadline": running.original_deadline_completion is True,
    }


@contextmanager
def diagnose_serve(factory, run_id, emit):
    from backend.app import oe_structured_socket_server as server
    from workers.research import oe_structured_controller as issuer

    original_serve = issuer.serve_controller
    original_event, original_read, original_write = (
        server._event,
        server.read_event,
        server.write_frame,
    )
    running, active = None, None
    records, count = [], 0

    def serve(*args, **kwargs):
        nonlocal running
        running = kwargs.get("running")
        return original_serve(*args, **kwargs)

    def read(*args, **kwargs):
        value = original_read(*args, **kwargs)
        if active is not None:
            kind = value.kind
            active["kind"] = kind if type(kind) is str and kind in KINDS else "other"
        return value

    def write(*args, **kwargs):
        result = original_write(*args, **kwargs)
        if active is not None:
            active["ack_written"] = True
        return result

    def event(*args, **kwargs):
        nonlocal active, count
        start = time.monotonic()
        active = {
            "kind": "other",
            "ack_written": False,
            "remaining_ms": milliseconds(max(0, running.deadline - start))
            if running is not None
            else None,
        }
        try:
            return original_event(*args, **kwargs)
        finally:
            active["elapsed_ms"] = milliseconds(time.monotonic() - start)
            count = min(count + 1, 10000)
            if len(records) < 64:
                records.append(active)
            else:
                records[-1] = active
            active = None

    try:
        with (
            patch.object(issuer, "serve_controller", serve),
            patch.object(server, "_event", event),
            patch.object(server, "read_event", read),
            patch.object(server, "write_frame", write),
        ):
            yield
    finally:
        # Outer queue has completed its existing cleanup before this projection.
        if running is not None:
            value = {"events": records, "count": count, "overflow": count > 64}
            try:
                value["unit"] = measured(running)
            except Exception:  # noqa: BLE001 -- diagnostic cannot mask original failure
                value["unit"] = {"unavailable": True}
            try:
                value["progress"] = progress(factory, run_id)
            except Exception:  # noqa: BLE001 -- diagnostic cannot mask original failure
                value["progress"] = {"unavailable": True}
            emit("OE_SERVE_PROGRESS_V1:", value)
