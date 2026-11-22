"""Test-only timings around unchanged RD RPC/commit boundaries.

Only synchronous ancestry is attributed; another thread's SQL is not included.
Units are ancestry labels, not exclusive timings: a provider SDK callback may
perform database work. Fenced ownership is confirmed only after neutral entry
yields its session; lock or admission failure before that remains unconfirmed.
Transaction begin is SQLAlchemy logical start, not proof of a PostgreSQL BEGIN.
Commit intervals include Python/ORM/DB overhead, not exclusive SQL CPU time.
Ordinals are observer-local (RPC, then session and transaction within RPC).
"""

import json
import math
from collections import deque
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from itertools import count
from threading import get_ident
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
    from backend.app import rdagent_operational_broker as fixture_broker
    from backend.app import rdagent_provider_broker as provider_broker
    from backend.app import rdagent_provider_handler as provider_handler
    from backend.app import research_lifecycle as lifecycle
    from backend.app.rdagent_operational_handler import OperationalHandler
    from packages.research_runtime.resource_scopes import SCOPES
    from runtimes.rdagent_operational import rpc
    from tests.integration.rd_analysis_stage_diagnostic import diagnose_analysis_stages
    from workers.research import rdagent_operational as worker

    original_serve, original_receive, original_send = (
        worker.serve_one,
        rpc.receive,
        rpc.send,
    )
    tail = deque(maxlen=64)
    transactions = deque(maxlen=128)
    failures = []
    totals = {}
    transaction_counts = {"tail_evictions": 0, "rpc_budget_omissions": 0}
    active = ContextVar("rd_rpc_diagnostic", default=None)
    unit = ContextVar("rd_rpc_unit", default="outside_callback")
    ordinals = count(1)

    def current():
        value = active.get()
        return value if value is not None and value["thread"] == get_ident() else None

    def record(stage, start, ok, *, ended=None, **fields):
        try:
            rpc_state = current()
            if rpc_state is None:
                return
            now = monotonic() if ended is None else ended
            elapsed = max(0, _milliseconds(now - start))
            key = (rpc_state["method"], stage)
            previous, total, maximum = totals.get(key, (0, 0, 0))
            totals[key] = (
                min(1_000_000, previous + 1),
                min(3_600_000, total + elapsed),
                max(maximum, elapsed),
            )
            tail.append(
                {
                    "method": rpc_state["method"],
                    "sequence": rpc_state["sequence"],
                    "rpc_ordinal": rpc_state["ordinal"],
                    "stage": stage,
                    "elapsed_ms": elapsed,
                    "remaining_ms": _milliseconds(rpc_state["deadline"] - now),
                    "ok": ok,
                    **fields,
                }
            )
        except BaseException:  # noqa: BLE001, S110 - diagnostics cannot change execution
            pass

    def timed(stage, call, *args, **kwargs):
        rpc_state = current()
        if rpc_state is None:
            return call(*args, **kwargs)
        token = unit.set({"auth": "outer_auth", "handle": "handler"}.get(stage, stage))
        start, ok = monotonic(), False
        try:
            result = call(*args, **kwargs)
            if stage == "read" and type(result) is dict:
                name = result.get("method")
                rpc_state["method"] = (
                    name if type(name) is str and name in rpc.METHODS else "invalid"
                )
            ok = True
            return result
        except BaseException as error:
            if stage == "handle" and len(failures) < 8:
                # Observe the same handler exception before controller cleanup
                # replaces it with a whole-attempt containment failure.
                try:
                    failure = _rpc_failure_chain(error)
                except BaseException:  # noqa: BLE001 - diagnostics cannot mask failure
                    failure = {"status": "diagnostic_unavailable"}
                failures.append(
                    {
                        "method": rpc_state["method"],
                        "boundary": "handle_exception",
                        **failure,
                    }
                )
            raise
        finally:
            record(stage, start, ok)
            unit.reset(token)

    def receive(*args, **kwargs):
        return timed("read", original_receive, *args, **kwargs)

    def send(*args, **kwargs):
        return timed("send", original_send, *args, **kwargs)

    def serve(channel, *, sequence, deadline, authenticate, handle):
        token = active.set(
            {
                "method": "unknown",
                "sequence": sequence
                if type(sequence) is int and 0 <= sequence <= 1_000_000
                else None,
                "deadline": deadline,
                "ordinal": next(ordinals),
                "thread": get_ident(),
                "binding": (handle.factory, handle.fence)
                if isinstance(handle, OperationalHandler)
                else None,
                "sessions": WeakKeyDictionary(),
                "open": {},
                "next_session": 0,
                "next_transaction": 0,
            }
        )
        try:
            return original_serve(
                channel,
                sequence=sequence,
                deadline=deadline,
                authenticate=lambda *a: timed("auth", authenticate, *a),
                handle=lambda *a: timed("handle", handle, *a),
            )
        finally:
            active.reset(token)

    def on_start(session, transaction):
        try:
            rpc_state = current()
            if rpc_state is None or transaction.parent is not None:
                return
            if rpc_state["next_transaction"] >= 128:
                transaction_counts["rpc_budget_omissions"] = min(
                    1_000_000, transaction_counts["rpc_budget_omissions"] + 1
                )
                return
            sid = rpc_state["sessions"].get(session)
            if sid is None:
                rpc_state["next_session"] += 1
                sid = rpc_state["sessions"][session] = rpc_state["next_session"]
            rpc_state["next_transaction"] += 1
            now = monotonic()
            rpc_state["open"][transaction] = {
                "session_ordinal": sid,
                "transaction_ordinal": rpc_state["next_transaction"],
                "ownership": "unconfirmed",
                "unit": unit.get(),
                "resource_scope": "unconfirmed",
                "begin_remaining_ms": _milliseconds(rpc_state["deadline"] - now),
                "begin_time": now,
                "commit_time": None,
                "completion": "end_unclassified",
                "finished_time": None,
            }
        except BaseException:  # noqa: BLE001, S110 - test observer is best effort
            pass

    original_neutral = lifecycle._neutral_transaction_once

    @contextmanager
    def neutral(factory, fence, *, resource_scope="none"):
        with original_neutral(factory, fence, resource_scope=resource_scope) as session:
            try:
                rpc_state = current()
                binding = rpc_state["binding"] if rpc_state is not None else None
                if (
                    binding is not None
                    and factory is binding[0]
                    and fence == binding[1]
                ):
                    transaction = session.get_transaction()
                    observed = rpc_state["open"].get(transaction)
                    state = session.info.get(lifecycle._NEUTRAL_KEY)
                    if (
                        observed is not None
                        and type(resource_scope) is str
                        and resource_scope in SCOPES
                        and type(state) is dict
                        and state.get("transaction") is transaction
                        and state.get("fence") == binding[1]
                        and state.get("resource_scope") == resource_scope
                    ):
                        observed["ownership"] = "rpc_fenced_neutral"
                        observed["resource_scope"] = resource_scope
            except BaseException:  # noqa: BLE001, S110 - never alter transaction outcome
                pass
            yield session

    def observation(session):
        rpc_state = current()
        if rpc_state is None or session.in_nested_transaction():
            return None
        return rpc_state["open"].get(session.get_transaction())

    def before_commit(session):
        try:
            row = observation(session)
            if row is not None:
                row["commit_time"] = monotonic()
        except BaseException:  # noqa: BLE001, S110
            pass

    def mark_completion(session, completion):
        try:
            row = observation(session)
            if row is not None:
                row["completion"] = completion
                row["finished_time"] = monotonic()
        except BaseException:  # noqa: BLE001, S110
            pass

    def on_end(session, transaction):
        try:
            rpc_state = current()
            if rpc_state is None or transaction.parent is not None:
                return
            row = rpc_state["open"].pop(transaction, None)
            if row is None:
                return
            now = monotonic()
            start, commit = row.pop("begin_time"), row.pop("commit_time")
            finished = row.pop("finished_time")
            if finished is None:
                finished = now
            row["transaction_ms"] = max(0, _milliseconds(now - start))
            row["commit_elapsed_ms"] = (
                None if commit is None else max(0, _milliseconds(finished - commit))
            )
            row["commit_begin_remaining_ms"] = (
                None
                if commit is None
                else _milliseconds(rpc_state["deadline"] - commit)
            )
            if len(transactions) == transactions.maxlen:
                transaction_counts["tail_evictions"] = min(
                    1_000_000, transaction_counts["tail_evictions"] + 1
                )
            transactions.append(
                {
                    "method": rpc_state["method"],
                    "sequence": rpc_state["sequence"],
                    "rpc_ordinal": rpc_state["ordinal"],
                    "remaining_ms": _milliseconds(rpc_state["deadline"] - now),
                    "transaction_completion_remaining_ms": _milliseconds(
                        rpc_state["deadline"] - finished
                    ),
                    **row,
                }
            )
            if commit is not None:
                record(
                    "commit",
                    commit,
                    row["completion"] == "commit",
                    ended=finished,
                    ownership=row["ownership"],
                    unit=row["unit"],
                    resource_scope=row["resource_scope"],
                    session_ordinal=row["session_ordinal"],
                    transaction_ordinal=row["transaction_ordinal"],
                )
        except BaseException:  # noqa: BLE001, S110 - observer cannot change execution
            pass

    def phase(original, label):
        def wrapped(*args, **kwargs):
            if current() is None:
                return original(*args, **kwargs)
            token = unit.set(label)
            try:
                return original(*args, **kwargs)
            finally:
                unit.reset(token)

        return wrapped

    callbacks = (
        ("after_transaction_create", on_start),
        ("before_commit", before_commit),
        ("after_commit", lambda session: mark_completion(session, "commit")),
        ("after_rollback", lambda session: mark_completion(session, "rollback")),
        ("after_transaction_end", on_end),
    )
    phases = (
        (OperationalHandler, "_authorize", "handler_authorize"),
        (fixture_broker, "submit_fixture_request", "fixture_broker"),
        (provider_handler, "submit_provider_request", "provider_broker"),
        (provider_broker, "_publish", "provider_publication"),
        (provider_broker, "_retain_receipt", "provider_receipt"),
        (provider_broker, "execute", "provider_sdk"),
    )
    try:
        with ExitStack() as stack:
            for name, callback in callbacks:
                event.listen(Session, name, callback)
                stack.callback(event.remove, Session, name, callback)
            stack.enter_context(diagnose_analysis_stages())
            for obj, name, replacement in (
                (worker, "serve_one", serve),
                (rpc, "receive", receive),
                (rpc, "send", send),
                (lifecycle, "_neutral_transaction_once", neutral),
            ):
                stack.enter_context(patch.object(obj, name, replacement))
            for obj, name, label in phases:
                stack.enter_context(
                    patch.object(obj, name, phase(getattr(obj, name), label))
                )
            yield
    finally:
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
            report = {
                "summary": summary,
                "tail": list(tail),
                "transactions": list(transactions),
                "transaction_counts": transaction_counts,
            }
            if failures:
                report["failures"] = failures
            try:
                print(
                    "RD_RPC_TIMING:"
                    + json.dumps(report, sort_keys=True, separators=(",", ":")),
                    flush=True,
                )
            except BaseException:  # noqa: BLE001, S110 - do not mask the original failure
                pass


