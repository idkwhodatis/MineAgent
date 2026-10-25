import os
from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from packages.factors.storage import (
    FactorStorageError,
    read_page,
    validate_rows,
    write_partition,
)


def sample(value=0.0):
    t = datetime(2026, 1, 2, 21, tzinfo=UTC)
    return {
        "event_time": t,
        "session_date": date(2026, 1, 2),
        "listing_id": "XNAS:A",
        "factor_version_id": str(UUID(int=1)),
        "available_at": t,
        "value": value,
        "value_state": "valid",
        "missing_reason": None,
        "input_window_start": date(2026, 1, 1),
        "input_window_end": date(2026, 1, 3),
        "input_evidence_digest": "a" * 64,
        "required_input_available_at": t,
        "membership_available_at": t,
        "lag_available_at": t,
    }


def test_roundtrip_zero_and_missing(tmp_path):
    a = sample()
    b = sample()
    b.update(
        listing_id="XNAS:B",
        value=None,
        value_state="input_missing",
        missing_reason="no_input",
    )
    part = write_partition([b, a], tmp_path / "values.parquet")
    assert (part.row_count, part.valid_count, part.missing_count) == (2, 1, 1)
    rows = read_page(part.path, part.sha256)
    assert rows[0]["value"] == 0 and rows[1]["value"] is None
    assert len(read_page(part.path, part.sha256, listing_id="XNAS:B")) == 1
    assert read_page(part.path, part.sha256, listing_id="' OR 1=1 --") == []


@pytest.mark.parametrize(
    "change",
    [
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": True},
        {"value": None},
        {"event_time": datetime(2026, 1, 2)},
        {"input_evidence_digest": "bad"},
        {"available_at": datetime(2025, 1, 1, tzinfo=UTC)},
    ],
)
def test_bad_rows(change):
    row = sample()
    row.update(change)
    with pytest.raises(FactorStorageError):
        validate_rows([row])


def test_duplicates(tmp_path):
    with pytest.raises(FactorStorageError):
        write_partition([sample(), sample()], tmp_path / "v.parquet")
    assert not list(tmp_path.iterdir())


def test_integrity_and_bounds(tmp_path):
    p = write_partition([sample()], tmp_path / "v.parquet")
    with pytest.raises(FactorStorageError):
        read_page(p.path, "0" * 64)
    with pytest.raises(FactorStorageError):
        read_page(p.path, p.sha256, limit=501)
    with pytest.raises(FactorStorageError):
        read_page(p.path, p.sha256, offset=True)
    with pytest.raises(FactorStorageError):
        write_partition([sample()], p.path)
    os.link(p.path, tmp_path / "hard")
    with pytest.raises(FactorStorageError):
        read_page(p.path, p.sha256)


def test_symlink_parent_rejected(tmp_path):
    directory = tmp_path / "real"
    directory.mkdir()
    p = write_partition([sample()], directory / "v.parquet")
    link = tmp_path / "link"
    link.symlink_to(directory, target_is_directory=True)
    with pytest.raises(FactorStorageError):
        read_page(link / "v.parquet", p.sha256)


def test_deterministic_sorted_output(tmp_path):
    a = sample()
    b = sample()
    b["listing_id"] = "XNAS:B"
    first = write_partition([a, b], tmp_path / "one")
    second = write_partition([b, a], tmp_path / "two")
    assert first.sha256 == second.sha256


def test_filtered_count_window_and_deadline(tmp_path):
    from packages.factors.storage import read_values_page

    a = sample()
    b = sample()
    b["listing_id"] = "XNAS:B"
    p = write_partition([a, b], tmp_path / "v")
    result = read_values_page(
        p.path, p.sha256, limit=1, start="2026-01-02", end="2026-01-03"
    )
    assert result["count"] == 2 and len(result["data"]) == 1
    assert read_values_page(p.path, p.sha256, end="2026-01-02")["count"] == 0
    with pytest.raises(FactorStorageError):
        write_partition([a], tmp_path / "bad", seconds=0)
    with pytest.raises(FactorStorageError):
        read_values_page(p.path, p.sha256, seconds=0)
