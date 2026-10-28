"""Explicit initial-OE lock acquisition window, not execution/retry authority."""

import math
import threading
import time
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass

from packages.research_runtime.fences import AttemptFence


@dataclass(frozen=True)
class _Window:
    fence: AttemptFence
    deadline: float
    thread: int


_window = ContextVar("initial_oe_lock_window", default=None)


def _error():
    from backend.app.services import DomainError

    return DomainError(
        "neutral_acquisition_invalid", "Lock acquisition scope unavailable", 409
    )


def current_window(fence):
    value = _window.get()
    if value is not None and (
        type(fence) is not AttemptFence
        or value.fence != fence
        or value.thread != threading.get_ident()
    ):
        raise _error()
    return value


@contextmanager
def acquisition_window(fence, deadline):
    if (
        type(fence) is not AttemptFence
        or type(deadline) not in (int, float)
        or not math.isfinite(deadline)
    ):
        raise _error()
    previous = current_window(fence)
    if previous is not None:
        deadline = min(deadline, previous.deadline)
    token = _window.set(_Window(fence, float(deadline), threading.get_ident()))
    try:
        yield
    finally:
        _window.reset(token)


def acquisition_deadline(fence):
    window = current_window(fence)
    return None if window is None else min(window.deadline, time.monotonic() + 5.0)


def retry_acquisition(acquire, fence, busy_type):
    """Retry a caller-proven acquisition only, after its resources have unwound."""
    deadline = acquisition_deadline(fence)
    while True:
        current_window(fence)
        if deadline is not None and time.monotonic() >= deadline:
            raise _error()
        try:
            # Nested DB admission must share this fixed window rather than
            # opening a fresh five seconds on each filesystem retry.
            with (
                acquisition_window(fence, deadline)
                if deadline is not None
                else nullcontext()
            ):
                result = acquire()
            if deadline is not None and time.monotonic() >= deadline:
                raise _error()
            return result
        except busy_type:
            if deadline is None:
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(0.05, remaining))


@contextmanager
def publication_storage_admission(root, fence):
    """Wait only before entering the FS guard, with no DB transaction held."""
    from contextlib import ExitStack

    from backend.app.research_object_services import object_storage_guard
    from backend.app.services import DomainError

    class Busy(Exception):
        pass

    stack = ExitStack()

    def acquire():
        try:
            stack.enter_context(object_storage_guard(root))
        except DomainError as exc:
            stack.close()
            if exc.code != "neutral_object_busy":
                raise
            raise Busy from exc

    try:
        retry_acquisition(acquire, fence, Busy)
        yield
    except Busy as exc:
        raise exc.__cause__ from None
    finally:
        stack.close()