def _analysis_trace_context(error):
    """Exact code identities and primitive breadcrumbs, no extra physical probes.

    Constructed objects are not persistence or containment evidence. In
    particular, constructing an identity precedes writing its journal and
    calling the database callback. Missing keys mean the frame was not observed.
    """
    from inspect import unwrap

    from backend.app import analysis_launch_services
    from packages.research_runtime.analysis_process_journal import (
        AnalysisExtraction,
        AnalysisIdentity,
        AnalysisLaunch,
    )
    from runtimes.factor_execution.attach_stream import AttachStream
    from runtimes.qlib import analysis_transport, daemon_identity, runtime_contract
    from runtimes.research_supervisor import analysis_journal, analysis_supervisor
    from runtimes.research_supervisor.numeric_journal import read_numeric_record
    from runtimes.research_supervisor.numeric_supervisor import NumericDockerClient
    from workers.research import analysis_process

    supervisor = analysis_supervisor.AnalysisSupervisor
    stages = (
        (unwrap(analysis_process.execute_analysis_acceptance_unit).__code__, "worker"),
        (
            unwrap(analysis_launch_services.record_analysis_identity).__code__,
            "record_identity",
        ),
        (
            unwrap(analysis_launch_services.record_analysis_containment).__code__,
            "record_containment",
        ),
        (unwrap(supervisor.execute).__code__, "execute"),
        (unwrap(supervisor._verify_staged_launch).__code__, "verify_launch"),
        (unwrap(supervisor._verify).__code__, "verify_runtime"),
        (unwrap(supervisor._observe_identity).__code__, "observe_identity"),
        (unwrap(supervisor.stop_and_verify).__code__, "stop"),
        (unwrap(supervisor._cleanup_unobserved).__code__, "cleanup_unobserved"),
        (unwrap(daemon_identity.observe_leader).__code__, "observe_leader"),
        (unwrap(runtime_contract.verify_inspect).__code__, "verify_inspect"),
        (unwrap(analysis_transport.read_analysis_frame).__code__, "read_frame"),
        (unwrap(analysis_transport.require_live_analysis_frame).__code__, "live_frame"),
        (
            unwrap(analysis_transport.require_complete_analysis_stream).__code__,
            "complete_stream",
        ),
        (unwrap(analysis_journal.read_analysis_journal).__code__, "read_journal"),
        (unwrap(read_numeric_record).__code__, "read_record"),
        (unwrap(NumericDockerClient._command).__code__, "docker_command"),
        (unwrap(NumericDockerClient._inspect).__code__, "docker_inspect"),
    )
    operations = {
        ("image", "inspect"): "image_inspect",
        ("container", "inspect"): "container_inspect",
        ("create",): "create",
        ("start",): "start",
        ("top",): "top",
        ("rm", "--force"): "remove_force",
    }
    result, analysis_seen = {}, False
    shared_codes = (
        unwrap(read_numeric_record).__code__,
        unwrap(NumericDockerClient._command).__code__,
        unwrap(NumericDockerClient._inspect).__code__,
    )
    trace = error.__traceback__
    for _ in range(64):
        if trace is None:
            break
        frame = trace.tb_frame
        for code, label in stages:
            if frame.f_code is code:
                result["analysis_stage"] = label
                analysis_seen = analysis_seen or not any(
                    frame.f_code is shared for shared in shared_codes
                )
                break
        if frame.f_code is unwrap(supervisor.execute).__code__:
            for name, expected in (
                ("launch", AnalysisLaunch),
                ("reader", AttachStream),
                ("identity", AnalysisIdentity),
                ("extraction", AnalysisExtraction),
            ):
                result["analysis_" + name + "_constructed"] = (
                    type(frame.f_locals.get(name)) is expected
                )
        if frame.f_code is unwrap(NumericDockerClient._command).__code__:
            args = frame.f_locals.get("args")
            operation = "unknown"
            if type(args) is list:
                for prefix, label in operations.items():
                    head = args[: len(prefix)]
                    if (
                        all(type(value) is str for value in head)
                        and tuple(head) == prefix
                    ):
                        operation = label
                        break
            result["docker_operation"] = operation
        trace = trace.tb_next
    if trace is not None:
        result["trace_overflow"] = True
    if not analysis_seen:
        result.pop("analysis_stage", None)
    return result


