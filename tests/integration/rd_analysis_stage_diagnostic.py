"""Bounded test-only analysis-unit wall timings, never execution authority.

Intervals overlap (transaction/callback/Docker); do not add them as exclusive CPU
cost. The original eight-second intent and authority deadlines are only read.
No observer clock or limit is passed to any runtime function. *_transaction
samples include the entire original context-manager exit/commit; commit samples
span SQLAlchemy before_commit to after_commit (including flush), not SQL CPU.
Remaining time references the original eight-second unit, even where existing
EOF/containment code independently allows its unchanged one-second grace.
"""

import json
import math
import threading
from collections import deque
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from functools import wraps
from time import monotonic
from unittest.mock import patch
from weakref import WeakKeyDictionary, ref

STAGES = frozenset(
    {
        "unit",
        "preflight_transaction",
        "intent_transaction",
        "current_transaction",
        "identity_transaction",
        "stopped_transaction",
        "transaction_unknown",
        "persist_intent",
        "claim_launch",
        "current_check",
        "identity_callback",
        "stopped_callback",
        "containment_callback",
        "mint_authority",
        "execute",
        "inventory",
        "verify_staged",
        "verify_runtime",
        "observe_identity",
        "stop",
        "cleanup_unobserved",
        "read_frame",
        "live_frame",
        "complete_stream",
        "attach",
        "reader_close",
        "journal_write",
        "docker_inspect",
        "docker_image_inspect",
        "docker_container_inspect",
        "docker_create",
        "docker_start",
        "docker_top",
        "docker_remove_force",
        "docker_unknown",
        "commit",
        "containment_commit",
    }
)


def _safe(callback, *args):
    try:
        return callback(*args)
    except BaseException:  # noqa: BLE001 - optional observation never owns outcome
        return None


def _ms(value):
    return (
        max(-3_600_000, min(3_600_000, round(value * 1000)))
        if math.isfinite(value)
        else 0
    )


