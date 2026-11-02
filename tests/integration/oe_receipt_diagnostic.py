"""Bounded test-only receipt costs. No queries, flushes or output in the deadline.

Entry includes lock/fence admission, body includes caller queries and flushes,
exit includes deferred commit/rollback and cleanup. Reads may overlap body;
publications occur between transactions. Receipt totals enclose both. Never
subtract nested helpers or indiscriminately sum overlapping spans.
Only the receipt kind is inspected; no IDs, arguments, SQL or errors are retained.
"""

import time
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from threading import Lock
from unittest.mock import patch

PHASE = ContextVar("oe_receipt_phase", default=None)
PHASES = frozenset({"intent", "started", "stopped"})
LIMIT = 128
TRANSACTIONS = (
    ("backend.app.oe_structured_provider_publication", "neutral_transaction"),
    ("backend.app.research_object_services", "neutral_transaction"),
)
FILES = (
    ("backend.app.research_object_services", "publish_bytes"),
    ("backend.app.research_object_services", "_read_exact"),
)
RECEIPT = ("backend.app.oe_structured_provider_broker", "retain_physical_receipt")


def now():
    try:
        return time.monotonic_ns()
    except Exception:  # noqa: BLE001 -- observation must not replace task failure
        return None


def elapsed(start, end):
    if type(start) is not int or type(end) is not int or end < start:
        return None
    return min(600000000, (end - start) // 1000)


class Recorder:
    def __init__(self):
        self.records = []
        self.overflow = False
        self.lock = Lock()

    def append(self, record):
        with self.lock:
            if len(self.records) < LIMIT:
                self.records.append(record)
            else:
                self.overflow = True

    def observe(self, record):
        try:
            self.append(record)
        except Exception:  # noqa: BLE001, S110 -- preserve original result or error
            pass

    def receipt(self, original):
        def wrapped(*args, **kwargs):
            # The fixed production signature puts the typed receipt at index four.
            document = args[4] if len(args) > 4 else kwargs.get("document")
            try:
                kind = document.kind
                phase = kind if type(kind) is str and kind in PHASES else "unknown"
            except Exception:  # noqa: BLE001 -- never inspect exception contents
                phase = "unknown"
            token = PHASE.set(phase)
            start = now()
            try:
                return original(*args, **kwargs)
            finally:
                try:
                    self.observe(
                        {
                            "phase": phase,
                            "operation": "receipt",
                            "total_us": elapsed(start, now()),
                        }
                    )
                finally:
                    PHASE.reset(token)

        return wrapped

    def transaction(self, original, selector):
        @contextmanager
        def wrapped(*args, **kwargs):
            phase = PHASE.get()
            if phase is None:
                with original(*args, **kwargs) as value:
                    yield value
                return
            start = now()
            entered = body_end = end = None
            try:
                with original(*args, **kwargs) as value:
                    entered = now()
                    try:
                        yield value
                    finally:
                        body_end = now()
            finally:
                end = now()
                self.observe(
                    {
                        "phase": phase,
                        "operation": selector,
                        "entry_us": elapsed(
                            start, entered if entered is not None else end
                        ),
                        "body_us": elapsed(entered, body_end),
                        "exit_us": elapsed(body_end, end),
                        "entered": entered is not None,
                    }
                )

        return wrapped

    def file(self, original, selector):
        def wrapped(*args, **kwargs):
            phase = PHASE.get()
            if phase is None:
                return original(*args, **kwargs)
            start = now()
            try:
                return original(*args, **kwargs)
            finally:
                self.observe(
                    {
                        "phase": phase,
                        "operation": selector,
                        "total_us": elapsed(start, now()),
                    }
                )

        return wrapped


@contextmanager
def diagnose_receipts(emit):
    from importlib import import_module

    recorder = Recorder()
    try:
        with ExitStack() as stack:
            module = import_module(RECEIPT[0])
            stack.enter_context(
                patch.object(
                    module, RECEIPT[1], recorder.receipt(getattr(module, RECEIPT[1]))
                )
            )
            for index, (name, attribute) in enumerate(TRANSACTIONS):
                module = import_module(name)
                stack.enter_context(
                    patch.object(
                        module,
                        attribute,
                        recorder.transaction(
                            getattr(module, attribute),
                            ("reserve_transaction", "publication_transaction")[index],
                        ),
                    )
                )
            for name, attribute in FILES:
                module = import_module(name)
                stack.enter_context(
                    patch.object(
                        module,
                        attribute,
                        recorder.file(
                            getattr(module, attribute),
                            {
                                "publish_bytes": "file_publish",
                                "_read_exact": "file_read",
                            }[attribute],
                        ),
                    )
                )
            yield
    finally:
        try:
            emit(
                "OE_RECEIPT_COST_V1:",
                {
                    "records": recorder.records,
                    "overflow": recorder.overflow,
                    "units": "microseconds",
                    "file_times_may_overlap_body": True,
                },
            )
        except Exception:  # noqa: BLE001, S110 -- preserve worker failure
            pass