def _rpc_failure_chain(error):
    """Bounded cause/context graph, including suppressed cleanup context.

    Python keeps the initiating exception in __context__ even when a cleanup
    raises from None. Neither that suppression nor a new explicit cause is
    permission to discard the original failure from this private test probe.
    """
    pending, seen, errors = deque([(error, "raised", None)]), set(), []
    while pending and len(errors) < 8:
        current, relation, parent = pending.popleft()
        if id(current) in seen:
            continue
        seen.add(id(current))
        index = len(errors)
        errors.append(
            {
                "relation": relation,
                "parent": parent,
                **_queue_failure(current),
                **_analysis_trace_context(current),
            }
        )
        for name, relation in (("__cause__", "cause"), ("__context__", "context")):
            child = getattr(current, name)
            if isinstance(child, BaseException) and id(child) not in seen:
                pending.append((child, relation, index))
    return {
        "errors": errors,
        "error_overflow": any(id(item[0]) not in seen for item in pending),
    }


def _closed_trace_context(error):
    """Only exact runtime code identities and fixed primitive vocabularies."""
    from types import CodeType

    from runtimes.rdagent_operational import daemon_identity, rpc

    observers = [
        (daemon_identity._observe.__code__, "unknown"),
        (daemon_identity._inspect.__code__, "container_state"),
        (daemon_identity._host_snapshot.__code__, "host_snapshot"),
        (daemon_identity._read.__code__, "host_read"),
        (daemon_identity.parse_top.__code__, "daemon_top"),
    ]
    current = [
        value
        for value in daemon_identity._observe.__code__.co_consts
        if isinstance(value, CodeType)
        and value.co_qualname == "_observe.<locals>.current"
    ]
    if len(current) == 1:
        observers.append((current[0], "deadline"))
    result = {}
    trace = error.__traceback__
    for _ in range(64):
        if trace is None:
            break
        frame = trace.tb_frame
        if frame.f_code is rpc.serve_one.__code__:
            request = frame.f_locals.get("request")
            method = request.get("method") if type(request) is dict else None
            result["rpc_method"] = (
                method if type(method) is str and method in rpc.METHODS else "unknown"
            )
        for code, category in observers:
            if frame.f_code is code:
                result["observer_failure"] = category
        trace = trace.tb_next
    return result


