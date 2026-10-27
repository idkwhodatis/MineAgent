"""Local filesystem and owner/terminal doubles; not a restored PG proof."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app.execution_snapshot_history import historical_session, terminal_owner
from backend.app.factor_analysis_history import retained_reader
from backend.app.factor_native_io import NativeStorageError, native_storage_guard
from backend.app.services import DomainError


def test_historical_bytes_require_guard_and_reject_escape_and_symlink(tmp_path):
    (tmp_path / "factors").mkdir()
    (tmp_path / "datasets").mkdir()
    (tmp_path / "datasets" / "value.json").write_bytes(b"{}")
    (tmp_path / "datasets" / "link.json").symlink_to(
        tmp_path / "datasets" / "value.json"
    )
    read = retained_reader(tmp_path)
    with pytest.raises(NativeStorageError):
        read("datasets/value.json", 100)
    with native_storage_guard(tmp_path):
        assert read("datasets/value.json", 100) == b"{}"
        for key in ("../secret", "/etc/passwd", "datasets//value.json"):
            with pytest.raises(DomainError):
                read(key, 100)
        with pytest.raises(OSError):
            read("datasets/link.json", 100)
        with pytest.raises(DomainError):
            read("datasets/value.json", 1)


@pytest.mark.parametrize(
    "status",
    [
        "queued",
        "preparing",
        "running",
        "finalizing",
        "pause_requested",
        "paused",
        "cancel_requested",
    ],
)
def test_active_run_cannot_be_historical_authority(status):
    session = SimpleNamespace(
        scalar=lambda *a: SimpleNamespace(
            engine="rdagent", purpose="factor_research", status=status
        )
    )
    with pytest.raises(DomainError):
        terminal_owner(session, uuid4(), uuid4())


def test_wrong_owner_missing_row_rejected():
    session = SimpleNamespace(scalar=lambda *a: None)
    with pytest.raises(DomainError):
        terminal_owner(session, uuid4(), uuid4())


def test_owner_uuid_required_before_any_db_or_storage_access():
    with pytest.raises(DomainError), historical_session(None, "owner", None):
        pass
