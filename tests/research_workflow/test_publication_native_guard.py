"""Real held directory fence is reused, never reacquired or unlocked."""

import fcntl
import os
import time
from types import SimpleNamespace as NS

import pytest

from backend.app.core.maintenance import _lifecycle
from backend.app.factor_native_io import (
    NativeStorageError,
    require_native_storage_guard,
)
from backend.app.factor_services import PublicationAdmission
from backend.app.research_workflow_native_guard import native_guard_from_publication
from backend.app.services import DomainError


@pytest.mark.parametrize(
    "change", [None, "expired", "lifecycle", "renamed", "inactive", "late"]
)
def test_actual_publisher_descriptor_and_lifecycle(tmp_path, monkeypatch, change):
    factors = tmp_path / "factors"
    factors.mkdir()
    rootfd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    fd = os.open(factors, os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    engine = object()
    session = NS(get_bind=lambda: engine, is_active=change != "inactive")
    settings = NS(storage_root=tmp_path)
    token = _lifecycle.set((engine if change != "lifecycle" else object(), tmp_path))
    admission = PublicationAdmission(
        time.monotonic() + (-1 if change == "expired" else 10), rootfd, fd
    )
    if change == "renamed":
        factors.rename(tmp_path / "old")
        factors.mkdir()
    monkeypatch.setattr(
        fcntl, "flock", lambda *a: pytest.fail("Must not reacquire flock")
    )
    try:

        def read():
            with native_guard_from_publication(session, settings, admission=admission):
                assert require_native_storage_guard(tmp_path) == fd
                if change == "late":
                    session.is_active = False

        if change:
            with pytest.raises((DomainError, NativeStorageError)):
                read()
        else:
            read()
        os.fstat(fd)  # Adapter never closes the publisher's actual descriptor.
        with pytest.raises(NativeStorageError):
            require_native_storage_guard(tmp_path)
    finally:
        _lifecycle.reset(token)
        os.close(fd)
        os.close(rootfd)