def _neutral_busy_cut(error, *, controller_limit=None):
    """Project the exact rejecting function and stopped-receipt boundary, never values."""
    from inspect import unwrap

    from backend.app import (
        rdagent_provider_broker,
        rdagent_workflow_coordination,
        research_lifecycle,
        research_object_services,
    )
    from backend.app.services import DomainError
    from packages.rdagent_provider.process_receipts import SDKStopped

    if type(error) is not DomainError or error.code != "neutral_busy":
        return {}
    trace = error.__traceback__
    origin = receipt = None
    for _ in range(64):
        if trace is None:
            break
        frame = trace.tb_frame
        code, values = frame.f_code, frame.f_locals
        if code is unwrap(rdagent_workflow_coordination._Gate.hold).__code__:
            remaining = values.get("remaining")
            if type(remaining) not in (int, float):
                origin = "gate_same_thread_or_closed"
            elif remaining <= 0:
                deadline = values.get("deadline")
                origin = "gate_expired"
                if controller_limit is not None and type(deadline) in (int, float):
                    if deadline == controller_limit:
                        origin = "gate_expired_controller"
                    elif deadline < controller_limit:
                        origin = "gate_expired_local"
            else:
                origin = "gate_closed_while_waiting"
        elif code is research_lifecycle._try_neutral_lock.__code__:
            scope = values.get("scope")
            if type(scope) is str:
                origin = (
                    "postgres_owner"
                    if scope.startswith("submit:")
                    else "postgres_resource"
                )
        if (
            code is rdagent_provider_broker._retain_receipt.__code__
            and type(values.get("document")) is SDKStopped
        ):
            receipt = "bind" if "object_id" in values else "reserve"
        elif (
            receipt == "reserve"
            and code is research_object_services.publish_reserved_object.__code__
        ):
            receipt = (
                "publication_finalize"
                if "journal_key" in values
                else "publication_prepare"
            )
        trace = trace.tb_next
    if trace is not None:
        return {}
    result = {}
    if origin is not None:
        result["neutral_busy_origin"] = origin
    if receipt is not None:
        result["receipt_boundary"] = receipt
    return result


