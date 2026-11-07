"""Actual parsing from pinned upload bytes; faked DB state is not a PG gate."""

import hashlib
import os
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace as NS
from uuid import UUID, uuid4

import pytest

from backend.app.models import DataImport
from packages.marketdata.contracts import make_listing
from workers.data import executor


@pytest.mark.parametrize("replacement", ["missing", "changed", "symlink", "fifo"])
def test_upload_worker_parses_only_verified_bytes(tmp_path, monkeypatch, replacement):
    original = b"session_date,open,high,low,close,volume\n2024-01-02,10,11,9,10,100\n"
    source = tmp_path / "source.csv"
    source.write_bytes(original)
    sha = hashlib.sha256(original).hexdigest()
    listing = make_listing(
        symbol="AAPL",
        mic="XNAS",
        currency="USD",
        timezone="America/New_York",
        calendar="XNYS",
        vendor="upload",
    )
    config = {
        "symbol": "AAPL",
        "mic": "XNAS",
        "currency": "USD",
        "timezone": "America/New_York",
        "calendar": "XNYS",
        "volume_unit": "shares",
        "price_semantics": {},
        "columns": {
            key: key
            for key in ("session_date", "open", "high", "low", "close", "volume")
        },
        "upload": {
            "storage_key": "source.csv",
            "sha256": sha,
            "size_bytes": len(original),
            "format": "csv",
        },
    }
    item = NS(
        id=uuid4(),
        owner_id=uuid4(),
        kind="upload",
        request=config,
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        progress={},
    )
    block = NS(
        id=uuid4(),
        status="requested",
        storage_key=None,
        attempts=0,
        listing_id=UUID(listing.listing_id),
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 3),
    )

    @contextmanager
    def factory():
        yield NS(get=lambda model, key: item if model is DataImport else block)

    factory.begin = factory
    monkeypatch.setattr(executor, "fence_import", lambda *args: item)
    monkeypatch.setattr(executor, "_make_blocks", lambda *args: [block.id])
    monkeypatch.setattr(
        executor, "_save_checkpoint", lambda *args: ("checkpoint.json", "c" * 64)
    )
    verified_read = executor.read_verified

    def replace_after_verification(*args):
        content = verified_read(*args)
        source.unlink()
        if replacement == "changed":
            source.write_bytes(original.replace(b",10,100", b",11,100"))
        elif replacement == "symlink":
            outside = tmp_path / "other.csv"
            outside.write_bytes(original)
            source.symlink_to(outside)
        elif replacement == "fifo":
            os.mkfifo(source)
        return content

    monkeypatch.setattr(executor, "read_verified", replace_after_verification)
    opened = []
    path_open = Path.open

    def observe_reopen(path, *args, **kwargs):
        if path == source and "r" in kwargs.get("mode", args[0] if args else "r"):
            opened.append(path)
            # A real blocking FIFO open would never return; detect the forbidden
            # reopen without stranding the test process or starting a writer.
            if replacement == "fifo":
                raise AssertionError("Verified upload was reopened as a FIFO")
        return path_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", observe_reopen)
    results, failed = executor._fetch_blocks(
        factory,
        item.id,
        1,
        uuid4(),
        NS(storage_root=tmp_path, max_upload_bytes=10_000_000),
        None,
        [listing],
    )
    assert opened == [], (
        "The parser reopened a mutable pathname after checksum verification"
    )
    assert failed == 0 and len(results) == 1
    assert results[0].data[0].close == 10
    assert results[0].raw[0].content == {
        "artifact": "source.csv",
        "sha256": sha,
        "size_bytes": len(original),
    }
    assert block.status == "ready"
