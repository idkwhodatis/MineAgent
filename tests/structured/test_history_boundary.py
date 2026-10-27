"""Outer read authority doubles; actual relocated files are tested separately."""

from contextlib import nullcontext
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import structured_factor_history as history
from backend.app.services import DomainError


def test_invalid_owner_rejected_before_any_storage_or_database_use():
    with pytest.raises(DomainError):
        history.read_historical_structured_execution(None, "not-a-uuid", uuid4(), None)


@pytest.mark.parametrize(
    "case", ["missing_owned_execution", "active_run", "missing_owned_run"]
)
def test_terminal_owned_result_required_before_any_physical_reader(
    monkeypatch, tmp_path, case
):
    owner = uuid4()
    execution = NS(run_id=uuid4())
    rows = iter(
        [None]
        if case == "missing_owned_execution"
        else [execution, None if case == "missing_owned_run" else NS(status="running")]
    )
    session = NS(scalar=lambda *_: next(rows))
    factory = NS(kw={"bind": object()}, begin=lambda: nullcontext(session))
    monkeypatch.setattr(history, "shared_lifecycle", lambda *_: nullcontext())
    monkeypatch.setattr(history, "native_storage_guard", lambda *_: nullcontext())
    monkeypatch.setattr(history, "require_structured_capability", lambda *_: None)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("Physical result accessed before terminal owner authority")

    monkeypatch.setattr(history, "read_historical_structured_bytes", unexpected)
    monkeypatch.setattr(history, "reconstruct_retained_input", unexpected)
    with pytest.raises(DomainError):
        history.read_historical_structured_execution(
            factory, owner, uuid4(), NS(storage_root=tmp_path)
        )


def test_interrupted_restore_rejects_even_with_new_schema(monkeypatch, tmp_path):
    (tmp_path / ".restore-incomplete").write_bytes(b"pending")
    monkeypatch.setattr(history, "shared_lifecycle", lambda *_: nullcontext())
    monkeypatch.setattr(history, "native_storage_guard", lambda *_: nullcontext())
    factory = NS(kw={"bind": object()})
    with pytest.raises(DomainError):
        history.read_historical_structured_execution(
            factory, uuid4(), uuid4(), NS(storage_root=tmp_path)
        )