def _controller_limit(error):
    from runtimes.rdagent_operational.rpc import serve_one

    trace = error.__traceback__
    limit = None
    for _ in range(64):
        if trace is None:
            return limit
        frame = trace.tb_frame
        if frame.f_code is serve_one.__code__:
            value = frame.f_locals.get("deadline")
            if (
                limit is not None
                or type(value) not in (int, float)
                or not math.isfinite(value)
            ):
                return None
            limit = value
        trace = trace.tb_next
    return None


def _sdk_outcome_cut(error):
    """Identify only a positively matched SDK wire/output exception boundary."""
    from packages.rdagent_provider import transport

    if (
        type(error) is not transport.ProviderFailure
        or error.code != "provider_outcome_unknown"
        or error.contained is not True
        or error.__cause__ is not None
    ):
        return {}
    # The transport intentionally suppresses private error context. In this
    # fixture, use only type/code-object identity; don't stringify the context.
    source = error.__context__
    if not isinstance(source, Exception):
        return {}
    parent_trace = error.__traceback__
    matching = None
    for _ in range(64):
        if parent_trace is None:
            break
        if parent_trace.tb_frame.f_code is transport.execute.__code__:
            if matching is not None:
                return {}
            matching = parent_trace.tb_frame
        parent_trace = parent_trace.tb_next
    if parent_trace is not None or matching is None:
        return {}
    trace = source.__traceback__
    wire = False
    anchored = False
    for _ in range(64):
        if trace is None:
            break
        anchored = anchored or trace.tb_frame is matching
        wire = wire or trace.tb_frame.f_code is transport._wire_exchange.__code__
        trace = trace.tb_next
    if trace is not None or not anchored:
        return {}
    if wire:
        cut = "wire_deadline" if type(source) is TimeoutError else "wire_error"
    elif type(matching.f_locals.get("output")) is bytes:
        cut = "output_rejected"
    else:
        return {}
    return {"sdk_outcome_cut": cut}


