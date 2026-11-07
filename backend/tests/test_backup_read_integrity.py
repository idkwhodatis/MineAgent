"""Actual-file regressions for backup hashing's stable read observation."""

import hashlib
import os
from types import SimpleNamespace

import pytest

from backend.app import operations_backup as backup


def _coarse_timestamps(monkeypatch):
    def wrap(operation):
        def read(*args, **kwargs):
            info = operation(*args, **kwargs)
            values = {
                key: getattr(info, key) for key in dir(info) if key.startswith("st_")
            }
            # Equal-length updates can occur within one filesystem timestamp tick.
            values.update(st_mtime_ns=0, st_ctime_ns=0)
            return SimpleNamespace(**values)

        return read

    monkeypatch.setattr(os, "fstat", wrap(os.fstat))
    monkeypatch.setattr(os, "stat", wrap(os.stat))


@pytest.mark.parametrize("mutation", ["replace", "overwrite", "unlink", "hardlink"])
def test_backup_hash_rejects_changes_during_read(tmp_path, monkeypatch, mutation):
    path = tmp_path / "object"
    original = b"original bytes"
    path.write_bytes(original)
    fdopen = os.fdopen

    class ChangingStream:
        def __init__(self, stream):
            self.stream, self.changed = stream, False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def fileno(self):
            return self.stream.fileno()

        def read(self, *args):
            content = self.stream.read(*args)
            if content and not self.changed:
                self.changed = True
                if mutation == "replace":
                    path.rename(tmp_path / "retired")
                    path.write_bytes(b"replaced bytes")
                elif mutation == "overwrite":
                    path.write_bytes(b"modified bytes")
                elif mutation == "unlink":
                    path.unlink()
                else:
                    os.link(path, tmp_path / "alias")
            return content

    _coarse_timestamps(monkeypatch)
    monkeypatch.setattr(os, "fdopen", lambda *a, **kw: ChangingStream(fdopen(*a, **kw)))
    with pytest.raises(backup.BackupError, match="Source changed"):
        backup._hash_file(tmp_path, "object", 100)


def test_backup_hash_preserves_empty_and_chunked_files(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "CHUNK", 3)
    for content in (b"", b"more than one chunk"):
        (tmp_path / "object").write_bytes(content)
        assert backup._hash_file(tmp_path, "object", len(content)) == {
            "path": "object",
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }


def test_verify_backup_rejects_replaced_inventory_entry(tmp_path, monkeypatch):
    import json

    root = tmp_path / "backup"
    (root / "storage/runs").mkdir(parents=True)
    contents = {
        "database.dump": b"PGDMParchive",
        "storage/runs/result.json": b"original",
    }
    for key, content in contents.items():
        (root / key).write_bytes(content)
    manifest = {
        "format": backup.FORMAT,
        "schema_revision": "004_m5",
        "application_ref": "a" * 40,
        "postgres_major": 18,
        "created_at": "2026-11-07T00:00:00+00:00",
        "storage_directories": ["runs"],
        "files": [
            {
                "path": key,
                "size_bytes": len(value),
                "sha256": hashlib.sha256(value).hexdigest(),
            }
            for key, value in contents.items()
        ],
    }
    raw = json.dumps(manifest).encode()
    (root / "manifest.json").write_bytes(raw)
    (root / "COMPLETE").write_bytes(hashlib.sha256(raw).hexdigest().encode() + b"\n")
    assert backup.verify_backup(root) == manifest
    original_open = backup._open_file
    _coarse_timestamps(monkeypatch)
    changed = False

    class ReplacingStream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def fileno(self):
            return self.stream.fileno()

        def read(self, *args):
            nonlocal changed
            data = self.stream.read(*args)
            if data and not changed:
                changed = True
                path = root / "storage/runs/result.json"
                # On coarse filesystems this rename can share the initial timestamp tick.
                path.rename(tmp_path / "retired")
                path.write_bytes(b"tampered")
            return data

    def replacing_open(path, key):
        stream, info = original_open(path, key)
        if key == "storage/runs/result.json":
            return ReplacingStream(stream), info
        return stream, info

    monkeypatch.setattr(backup, "_open_file", replacing_open)
    with pytest.raises(backup.BackupError, match="Source changed"):
        backup.verify_backup(root)
    assert changed
