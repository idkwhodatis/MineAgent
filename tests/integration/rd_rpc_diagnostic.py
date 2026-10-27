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


def _queue_failure(error):
    """Closed diagnostic vocabulary; never stringify arbitrary exceptions."""
    from sqlalchemy.exc import DBAPIError

    from backend.app.services import DomainError
    from packages.rdagent_provider.transport import ProviderFailure
    from packages.research_runtime.process_contracts import ProcessError
    from runtimes.rdagent_operational.rpc import RPCError

    family = {
        RPCError: "rpc",
        ProcessError: "process",
        ProviderFailure: "provider",
        DomainError: "domain",
        ValueError: "value",
        TimeoutError: "timeout",
    }.get(type(error), "other")
    code = getattr(error, "code", None)
    if type(error) is RPCError:
        code = error.args[0] if len(error.args) == 1 else None
    allowed = {
        "deadline",
        "rd_controller_daemon_identity",
        "rd_controller_peer",
        "rd_controller_not_running",
        "rd_controller_launch",
        "rd_controller_receipts",
        "rd_controller_peer_drift",
        "rd_controller_incomplete_generation",
        "rd_provider_containment_unknown",
        "original_controller_deadline",
        "provider_secret_unavailable",
        "provider_timeout",
        "provider_outcome_unknown",
        "provider_containment_unconfirmed",
        "rd_controller_deadline",
        "rd_controller_expired_before_create",
        "rd_controller_expired_before_start",
        "rd_controller_expired_peer",
        "rd_controller_kernel_deadline_exceeded",
        "rd_controller_original_deadline_completion_missing",
        "rd_controller_incomplete_or_failed_reports",
        "rd_controller_daemon_stop_uncertain",
        "rd_controller_absence_uncertain",
        "neutral_transaction_lost",
        "neutral_busy",
        "numeric_process_command_deadline",
        "numeric_process_command_cleanup",
    }
    code = code if type(code) is str and code in allowed else "unknown"
    sqlstate = "none"
    if isinstance(error, DBAPIError):
        family = "database"
        value = getattr(error.orig, "sqlstate", None)
        sqlstate = (
            value
            if type(value) is str
            and value in {"23503", "23505", "23514", "40001", "55P03", "57014"}
            else "other"
        )
    stages = {
        ("workers.research.rdagent_queue", "execute"): "queue",
        ("workers.research.rdagent_operational", "_execute_controller"): "controller",
        ("runtimes.rdagent_operational.supervisor", "start"): "controller_start",
        (
            "runtimes.rdagent_operational.supervisor",
            "accept_controller",
        ): "controller_accept",
        ("runtimes.rdagent_operational.supervisor", "finish"): "controller_finish",
        ("runtimes.rdagent_operational.rpc", "receive"): "rpc_read",
        ("runtimes.rdagent_operational.rpc", "send"): "rpc_send",
        ("runtimes.rdagent_operational.peer", "_revalidate"): "peer",
        ("runtimes.rdagent_operational.daemon_identity", "_observe"): "observer",
        ("backend.app.rdagent_provider_broker", "submit_provider_request"): "provider",
        (
            "backend.app.rdagent_configured_evaluation",
            "execute_configured_evaluation",
        ): "evaluation",
        ("backend.app.research_lifecycle", "neutral_transaction"): "transaction",
    }
    stage, trace = "unknown", error.__traceback__
    while trace is not None:
        key = (trace.tb_frame.f_globals.get("__name__"), trace.tb_frame.f_code.co_name)
        stage = stages.get(key, stage)
        trace = trace.tb_next
    return {"family": family, "code": code, "stage": stage, "sqlstate": sqlstate}


def _queue_state(factory, run_id):
    from uuid import UUID

    from sqlalchemy import select

    from backend.app.models import ExecutionAttempt, ResearchRun
    from backend.app.research_process_models import (
        ResearchProcessAdmission,
        ResearchProcessState,
    )

    statuses = {
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "interrupted",
        "paused",
        "cancel_requested",
    }
    process_states = {"launch_intended", "uncertain", "running", "stopped"}
    roles = {
        "rd_orchestrator",
        "factor_cutoff_candidate",
        "factor_normalizer",
        "native_evaluator",
    }
    identity = UUID(str(run_id))
    with factory() as session:
        run = session.get(ResearchRun, identity)
        attempts = session.scalars(
            select(ExecutionAttempt).filter_by(run_id=identity).limit(9)
        ).all()
        units = session.execute(
            select(ResearchProcessAdmission.role, ResearchProcessState.status)
            .outerjoin(
                ResearchProcessState,
                ResearchProcessState.admission_id == ResearchProcessAdmission.id,
            )
            .where(ResearchProcessAdmission.run_id == identity)
            .limit(17)
        ).all()
        return {
            "status": run.status
            if run is not None and run.status in statuses
            else "unknown",
            "attempt_overflow": len(attempts) > 8,
            "attempts": [
                {
                    "status": a.status if a.status in statuses else "unknown",
                    "error": "rd_controller_execution_failed"
                    if a.error == "rd_controller_execution_failed"
                    else ("none" if a.error is None else "other"),
                    "stopped": a.sandbox_stopped is True,
                    "exit_code": a.exit_code
                    if type(a.exit_code) is int and 0 <= a.exit_code <= 255
                    else None,
                }
                for a in attempts[:8]
            ],
            "unit_overflow": len(units) > 16,
            "units": sorted(
                [
                    {
                        "role": role if role in roles else "other",
                        "state": state if state in process_states else "unknown",
                    }
                    for role, state in units[:16]
                ],
                key=lambda r: (r["role"], r["state"]),
            ),
        }


@contextmanager
def diagnose_rd_queue(factory, run_id):
    """Observe the original queue callback; retain its exception and retry policy."""
    from workers.research import rdagent_queue

    original = rdagent_queue.execute
    failures = 0

    def execute(*args, **kwargs):
        nonlocal failures
        try:
            return original(*args, **kwargs)
        except BaseException as error:
            if failures < 8:
                try:
                    diagnostic = _queue_failure(error)
                except Exception:  # noqa: BLE001 - retain the original task failure
                    diagnostic = {
                        "family": "other",
                        "code": "unknown",
                        "stage": "unknown",
                        "sqlstate": "none",
                    }
                print(
                    "RD_QUEUE_FAILURE:" + json.dumps(diagnostic, sort_keys=True),
                    flush=True,
                )
                failures += 1
            raise

    try:
        with diagnose_rd_rpc(), patch.object(rdagent_queue, "execute", execute):
            yield
    finally:
        try:
            state = _queue_state(factory, run_id)
        except Exception:  # noqa: BLE001 - diagnostics cannot replace the failure
            state = {"status": "diagnostic_unavailable"}
        print("RD_QUEUE_STATE:" + json.dumps(state, sort_keys=True), flush=True)