def _queue_failure(error):
    """Closed diagnostic vocabulary; never stringify arbitrary exceptions."""
    from pydantic import ValidationError
    from sqlalchemy.exc import DBAPIError, SQLAlchemyError

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
        ValidationError: "validation",
        AttributeError: "attribute",
        TypeError: "type",
        KeyError: "key",
        TimeoutError: "timeout",
        FileNotFoundError: "file_missing",
        PermissionError: "permission",
        OSError: "os",
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
        "provider_input_rejected",
        "provider_admission_unconfirmed",
        "provider_identity_receipt_unconfirmed",
        "provider_stop_receipt_unconfirmed",
        "attempt_fenced",
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
        "numeric_process_command_failed",
        "numeric_process_command_output",
        "numeric_process_uncertain",
        "analysis_process_authority",
        "analysis_process_deadline",
        "analysis_process_original_deadline",
        "analysis_process_runtime_identity",
        "analysis_process_kernel_identity",
        "analysis_process_identity_unobserved",
        "analysis_process_created_cleanup_uncertain",
        "analysis_process_journal_identity",
        "analysis_process_root_identity",
        "analysis_process_uncertain",
        "analysis_process_output_rejected",
        "analysis_process_claim",
        "analysis_process_retained_input_drift",
    }
    code = code if type(code) is str and code in allowed else "unknown"
    sqlstate = "none"
    if isinstance(error, SQLAlchemyError):
        family = "orm"
    if isinstance(error, DBAPIError):
        family = "database"
        value = getattr(error.orig, "sqlstate", None)
        sqlstate = (
            value
            if type(value) is str
            and value
            in {"23503", "23505", "23514", "40001", "40P01", "55P03", "57014", "P0001"}
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
        (
            "backend.app.rdagent_numeric_failure_finalization",
            "_closed_phase",
        ): "failure_phase",
        (
            "backend.app.rdagent_numeric_failure_finalization",
            "retain_numeric_failure_feedback",
        ): "failure_finalize",
        (
            "backend.app.rdagent_services",
            "_completed_member_response",
        ): "member_response",
        ("backend.app.rdagent_operational_broker", "_publish"): "object_publish",
        ("backend.app.research_object_services", "reserve_object"): "object_reserve",
        (
            "backend.app.research_object_services",
            "publish_reserved_object",
        ): "object_persist",
        (
            "packages.rdagent_adapter.numeric_failure",
            "upstream_observation",
        ): "failure_document",
        ("backend.app.research_lifecycle", "neutral_transaction"): "transaction",
    }
    stage, trace = "unknown", error.__traceback__
    for _ in range(64):
        if trace is None:
            break
        key = (trace.tb_frame.f_globals.get("__name__"), trace.tb_frame.f_code.co_name)
        stage = stages.get(key, stage)
        trace = trace.tb_next
    return {
        "family": family,
        "code": code,
        "stage": stage,
        "sqlstate": sqlstate,
        **_closed_trace_context(error),
        **_sdk_outcome_cut(error),
        **_neutral_busy_cut(error, controller_limit=_controller_limit(error)),
    }


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


