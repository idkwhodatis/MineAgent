"""Bounded test-only commit observations. No authority, SQL, or timing changes."""

import ast
import hashlib
import inspect
import json
import math
import sys
import textwrap
import threading
import time
from collections import deque
from contextlib import ExitStack, contextmanager
from unittest.mock import patch
from weakref import WeakKeyDictionary, ref

from sqlalchemy import event
from sqlalchemy.orm import Session


def _ms(value):
    if not math.isfinite(value):
        return 0
    return max(-3_600_000, min(3_600_000, round(value * 1000)))


def _sites():
    from backend.app import rdagent_operational_broker as objects
    from backend.app import rdagent_provider_broker as provider
    from backend.app import research_object_services as storage

    pins = {
        "_publish": "b5c397f8e82c5c7f892b90a4fd47c28b539d08288e6c897362b332ab4b36de5c",
        "publish_reserved_object": "76f3cc1624c3375c3d209f67035b9b60217407081680c65f965f71fcb834e7ef",
        "_retain_receipt": "da6025de669a18bc87b4ac99028bbb2d829a2c9be2b21a16d75e8c62463dfb5b",
        "submit_provider_request": "46bd9e54ba5601787836db68e633ae17b805443e89b86783c210124136b7b8ef",
    }
    result = {}
    for function, labels in (
        (objects._publish, ("publication_reserve",)),
        (
            storage.publish_reserved_object,
            ("publication_preflight", "publication_finalize"),
        ),
        (provider._retain_receipt, ("receipt_bind",)),
        (
            provider.submit_provider_request,
            (
                "provider_lookup",
                "provider_admit",
                "provider_settle",
                "provider_unknown_settle",
            ),
        ),
    ):
        lines, offset = inspect.getsourcelines(function)
        tree = ast.parse(textwrap.dedent("".join(lines)))
        if (
            hashlib.sha256(
                ast.dump(tree, include_attributes=False).encode()
            ).hexdigest()
            != pins[function.__name__]
        ):
            raise ValueError("Diagnostic source layout changed")
        nodes = []

        def visit(node, nodes=nodes):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                return
            if isinstance(node, ast.With) and any(
                isinstance(item.context_expr, ast.Call)
                and isinstance(item.context_expr.func, ast.Name)
                and item.context_expr.func.id == "neutral_transaction"
                for item in node.items
            ):
                nodes.append(node)
            for child in ast.iter_child_nodes(node):
                visit(child)

        for statement in tree.body[0].body:
            visit(statement)
        if len(nodes) != len(labels):
            raise ValueError("Diagnostic source layout changed")
        result[function.__code__] = tuple(
            (node.lineno + offset - 1, node.end_lineno + offset - 1, label)
            for node, label in zip(nodes, labels, strict=True)
        )
    return result


