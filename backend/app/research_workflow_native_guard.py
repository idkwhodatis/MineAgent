"""Reuse the actual publisher's retained namespace fence without reentrant flock."""

import os
from contextlib import contextmanager

from backend.app.factor_native_io import _NATIVE_GUARD, require_native_storage_guard
from backend.app.research_input_storage import require_publication_admission


@contextmanager
def native_guard_from_publication(session, settings, *, admission):
    """Fixed internal bridge; neither acquire another lock nor mint admission.

    The publisher owns the descriptor, lifecycle, quota checks and sole commit.
    Revalidate that live admission before and after opaque reads. Nested readers
    see the same actual descriptor; restoration does not close or unlock it.
    """
    root = require_publication_admission(session, settings, admission)
    before = os.fstat(admission.factor_fd)
    prior = _NATIVE_GUARD.get()
    if prior is not None and require_native_storage_guard(root) != admission.factor_fd:
        raise ValueError("Different native and publication fences")
    token = _NATIVE_GUARD.set((root, admission.factor_fd, before.st_dev, before.st_ino))
    try:
        require_native_storage_guard(root)
        yield
        require_native_storage_guard(root)
        require_publication_admission(session, settings, admission)
    finally:
        _NATIVE_GUARD.reset(token)