def _masked_controller_failure(error, run_id):
    """Recover only the original error retained by the owned controller frame."""
    from packages.research_runtime.fences import AttemptFence
    from packages.research_runtime.process_contracts import ProcessError
    from workers.research.rdagent_operational import _execute_controller

    if (
        type(error) is not ProcessError
        or error.code != "rd_provider_containment_unknown"
    ):
        return {}
    trace = error.__traceback__
    original = None
    found = False
    for _ in range(64):
        if trace is None:
            break
        frame = trace.tb_frame
        if frame.f_code is _execute_controller.__code__:
            fence = frame.f_locals.get("fence")
            if found or type(fence) is not AttemptFence or fence.run_id != run_id:
                return {}
            found = True
            if "failure" not in frame.f_locals:
                return {"state": "unavailable"}
            original = frame.f_locals["failure"]
        trace = trace.tb_next
    if trace is not None or not found:
        return {}
    if original is None:
        return {"state": "no_prior_failure"}
    if not isinstance(original, BaseException) or original is error:
        return {"state": "unavailable"}
    return _masked_controller_summary(original)


def _masked_controller_summary(original):
    fields = (
        "family",
        "code",
        "stage",
        "sqlstate",
        "rpc_method",
        "analysis_stage",
        "docker_operation",
        "neutral_busy_origin",
        "receipt_boundary",
        "broker_boundary",
        "validation_boundary",
        "validation_field",
        "validation_type",
    )
    original_method = _closed_trace_context(original).get("rpc_method")

    def details(error, scope):
        row = {
            **_queue_failure(error),
            **_analysis_trace_context(error),
            **_neutral_busy_cut(error, controller_limit=_controller_limit(original)),
        }
        if row.get(
            "neutral_busy_origin"
        ) == "gate_expired_controller" and _closed_trace_context(original).get(
            "rpc_method"
        ) in {"submit_chat", "submit_embedding"}:
            from tests.integration.rd_broker_busy_cut import broker_busy_boundary

            boundary = broker_busy_boundary(error.__traceback__)
            if boundary is not None:
                row["broker_boundary"] = boundary
        if (
            original_method in {"submit_chat", "submit_embedding"}
            and row.get("family") == "validation"
        ):
            from tests.integration.rd_broker_validation_cut import broker_validation_cut

            row.update(broker_validation_cut(error))
        return {f"{scope}_{key}": row[key] for key in fields if key in row}

    projected = {"state": "observed", **details(original, "masked")}

    def uncertain(chain):
        for key in (
            "broker_boundary",
            "validation_boundary",
            "validation_field",
            "validation_type",
        ):
            projected.pop(f"masked_{key}", None)
        return {**projected, "chain": chain}

    # Traverse both explicit causes and suppressed contexts. Do not pass off a
    # traversal artifact as a root cause if there are multiple leaves/cycles.
    active, done, leaves = set(), set(), []
    pending = [(original, False)]
    while pending:
        current, exiting = pending.pop()
        identity = id(current)
        if exiting:
            active.remove(identity)
            done.add(identity)
            continue
        if identity in active:
            return uncertain("ambiguous")
        if identity in done:
            continue
        if len(active) + len(done) == 8:
            return uncertain("overflow")
        active.add(identity)
        children = [
            value
            for value in (current.__cause__, current.__context__)
            if isinstance(value, BaseException)
        ]
        if not children:
            leaves.append(current)
        pending.append((current, True))
        pending.extend((child, False) for child in children)
    if len(leaves) != 1:
        return uncertain("ambiguous")
    if leaves[0] is original:
        return {**projected, "chain": "single"}
    return {**projected, "chain": "unique_leaf", **details(leaves[0], "nested")}


@contextmanager
def diagnose_rd_queue(factory, run_id, *, project_masked=False):
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
                if project_masked is True:
                    try:
                        detail = _masked_controller_failure(error, run_id)
                        if detail:
                            diagnostic["masked_controller"] = detail
                    except BaseException:  # noqa: BLE001, S110 -- keep original error
                        pass
                try:
                    print(
                        "RD_QUEUE_FAILURE:" + json.dumps(diagnostic, sort_keys=True),
                        flush=True,
                    )
                except BaseException:  # noqa: BLE001, S110 -- retain the task failure
                    pass
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
        try:
            print("RD_QUEUE_STATE:" + json.dumps(state, sort_keys=True), flush=True)
        except BaseException:  # noqa: BLE001, S110 -- diagnostics cannot replace failure
            pass
