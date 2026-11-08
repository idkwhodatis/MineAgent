"""Test-only closed aggregate timings around unchanged OE calls and DB events.

No queries, daemon/file reads, retries, payload logging, or deadline changes.
Nested inclusive durations overlap and must not be summed. Separate commit
segments expose reservation, pre-publication checks, finalization, and binding.
"""

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from functools import wraps
from time import monotonic_ns
from unittest.mock import patch
from weakref import WeakKeyDictionary

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

PHASES = frozenset(
    {
        "intent_receipt",
        "started_receipt",
        "stopped_receipt",
        "other_receipt",
        "pre_sdk_operation",
        "sdk",
        "started_boundary",
        "stopped_boundary",
        "other_boundary",
    }
)
SEGMENTS = frozenset(
    {
        "reservation",
        "publication_preflight",
        "finalization",
        "binding",
        "operation",
        "other",
    }
)
COMPONENTS = frozenset(
    {
        "retain_receipt",
        "provider_operation",
        "sdk",
        "boundary",
        "publish_evidence",
        "reserve_budget",
        "reserve_object",
        "publish_reserved_object",
        "bind_physical_receipt",
        "finalize_object",
        "publish_bytes",
        "parent_candidate",
        "selection",
        "population_engine_outcome",
        "controller_context",
        "peer_revalidate",
        "daemon_observe",
        "daemon_batch_observe",
        "daemon_inspect",
        "host_snapshot",
        "sql_execute",
    }
) | {"commit_" + name for name in SEGMENTS}
MAX_COUNT = 10000
MAX_NS = 3_600_000_000_000


class Recorder:
    def __init__(self):
        self.phase = ContextVar("oe_cost_phase", default=None)
        self.segment = ContextVar("oe_cost_segment", default="other")
        self.totals = {}
        self.commits = WeakKeyDictionary()
        self.cursors = WeakKeyDictionary()
        self.overflow = False

    def now(self):
        try:
            value = monotonic_ns()
            return value if type(value) is int and value >= 0 else None
        except Exception:  # noqa: BLE001 -- timing cannot prevent a call
            return None

    def record(self, component, start, ok, phase):
        try:
            end = self.now()
            if (
                phase not in PHASES
                or component not in COMPONENTS
                or start is None
                or end is None
            ):
                return
            elapsed = max(0, end - start)
            row = self.totals.setdefault((phase, component), [0, 0, 0, 0])
            self.overflow |= row[0] >= MAX_COUNT or row[1] + elapsed > MAX_NS
            row[0] = min(MAX_COUNT, row[0] + 1)
            row[1] = min(MAX_NS, row[1] + elapsed)
            row[2] = min(MAX_NS, max(row[2], elapsed))
            row[3] = min(MAX_COUNT, row[3] + (not ok))
        except Exception:  # noqa: BLE001, S110 -- preserve result/exception
            pass

    def wrap(self, original, component, *, selector=None, segment=None):
        @wraps(original)
        def call(*args, **kwargs):
            phase_token = segment_token = None
            if selector is not None:
                try:
                    selected = selector(args, kwargs)
                except Exception:  # noqa: BLE001 -- never inspect error text
                    selected = "other_receipt"
                phase_token = self.phase.set(
                    selected
                    if type(selected) is str and selected in PHASES
                    else "other_receipt"
                )
                segment_token = self.segment.set("other")
            if segment is not None:
                # Sticky until enclosing phase ends, including the commit after
                # finalize_object/bind_physical_receipt return to their caller.
                self.segment.set(segment)
            phase, start, ok = self.phase.get(), self.now(), False
            try:
                result = original(*args, **kwargs)
                ok = True
                return result
            finally:
                self.record(component, start, ok, phase)
                if segment_token is not None:
                    self.segment.reset(segment_token)
                if phase_token is not None:
                    self.phase.reset(phase_token)

        return call

    def before_commit(self, session):
        try:
            if self.phase.get() in PHASES:
                if len(self.commits) >= MAX_COUNT:
                    self.overflow = True
                else:
                    self.commits[session] = (
                        self.now(),
                        self.phase.get(),
                        self.segment.get(),
                    )
        except Exception:  # noqa: BLE001, S110
            pass

    def finish_commit(self, session, ok):
        try:
            started = self.commits.pop(session, None)
            if started is not None:
                self.record("commit_" + started[2], started[0], ok, started[1])
        except Exception:  # noqa: BLE001, S110
            pass

    def after_commit(self, session):
        self.finish_commit(session, True)

    def after_rollback(self, session):
        self.finish_commit(session, False)

    def before_cursor(
        self, connection, cursor, statement, parameters, context, executemany
    ):
        try:
            if self.phase.get() in PHASES:
                if len(self.cursors) >= MAX_COUNT:
                    self.overflow = True
                else:
                    self.cursors[context] = (self.now(), self.phase.get())
        except Exception:  # noqa: BLE001, S110
            pass

    def finish_cursor(self, context, ok):
        try:
            started = self.cursors.pop(context, None)
            if started is not None:
                self.record("sql_execute", started[0], ok, started[1])
        except Exception:  # noqa: BLE001, S110
            pass

    def after_cursor(
        self, connection, cursor, statement, parameters, context, executemany
    ):
        self.finish_cursor(context, True)

    def handle_error(self, context):
        try:
            self.finish_cursor(context.execution_context, False)
        except Exception:  # noqa: BLE001, S110 -- preserve the database exception
            pass

    def report(self):
        return {
            "inclusive_overlapping": True,
            "overflow": self.overflow,
            "summary": [
                {
                    "phase": phase,
                    "component": component,
                    "count": row[0],
                    "inclusive_total_ms": round(row[1] / 1e6, 3),
                    "max_ms": round(row[2] / 1e6, 3),
                    "failures": row[3],
                }
                for (phase, component), row in sorted(self.totals.items())
            ],
        }


