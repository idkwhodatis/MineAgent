"""Only pre-publication lock admission may durably requeue; body is never replayed."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.app.services import DomainError
from tests.factors.test_import_worker import admission_row
from workers.research import factor_import as worker


@pytest.mark.parametrize(
    "phase,code,retry",
    [
        ("enter", "factor_busy", True),
        ("body", "factor_busy", False),
        ("exit", "factor_busy", False),
        ("enter", "factor_capacity", False),
        ("enter", "factor_storage_uncertain", False),
        ("enter", "factor_recovery_required", False),
        ("enter", "factor_admission_timeout", False),
        ("impostor", "factor_busy", False),
    ],
)
def test_retry_is_known_unexecuted_admission_only(monkeypatch, phase, code, retry):
    session = MagicMock()
    session.scalar.return_value = admission_row()
    claim = worker.claim_one(session)
    factory = MagicMock()
    factory.return_value.__enter__.return_value = session
    monkeypatch.setattr(worker, "claim_one", lambda *a: claim)
    calls = []

    @contextmanager
    def publication(*args, **kwargs):
        if phase == "enter":
            raise DomainError(code, "not logged", 503)
        if phase == "impostor":
            error = RuntimeError("not logged")
            error.code = code
            raise error
        yield object()
        if phase == "exit":
            raise DomainError(code, "not logged", 503)

    def fenced(*args):
        calls.append("body")
        if phase == "body":
            raise DomainError(code, "not logged", 503)
        return SimpleNamespace(archive_object_id=claim.archive_object_id)

    monkeypatch.setattr(worker, "publication", publication)
    monkeypatch.setattr(worker, "fenced_admission", fenced)
    monkeypatch.setattr(
        worker, "owned", lambda *a: SimpleNamespace(storage_key="private")
    )
    monkeypatch.setattr(worker, "read_verified", lambda *a: b"package")
    monkeypatch.setattr(worker, "decode_package", lambda *a: object())
    monkeypatch.setattr(worker, "_finish", lambda *a: calls.append("finish"))
    outcome = MagicMock()
    monkeypatch.setattr(worker, "_outcome", outcome)
    assert worker.process_one(SimpleNamespace(storage_root="private"), factory=factory)
    assert outcome.call_count == 1
    assert outcome.call_args.kwargs.get("retry", False) is retry
    assert calls == (
        []
        if phase in {"enter", "impostor"}
        else ["body"]
        if phase == "body"
        else ["body", "finish"]
    )


@pytest.mark.parametrize(
    "generation,quiescent,expected",
    [
        (1, True, "queued"),
        (2, True, "queued"),
        (3, True, "failed"),
        (1, False, "blocked"),
        (3, False, "blocked"),
    ],
)
def test_outcome_retains_original_quiescence_and_generation_cap(
    monkeypatch, tmp_path, generation, quiescent, expected
):
    session = MagicMock()
    row = admission_row()
    row.generation = generation - 1
    session.scalar.return_value = row
    claim = worker.claim_one(session)
    session.scalar.side_effect = [row, None]
    factory = MagicMock()
    factory.return_value.__enter__.return_value = session
    fences = []

    @contextmanager
    def fence(root):
        fences.append(root)
        yield

    def check(root):
        assert fences == [root]
        if not quiescent:
            raise worker.ParserIsolationError("factor_recovery_required")

    monkeypatch.setattr(worker, "factor_fence", fence)
    monkeypatch.setattr(worker, "assert_quiescent", check)
    assert (
        worker._outcome(
            factory,
            claim,
            "factor_busy",
            SimpleNamespace(storage_root=tmp_path),
            retry=True,
        )
        == expected
    )
    assert row.status == expected
    assert row.lease_token is None and row.lease_expires_at is None
    assert row.generation == generation


@pytest.mark.parametrize("case", ["committed", "new_generation", "new_lease"])
def test_requeue_request_never_overrides_commit_or_replaced_fence(
    monkeypatch, tmp_path, case
):
    session = MagicMock()
    row = admission_row()
    session.scalar.return_value = row
    claim = worker.claim_one(session)
    envelope = SimpleNamespace(id=claim.id) if case == "committed" else None
    if case == "new_generation":
        row.generation += 1
    elif case == "new_lease":
        from uuid import uuid4

        row.lease_token = uuid4()
    session.scalar.side_effect = [row, envelope]
    factory = MagicMock()
    factory.return_value.__enter__.return_value = session

    def forbidden(*args):
        raise AssertionError("Committed/superseded generation must not enter a retry")

    monkeypatch.setattr(worker, "factor_fence", forbidden)
    actual = worker._outcome(
        factory,
        claim,
        "factor_busy",
        SimpleNamespace(storage_root=tmp_path),
        retry=True,
    )
    assert actual == ("succeeded" if case == "committed" else "superseded")
    assert row.status == ("succeeded" if case == "committed" else "processing")
