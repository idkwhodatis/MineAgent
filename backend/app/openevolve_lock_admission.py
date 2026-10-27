"""Explicit initial-OE lock acquisition window, not execution/retry authority."""

import math
import threading
import time
from contextlib import contextmanager
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
