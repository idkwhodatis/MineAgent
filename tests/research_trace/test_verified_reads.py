"""Trace readers reject mutation through their pinned inode before releasing results."""

import hashlib
import os
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

from packages.research_trace import storage
from tests.research_trace.test_storage import binding as trace_binding  # noqa: F401
from tests.research_trace.test_storage import captured


def reference(content):
    return SimpleNamespace(
        size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest()
    )


def test_same_size_parquet_change_after_verification_is_rejected(tmp_path):
    original, replacement = (
        tmp_path / "original.parquet",
        tmp_path / "replacement.parquet",
    )
    with duckdb.connect() as connection:
        for path, number in ((original, 1), (replacement, 9)):
            connection.execute(
                f"COPY (SELECT {number}::BIGINT AS value) TO ? (FORMAT PARQUET, COMPRESSION UNCOMPRESSED)",
                [str(path)],
            )
        before, after = original.read_bytes(), replacement.read_bytes()
        assert len(before) == len(after)
        with (
            pytest.raises(storage.TraceStorageError),
            storage._verified_file(original, reference(before)) as verified,
        ):
            original.write_bytes(after)
            assert connection.execute(
                "SELECT * FROM read_parquet(?)", [verified]
            ).fetchall() == [(9,)]


def test_reader_does_not_return_results_after_inplace_mutation(
    tmp_path,
    trace_binding,  # noqa: F811 -- imported pytest fixture
    monkeypatch,
):
    recorder = captured(days=1)
    manifest, artifacts = storage.write_trace_artifacts(
        recorder.records, trace_binding, recorder.summary(), tmp_path
    )
    paths = {item.name: item.path for item in artifacts}
    original_connection = storage._connection

    @contextmanager
    def changed_connection():
        # All selected files have already been hashed. Rewrite their bytes in
        # place, retaining valid Parquet so the real queries themselves succeed.
        for item in artifacts:
            if item.name.endswith(".parquet"):
                os.chmod(item.path, 0o600)
                item.path.write_bytes(item.path.read_bytes())
        with original_connection() as connection:
            yield connection

    monkeypatch.setattr(storage, "_connection", changed_connection)
    with pytest.raises(storage.TraceStorageError):
        storage.TraceReader(manifest, paths).query("events")


def test_hashing_growth_is_bounded_and_rejected(tmp_path, monkeypatch):
    path = tmp_path / "evidence"
    content = b"original"
    path.write_bytes(content)
    real_read = os.read
    lengths = []

    def growing_read(fd, count):
        lengths.append(count)
        with path.open("ab") as stream:
            stream.write(b"x" * 100)
        return real_read(fd, count)

    monkeypatch.setattr(storage.os, "read", growing_read)
    with (
        pytest.raises(storage.TraceStorageError),
        storage._verified_file(path, reference(content)),
    ):
        pytest.fail("Changed file was admitted")
    assert lengths == [len(content) + 1]


@pytest.mark.parametrize("kind", ["file", "parent"])
def test_symlinks_are_not_followed(tmp_path, kind):
    directory = tmp_path / "real"
    directory.mkdir()
    path = directory / "evidence"
    path.write_bytes(b"good")
    link = tmp_path / "link"
    link.symlink_to(path if kind == "file" else directory)
    target = link if kind == "file" else link / "evidence"
    with (
        pytest.raises(storage.TraceStorageError),
        storage._verified_file(target, reference(b"good")),
    ):
        pytest.fail("Symlink admitted")


def test_unchanged_verified_descriptor_is_readable(tmp_path):
    path = tmp_path / "evidence"
    path.write_bytes(b"good")
    with storage._verified_file(path, reference(b"good")) as verified:
        assert Path(verified).read_bytes() == b"good"
