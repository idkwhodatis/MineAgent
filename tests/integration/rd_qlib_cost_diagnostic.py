"""Closed Qlib RPC timings; never initiate SQL, I/O, or provider work.

DBAPI includes the server round trip, deferred checks and waits. These spans
overlap the existing commit/body totals. A null publication stage means neither
the reserve nor finalize call was observed; it is not proof of a read-only one.
"""

from contextlib import ExitStack, contextmanager
from functools import wraps
from threading import get_ident
from time import monotonic_ns
from unittest.mock import patch

from sqlalchemy.orm import Session

TARGETS = {
    5: ("factor_numeric", None),
    6: ("objects", "proposal"),
    8: ("objects", "proposal"),
    9: ("objects", "adoption"),
    11: ("objects", "adoption"),
    12: ("factor_numeric", None),
}
MAX_NS = 3_600_000_000_000
MAX_COUNT = 128
MAX_CHAT_TRANSACTIONS = 64
POLICIES = frozenset({"default_off", "explicit_opt_in"})
CHILDREN = frozenset({"first", "seeded"})
METHODS = frozenset({"generation_boundary", "submit_chat"})
CHAT_OBJECTS = frozenset(
    {"request", "response", "receipt_intent", "receipt_started", "receipt_stopped"}
)


class QlibCost:
    """One synchronous, fixture-owned Qlib RPC on its exact worker engine."""

    def __init__(self, engine, observe_transaction, *, method, policy, child, ordinal):
        if method not in METHODS or policy not in POLICIES or child not in CHILDREN:
            raise ValueError("closed Qlib diagnostic labels required")
        self.engine, self.observe_transaction = engine, observe_transaction
        self.method, self.policy, self.child = method, policy, child
        self.ordinal = min(1_000_000, max(0, ordinal))
        self.thread = get_ident()
        self.publication = None
        self.receipt = None
        self.committing = None
        self.samples = {}
        self.transactions = {}
        self.stages = {}
        self.overflow = False

    def target(self, row):
        if get_ident() != self.thread or row is None:
            return None
        ordinal = row["transaction_ordinal"]
        if row["ownership"] != "rpc_fenced_neutral":
            return None
        if self.method == "generation_boundary" and TARGETS.get(ordinal) == (
            row["resource_scope"],
            self.publication,
        ):
            return ordinal
        if (
            self.method == "submit_chat"
            and row["resource_scope"] == "objects"
            and row["unit"] == "provider_publication"
            and self.publication in CHAT_OBJECTS
        ):
            if 1 <= ordinal <= MAX_CHAT_TRANSACTIONS:
                return ordinal
            self.overflow = True
        return None

    def prepare_commit(self, session, row):
        if self.target(row) is not None and session.get_bind() is self.engine:
            self.committing = row

    def end(self, row):
        if self.committing is row:
            self.committing = None
        ordinal = self.target(row)
        if ordinal is not None:
            self.transactions[ordinal] = {
                "publication": self.publication,
                "publication_stage": self.stages.get(ordinal),
                **{
                    key: row[key]
                    for key in (
                        "resource_scope",
                        "transaction_ms",
                        "commit_elapsed_ms",
                        "commit_begin_remaining_ms",
                        "completion",
                    )
                },
            }

    def timed(self, component, locate, original, *args, **kwargs):
        ordinal = start = None
        try:
            ordinal = self.target(locate())
            if ordinal is not None:
                if component in ("reserve_object", "finalize_object"):
                    self.stages[ordinal] = component
                start = monotonic_ns()
        except BaseException:  # noqa: BLE001, S110 -- observer cannot change the call
            pass
        ok = False
        try:
            result = original(*args, **kwargs)
            ok = True
            return result
        finally:
            if ordinal is not None and start is not None:
                try:
                    elapsed = max(0, monotonic_ns() - start)
                    row = self.samples.setdefault((ordinal, component), [0, 0, 0, 0])
                    self.overflow |= row[0] >= MAX_COUNT or row[1] + elapsed > MAX_NS
                    row[0] = min(MAX_COUNT, row[0] + 1)
                    row[1] = min(MAX_NS, row[1] + elapsed)
                    row[2] = min(MAX_NS, max(row[2], elapsed))
                    row[3] = min(MAX_COUNT, row[3] + (not ok))
                except BaseException:  # noqa: BLE001, S110 -- retain original exception
                    pass

    def wrap_verifier(self, original, label):
        @wraps(original)
        def observed(*args, **kwargs):
            return self.timed(
                label,
                lambda: self.observe_transaction(
                    args[0] if args else kwargs["session"]
                ),
                original,
                *args,
                **kwargs,
            )

        return observed

    def wrap_flush(self, original):
        @wraps(original)
        def observed(session, *args, **kwargs):
            row = None
            try:
                candidate = self.observe_transaction(session)
                if candidate is not None and session.get_bind() is self.engine:
                    row = candidate
            except BaseException:  # noqa: BLE001, S110 -- preserve original flush
                pass
            label = "orm_flush" if row is self.committing else "body_orm_flush"
            return self.timed(label, lambda: row, original, session, *args, **kwargs)

        return observed

    def wrap_driver(self, original):
        @wraps(original)
        def observed(connection):
            return self.timed(
                "dbapi_commit", lambda: self.committing, original, connection
            )

        return observed

    def wrap_publication(self, original):
        @wraps(original)
        def observed(*args, **kwargs):
            if get_ident() != self.thread:
                return original(*args, **kwargs)
            kind = args[3] if len(args) > 3 else kwargs.get("kind")
            previous = self.publication
            self.publication = None
            if type(kind) is str:
                self.publication = {
                    "factor_generation_proposal": "proposal",
                    "factor_adoption_receipt": "adoption",
                    "model_request": "request",
                    "model_response": "response",
                    "rd_provider_process_receipt": self.receipt,
                }.get(kind)
            try:
                return original(*args, **kwargs)
            finally:
                self.publication = previous

        return observed

    def wrap_receipt(self, original):
        from packages.rdagent_provider.process_receipts import (
            SDKIntent,
            SDKStarted,
            SDKStopped,
        )

        @wraps(original)
        def observed(*args, **kwargs):
            if get_ident() != self.thread:
                return original(*args, **kwargs)
            document = args[3] if len(args) > 3 else kwargs.get("document")
            previous = self.receipt
            self.receipt = {
                SDKIntent: "receipt_intent",
                SDKStarted: "receipt_started",
                SDKStopped: "receipt_stopped",
            }.get(type(document))
            try:
                return original(*args, **kwargs)
            finally:
                self.receipt = previous

        return observed

    def report(self):
        return {
            "method": self.method,
            "policy": self.policy,
            "child": self.child,
            "rpc_ordinal": self.ordinal,
            "inclusive_overlapping": True,
            "overflow": self.overflow,
            "transactions": [
                {
                    "ordinal": ordinal,
                    **values,
                    "components": [
                        {
                            "component": component,
                            "count": row[0],
                            "total_ms": round(row[1] / 1e6, 3),
                            "max_ms": round(row[2] / 1e6, 3),
                            "failures": row[3],
                        }
                        for (index, component), row in sorted(self.samples.items())
                        if index == ordinal
                    ],
                }
                for ordinal, values in sorted(self.transactions.items())
            ],
        }

    @contextmanager
    def installed(self):
        from backend.app import rdagent_analysis_finalization as analysis
        from backend.app import rdagent_operational_broker as fixture_broker
        from backend.app import rdagent_operational_finalization as finalization
        from backend.app import rdagent_provider_broker as provider_broker
        from backend.app import research_object_services as objects

        dialect = self.engine.dialect
        with ExitStack() as stack:
            try:
                generic = (
                    (dialect, "do_commit", self.wrap_driver(dialect.do_commit)),
                    (Session, "flush", self.wrap_flush(Session.flush)),
                )
                generation = (
                    (
                        finalization,
                        "_publish",
                        self.wrap_publication(finalization._publish),
                    ),
                    *[
                        (owner, name, self.wrap_verifier(getattr(owner, name), label))
                        for owner, name, label in (
                            (analysis, "_snapshot", "verified_analysis"),
                            (finalization, "_published_feedback", "verified_feedback"),
                            (finalization, "read_research_object", "verified_artifact"),
                            (
                                finalization,
                                "record_generation_observation",
                                "generation_observe",
                            ),
                            (
                                finalization,
                                "confirm_generation_boundary",
                                "generation_confirm",
                            ),
                            (finalization, "settle_budget", "mutation_settle"),
                        )
                    ],
                )
                chat = (
                    (
                        provider_broker,
                        "_publish",
                        self.wrap_publication(provider_broker._publish),
                    ),
                    (
                        provider_broker,
                        "_retain_receipt",
                        self.wrap_receipt(provider_broker._retain_receipt),
                    ),
                    (
                        fixture_broker,
                        "reserve_object",
                        self.wrap_verifier(
                            fixture_broker.reserve_object, "reserve_object"
                        ),
                    ),
                    (
                        objects,
                        "finalize_object",
                        self.wrap_verifier(objects.finalize_object, "finalize_object"),
                    ),
                )
                selected = generation if self.method == "generation_boundary" else chat
                for owner, name, replacement in generic + selected:
                    stack.enter_context(patch.object(owner, name, replacement))
            except Exception:  # noqa: BLE001 -- partial observation is not evidence
                stack.close()
            yield