def _receipt_phase(args, kwargs):
    document = args[4] if len(args) > 4 else kwargs.get("document")
    kind = document.kind
    return (
        kind + "_receipt"
        if type(kind) is str and kind in {"intent", "started", "stopped"}
        else "other_receipt"
    )


@contextmanager
def diagnose_receipt_cost(emit):
    from backend.app import oe_structured_candidate_services as candidate
    from backend.app import oe_structured_controller_context as controller
    from backend.app import oe_structured_engine_results as results
    from backend.app import oe_structured_population as population
    from backend.app import oe_structured_provider_broker as broker
    from backend.app import oe_structured_provider_operation as operation
    from backend.app import oe_structured_provider_publication as publication
    from backend.app import research_object_services as objects
    from runtimes.openevolve_operational import daemon_identity as daemon
    from runtimes.openevolve_structured_host import peer

    r = Recorder()
    targets = [
        (broker, "retain_physical_receipt", "retain_receipt", _receipt_phase, None),
        (
            broker,
            "require_provider_operation",
            "provider_operation",
            lambda a, k: r.phase.get() or "pre_sdk_operation",
            "operation",
        ),
        (publication, "publish_evidence", "publish_evidence", None, "reservation"),
        (
            publication,
            "publish_reserved_object",
            "publish_reserved_object",
            None,
            "publication_preflight",
        ),
        (
            publication,
            "bind_physical_receipt",
            "bind_physical_receipt",
            None,
            "binding",
        ),
        *[
            (publication, name, name, None, None)
            for name in ("reserve_budget", "reserve_object")
        ],
        (objects, "finalize_object", "finalize_object", None, "finalization"),
        (objects, "publish_bytes", "publish_bytes", None, None),
        (operation, "require_parent_candidate", "parent_candidate", None, None),
        (population, "require_selection", "selection", None, None),
        (
            population,
            "read_population_engine_outcome",
            "population_engine_outcome",
            None,
            None,
        ),
        *[
            (m, "require_controller_context", "controller_context", None, None)
            for m in (candidate, controller, results)
            if hasattr(m, "require_controller_context")
        ],
        (peer, "_revalidate", "peer_revalidate", None, None),
        (daemon, "_observe", "daemon_observe", None, None),
        (daemon, "_inspect", "daemon_inspect", None, None),
        (daemon, "_host_snapshot", "host_snapshot", None, None),
    ]
    if hasattr(daemon, "_observe_members"):
        targets.append((daemon, "_observe_members", "daemon_batch_observe", None, None))
    original_complete = broker.complete_chat

    def complete(*args, **kwargs):
        boundary = kwargs.get("identity_boundary")
        if boundary is not None:

            def selected(a, k):
                name = a[0] if a else k.get("event")
                return (
                    name + "_boundary"
                    if type(name) is str and name in {"started", "stopped"}
                    else "other_boundary"
                )

            kwargs = {
                **kwargs,
                "identity_boundary": r.wrap(boundary, "boundary", selector=selected),
            }
        return original_complete(*args, **kwargs)

    listeners = (
        (Session, "before_commit", r.before_commit),
        (Session, "after_commit", r.after_commit),
        (Session, "after_rollback", r.after_rollback),
        (Engine, "before_cursor_execute", r.before_cursor),
        (Engine, "after_cursor_execute", r.after_cursor),
        (Engine, "handle_error", r.handle_error),
    )
    with ExitStack() as stack:
        for module, name, component, selector, segment in targets:
            stack.enter_context(
                patch.object(
                    module,
                    name,
                    r.wrap(
                        getattr(module, name),
                        component,
                        selector=selector,
                        segment=segment,
                    ),
                )
            )
        stack.enter_context(
            patch.object(
                broker,
                "complete_chat",
                r.wrap(complete, "sdk", selector=lambda a, k: "sdk"),
            )
        )
        for owner, name, callback in listeners:
            event.listen(owner, name, callback)
            stack.callback(event.remove, owner, name, callback)
        try:
            yield
        finally:
            try:
                emit("OE_RECEIPT_COST_V1:", r.report())
            except Exception:  # noqa: BLE001, S110 -- output never masks work
                pass