class Observer:
    def __init__(self):
        self.local = threading.local()
        self.lock = threading.Lock()
        self.units = 0

    def state(self):
        return getattr(self.local, "state", None)

    def begin(self):
        if self.state() is not None or not self.lock.acquire(blocking=False):
            return None
        try:
            if self.units >= 8:
                return None
            self.units += 1
        finally:
            self.lock.release()
        state = {
            "tail": deque(maxlen=128),
            "count": 0,
            "deadline": None,
            "deadline_source": "unavailable",
            "transaction": None,
            "containment": False,
            "stop_phase": "not_stopping",
            "client_deadline": None,
            "commits": WeakKeyDictionary(),
        }
        self.local.state = state
        return state

    def record(self, stage, start, ok):
        state = self.state()
        if state is None or stage not in STAGES or type(start) not in (float, int):
            return
        now = monotonic()
        state["count"] = min(1_000_000, state["count"] + 1)
        state["tail"].append(
            {
                "stage": stage,
                "elapsed_ms": max(0, _ms(now - start)),
                "unit_remaining_ms": None
                if state["deadline"] is None
                else _ms(state["deadline"] - now),
                "deadline_source": state["deadline_source"],
                "stop_phase": state["stop_phase"],
                "client_remaining_ms": None
                if state["client_deadline"] is None
                else _ms(state["client_deadline"] - now),
                "ok": bool(ok),
            }
        )

    def phase(self, stage):
        state = self.state()
        if state is not None and state["transaction"] is not None:
            state["transaction"]["stage"] = stage

    def intent(self, intent):
        from packages.research_runtime.analysis_process_journal import AnalysisIntent

        state = self.state()
        if state is not None and type(intent) is AnalysisIntent:
            state["deadline"] = (
                monotonic() + (intent.not_after - datetime.now(UTC)).total_seconds()
            )
            state["deadline_source"] = "intent_wall_projection"

    def authority(self, authority):
        from runtimes.research_supervisor.analysis_supervisor import AnalysisAuthority

        state = self.state()
        if state is not None and type(authority) is AnalysisAuthority:
            state["deadline"] = authority.deadline
            state["deadline_source"] = "original_authority_monotonic"

    def invoke(self, stage, original, *args, **kwargs):
        start, ok = _safe(monotonic), False
        try:
            value = original(*args, **kwargs)
            ok = True
            return value
        finally:
            _safe(self.record, stage, start, ok)

    def wrapped(self, stage, original, *, phase=None, intent=False, authority=False):
        @wraps(original)
        def call(*args, **kwargs):
            if phase is not None:
                _safe(self.phase, phase)
            if intent:
                _safe(
                    lambda: self.intent(args[2] if len(args) > 2 else kwargs["intent"])
                )
            if stage == "containment_callback":
                _safe(lambda: self.state().__setitem__("containment", True))
            value = self.invoke(stage, original, *args, **kwargs)
            if authority:
                _safe(self.authority, value)
            return value

        return call

    def root(self, original):
        @wraps(original)
        def call(*args, **kwargs):
            state = _safe(self.begin)
            if state is None:
                return original(*args, **kwargs)
            try:
                return self.invoke("unit", original, *args, **kwargs)
            finally:
                _safe(self.finish, state)
                # TLS belongs only to this wrapper, never a heartbeat thread.
                self.local.state = None

        return call

    def finish(self, state):
        print(
            "RD_ANALYSIS_STAGE_TIMING_V1:"
            + json.dumps(
                {
                    "scope": "original_analysis_unit_not_controller_budget",
                    "samples": state["count"],
                    "tail": list(state["tail"]),
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            flush=True,
        )

    def stopping(self, original):
        @wraps(original)
        def call(*args, **kwargs):
            state = self.state()
            if state is None:
                return original(*args, **kwargs)
            previous = state["stop_phase"]
            state["stop_phase"] = (
                "original_deadline_stop"
                if kwargs.get("original_deadline") is not None
                else "fresh_budget_containment"
            )
            try:
                return self.invoke("stop", original, *args, **kwargs)
            finally:
                state["stop_phase"] = previous

        return call

    def client_call(self, stage, original, client, *args, **kwargs):
        state = self.state()
        if state is None:
            return original(client, *args, **kwargs)
        previous = state["client_deadline"]
        cutoff = _safe(lambda: client.deadline)
        state["client_deadline"] = (
            cutoff if type(cutoff) in (int, float) and math.isfinite(cutoff) else None
        )
        try:
            return self.invoke(stage, original, client, *args, **kwargs)
        finally:
            state["client_deadline"] = previous

    def transaction(self, original):
        @wraps(original)
        @contextmanager
        def call(*args, **kwargs):
            state = self.state()
            if state is None:
                with original(*args, **kwargs) as session:
                    yield session
                return
            previous = state["transaction"]
            # Until an exact original callback identifies this scope, do not
            # infer a phase from timing or from whether an intent exists yet.
            frame = {"stage": "transaction_unknown"}
            state["transaction"] = frame
            start, ok = _safe(monotonic), False
            try:
                with original(*args, **kwargs) as session:
                    yield session
                ok = True
            finally:
                _safe(self.record, frame["stage"], start, ok)
                state["transaction"] = previous

        return call

    def before_commit(self, session):
        state = self.state()
        if (
            state is None
            or session.get_nested_transaction() is not None
            or len(state["commits"]) >= 128
        ):
            return
        root = session.get_transaction()
        if root is not None:
            state["commits"][session] = (
                ref(root),
                monotonic(),
                "containment_commit" if state["containment"] else "commit",
            )

    def end_commit(self, session, ok):
        state = self.state()
        if state is None or session.get_nested_transaction() is not None:
            return
        item = state["commits"].pop(session, None)
        if item is not None and item[0]() is session.get_transaction():
            self.record(item[2], item[1], ok)


def _install(stack, observer):
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    from runtimes.factor_execution.attach_stream import AttachStream
    from runtimes.research_supervisor import analysis_supervisor as supervisor
    from runtimes.research_supervisor.numeric_supervisor import NumericDockerClient
    from workers.research import analysis_process as worker

    def install(target, name, replacement):
        stack.enter_context(patch.object(target, name, replacement))

    install(
        worker,
        "execute_analysis_acceptance_unit",
        observer.root(worker.execute_analysis_acceptance_unit),
    )
    install(
        worker, "neutral_transaction", observer.transaction(worker.neutral_transaction)
    )
    for name, stage, phase in (
        ("persist_analysis_intent", "persist_intent", "intent_transaction"),
        ("claim_analysis_launch", "claim_launch", "intent_transaction"),
        ("check_analysis_claim", "current_check", "current_transaction"),
        ("record_analysis_identity", "identity_callback", "identity_transaction"),
        ("record_analysis_stopped", "stopped_callback", "stopped_transaction"),
        ("record_analysis_containment", "containment_callback", None),
    ):
        install(
            worker,
            name,
            observer.wrapped(
                stage,
                getattr(worker, name),
                phase=phase,
                intent=name == "persist_analysis_intent",
            ),
        )
    install(
        worker,
        "_mint_analysis_authority",
        observer.wrapped(
            "mint_authority", worker._mint_analysis_authority, authority=True
        ),
    )
    for name, stage in (
        ("execute", "execute"),
        ("verified_inventory", "inventory"),
        ("_verify_staged_launch", "verify_staged"),
        ("_verify", "verify_runtime"),
        ("_observe_identity", "observe_identity"),
        ("_cleanup_unobserved", "cleanup_unobserved"),
    ):
        install(
            supervisor.AnalysisSupervisor,
            name,
            observer.wrapped(stage, getattr(supervisor.AnalysisSupervisor, name)),
        )
    install(
        supervisor.AnalysisSupervisor,
        "stop_and_verify",
        observer.stopping(supervisor.AnalysisSupervisor.stop_and_verify),
    )
    for name, stage in (
        ("read_analysis_frame", "read_frame"),
        ("require_live_analysis_frame", "live_frame"),
        ("require_complete_analysis_stream", "complete_stream"),
        ("write_record", "journal_write"),
    ):
        install(supervisor, name, observer.wrapped(stage, getattr(supervisor, name)))
    # Preserve descriptors; the original connect is a classmethod.
    descriptor = AttachStream.__dict__["connect"]
    if not isinstance(descriptor, classmethod):
        raise TypeError("Expected attach classmethod")
    install(
        AttachStream,
        "connect",
        classmethod(observer.wrapped("attach", descriptor.__func__)),
    )
    install(AttachStream, "close", observer.wrapped("reader_close", AttachStream.close))
    original_inspect = NumericDockerClient._inspect

    @wraps(original_inspect)
    def inspect_client(self, *args, **kwargs):
        return observer.client_call(
            "docker_inspect", original_inspect, self, *args, **kwargs
        )

    install(NumericDockerClient, "_inspect", inspect_client)
    original_command = NumericDockerClient._command

    @wraps(original_command)
    def command(self, args, **kwargs):
        stage = "docker_unknown"

        def classify():
            if type(args) not in (list, tuple):
                return "docker_unknown"
            for prefix, label in (
                (("image", "inspect"), "docker_image_inspect"),
                (("container", "inspect"), "docker_container_inspect"),
                (("create",), "docker_create"),
                (("start",), "docker_start"),
                (("top",), "docker_top"),
                (("rm", "--force"), "docker_remove_force"),
            ):
                if (
                    all(type(value) is str for value in args[: len(prefix)])
                    and tuple(args[: len(prefix)]) == prefix
                ):
                    return label
            return "docker_unknown"

        stage = _safe(classify) or stage
        return observer.client_call(stage, original_command, self, args, **kwargs)

    install(NumericDockerClient, "_command", command)
    for name, callback in (
        ("before_commit", lambda session: _safe(observer.before_commit, session)),
        ("after_commit", lambda session: _safe(observer.end_commit, session, True)),
        ("after_rollback", lambda session: _safe(observer.end_commit, session, False)),
    ):
        event.listen(Session, name, callback)
        stack.callback(event.remove, Session, name, callback)


@contextmanager
def diagnose_analysis_stages():
    stack = ExitStack()
    try:
        _install(stack, Observer())
    except BaseException:  # noqa: BLE001 - partial optional setup restores patches
        _safe(stack.close)
    try:
        yield
    finally:
        _safe(stack.close)