class Observer:
    def __init__(self, sites=(), clock=time.monotonic):
        self.local = threading.local()
        self.lock = threading.Lock()
        self.tail = deque(maxlen=128)
        self.sites = dict(sites)
        self.clock = clock
        self.count = 0
        self.dropped = False

    def site(self):
        frame = sys._getframe(1)
        try:
            for _ in range(64):
                if frame is None:
                    break
                for low, high, label in self.sites.get(frame.f_code, ()):
                    if low <= frame.f_lineno <= high:
                        return label
                frame = frame.f_back
            return "unclassified"
        finally:
            del frame

    @contextmanager
    def context(self, role, rpc=None):
        old = None
        try:
            old = getattr(self.local, "context", None)
            self.local.context = (role, rpc)
        except BaseException:  # noqa: BLE001
            self.dropped = True
        try:
            yield
        finally:
            try:
                self.local.context = old
            except BaseException:  # noqa: BLE001
                self.dropped = True

    def before(self, session):
        try:
            if session.get_nested_transaction() is not None:
                return
            root = session.get_transaction()
            if root is None:
                return
            if not hasattr(self.local, "pending"):
                self.local.pending = WeakKeyDictionary()
            self.local.pending.pop(session, None)
            if len(self.local.pending) >= 128:
                self.dropped = True
                return
            role, rpc = getattr(self.local, "context", None) or ("other", None)
            method, sequence, deadline = rpc or ("none", 0, None)
            self.local.pending[session] = (
                ref(root),
                self.clock(),
                role,
                method,
                sequence,
                deadline,
                self.site(),
            )
        except BaseException:  # noqa: BLE001 - observation cannot replace execution
            self.dropped = True

    def finish(self, session, ok):
        acquired = False
        try:
            if session.get_nested_transaction() is not None:
                return
            entry = getattr(self.local, "pending", {}).pop(session, None)
            if entry is None:
                return
            root, start, role, method, sequence, deadline, site = entry
            if root() is None or root() is not session.get_transaction():
                self.dropped = True
                return
            now = self.clock()
            acquired = self.lock.acquire(blocking=False)
            if not acquired:
                self.dropped = True
                return
            self.count = min(1_000_000, self.count + 1)
            self.tail.append(
                {
                    "role": role,
                    "method": method,
                    "sequence": sequence,
                    "site": site,
                    "elapsed_ms": max(0, _ms(now - start)),
                    "remaining_ms": None if deadline is None else _ms(deadline - now),
                    "ok": ok,
                }
            )
        except BaseException:  # noqa: BLE001
            self.dropped = True
        finally:
            if acquired:
                try:
                    self.lock.release()
                except BaseException:  # noqa: BLE001
                    self.dropped = True

    def report(self):
        try:
            if not self.lock.acquire(blocking=False):
                return
            try:
                report = {
                    "status": "best_effort_nonblocking",
                    "count": self.count,
                    "dropped": self.dropped,
                    "tail": list(self.tail),
                }
            finally:
                self.lock.release()
            print(
                "RD_COMMIT_ATTRIBUTION_V1:" + json.dumps(report, sort_keys=True),
                flush=True,
            )
        except BaseException:  # noqa: BLE001, S110
            pass


@contextmanager
def diagnose_commit_attribution():
    try:
        from runtimes.rdagent_operational import rpc
        from workers.research import rdagent_operational as worker
        from workers.research.lease import AttemptLease

        observer = Observer(_sites())
        original_serve, original_receive, original_pulse = (
            worker.serve_one,
            rpc.receive,
            AttemptLease.pulse,
        )
    except BaseException:  # noqa: BLE001 - optional diagnostic setup
        yield
        return

    def serve(channel, *, sequence, deadline, authenticate, handle):
        # Context is local to the serving thread, never inherited by heartbeat.
        observed_sequence = (
            sequence if type(sequence) is int and 0 <= sequence <= 1_000_000 else 0
        )
        with observer.context("rpc", ("unknown", observed_sequence, deadline)):
            return original_serve(
                channel,
                sequence=sequence,
                deadline=deadline,
                authenticate=authenticate,
                handle=handle,
            )

    def receive(*args, **kwargs):
        result = original_receive(*args, **kwargs)
        try:
            role, context = observer.local.context
            method = result.get("method") if type(result) is dict else None
            method = (
                method if type(method) is str and method in rpc.METHODS else "unknown"
            )
            observer.local.context = (role, (method, context[1], context[2]))
        except BaseException:  # noqa: BLE001, S110
            pass
        return result

    def pulse(*args, **kwargs):
        with observer.context("heartbeat"):
            return original_pulse(*args, **kwargs)

    def committed(session):
        observer.finish(session, True)

    def rolled_back(session):
        observer.finish(session, False)

    callbacks = [
        ("before_commit", observer.before),
        ("after_commit", committed),
        ("after_rollback", rolled_back),
    ]
    installed = []
    stack = ExitStack()

    def cleanup():
        try:
            stack.close()
        except BaseException:  # noqa: BLE001
            observer.dropped = True
        for name, callback in installed:
            try:
                event.remove(Session, name, callback)
            except BaseException:  # noqa: BLE001
                observer.dropped = True
        installed.clear()

    try:
        for name, callback in callbacks:
            event.listen(Session, name, callback)
            installed.append((name, callback))
        stack.enter_context(patch.object(worker, "serve_one", serve))
        stack.enter_context(patch.object(rpc, "receive", receive))
        stack.enter_context(patch.object(AttemptLease, "pulse", pulse))
    except BaseException:  # noqa: BLE001 - optional setup must not prevent trial
        cleanup()
        observer.dropped = True
    try:
        yield
    finally:
        cleanup()
        observer.report()
