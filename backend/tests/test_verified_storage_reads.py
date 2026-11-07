"""Actual filesystem regressions for bounded immutable artifact reads."""

import hashlib
import multiprocessing
import os
from pathlib import Path

import pytest

from backend.app import operations_backup, storage
from backend.app.services import DomainError


def _read_fifo(root, result):
    try:
        storage.read_verified(Path(root), "fifo", hashlib.sha256(b"").hexdigest(), 100)
    except DomainError as exc:
        result.put(exc.code)


def test_special_file_is_rejected_without_waiting_for_a_writer(tmp_path):
    os.mkfifo(tmp_path / "fifo")
    context = multiprocessing.get_context("spawn")
    result = context.Queue()
    process = context.Process(target=_read_fifo, args=(str(tmp_path), result))
    process.start()
    try:
        process.join(timeout=5)
        assert not process.is_alive(), (
            "Artifact FIFO open blocked before type validation"
        )
        assert process.exitcode == 0
        assert result.get(timeout=1) == "invalid_artifact"
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
        result.close()


@pytest.mark.parametrize("reader", ["artifact", "backup"])
def test_parent_replacement_cannot_redirect_read_outside_storage(
    tmp_path, monkeypatch, reader
):
    root, outside = tmp_path / "storage", tmp_path / "outside"
    parent = root / "objects"
    parent.mkdir(parents=True)
    outside.mkdir()
    content = b"unchanged expected bytes"
    (parent / "object").write_bytes(content)
    (outside / "object").write_bytes(content)
    original_open = os.open
    swapped = False
    outside_opened = False

    def replace_before_open(path, flags, *args, **kwargs):
        nonlocal swapped, outside_opened
        # Swap after pathname validation but before the first storage lookup.
        if not swapped and (Path(path) == parent / "object" or path == "objects"):
            parent.rename(root / "retired")
            parent.symlink_to(outside, target_is_directory=True)
            swapped = True
        fd = original_open(path, flags, *args, **kwargs)
        if os.fstat(fd).st_ino == (outside / "object").stat().st_ino:
            outside_opened = True
        return fd

    monkeypatch.setattr(os, "open", replace_before_open)
    with pytest.raises((DomainError, operations_backup.BackupError, OSError)):
        if reader == "artifact":
            storage.read_verified(
                root, "objects/object", hashlib.sha256(content).hexdigest(), 100
            )
        else:
            operations_backup._hash_file(root, "objects/object", 100)
    assert swapped
    assert not outside_opened


def test_symlink_storage_root_is_not_resolved_away(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    content = b"artifact"
    (real / "object").write_bytes(content)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(DomainError):
        storage.read_verified(link, "object", hashlib.sha256(content).hexdigest(), 100)


def test_missing_artifact_retains_recovery_diagnostic(tmp_path):
    with pytest.raises(DomainError) as caught:
        storage.read_verified(tmp_path, "missing/object", "0" * 64, 100)
    assert caught.value.code == "artifact_missing"


@pytest.mark.parametrize("replacement", [False, True])
def test_inode_or_content_change_during_read_is_rejected(
    tmp_path, monkeypatch, replacement
):
    path = tmp_path / "object"
    original = b"old immutable data"
    path.write_bytes(original)
    fdopen = os.fdopen

    class ChangingStream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def fileno(self):
            return self.stream.fileno()

        def read(self, *args):
            content = self.stream.read(*args)
            if replacement:
                path.rename(tmp_path / "retired")
            path.write_bytes(b"new immutable data")
            return content

    monkeypatch.setattr(
        os, "fdopen", lambda *args, **kwargs: ChangingStream(fdopen(*args, **kwargs))
    )
    with pytest.raises(DomainError) as caught:
        storage.read_verified(
            tmp_path, "object", hashlib.sha256(original).hexdigest(), 100
        )
    assert caught.value.code == "artifact_integrity"
