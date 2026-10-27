"""Transaction-order tests with inert persistence; actual bytes/PG have separate gates."""

from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import engine_result_release as release
from backend.app.services import DomainError


@pytest.fixture
def harness(monkeypatch):
    events = []
    owner, run_id, resource_id = uuid4(), uuid4(), uuid4()
    verified = object()
    state = NS(
        existing=True, fail_commit=False, fail_binding=False, tamper_receipt=False
    )

    class Session:
        def scalar(self, query):
            return uuid4() if state.existing else None

    @contextmanager
    def session():
        yield Session()

    @contextmanager
    def transaction():
        events.append("begin")
        try:
            yield Session()
            if state.fail_commit:
                raise RuntimeError("commit unavailable")
            events.append("commit")
        except Exception:
            events.append("rollback")
            raise

    class Factory:
        def __init__(self):
            self.kw = {"bind": object()}

        __call__ = staticmethod(session)
        begin = staticmethod(transaction)

    @contextmanager
    def lifecycle(*args):
        events.append("lifecycle")
        yield

    def reader(*args):
        events.append("private_verified_buffer")
        return verified

    def bindings(*args):
        events.append("exact_bindings")
        assert args[-1] is verified
        if state.fail_binding:
            raise DomainError("engine_result_inconsistent", "Evidence differs", 409)
        dataset = NS(
            id=uuid4(),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 3, 1),
            content_sha256="a" * 64,
        )
        return NS(id=run_id, experiment_id=None, split_version_id=None), [
            ("factor", dataset, {"run_id": str(run_id)})
        ]

    monkeypatch.setattr(release, "owned_resource", lambda *args: None)
    monkeypatch.setattr(release, "read_historical_execution_snapshot", reader)
    monkeypatch.setattr(release, "release_bindings", bindings)
    monkeypatch.setattr(release, "shared_lifecycle", lifecycle)
    monkeypatch.setattr(
        release, "advisory_lock", lambda *args: events.append("owner_lock")
    )
    monkeypatch.setattr(
        release,
        "record_exposure",
        lambda *args, **kwargs: (
            events.append("record_exposure")
            or NS(
                id=resource_id,
                owner_id=owner,
                run_id=uuid4() if state.tamper_receipt else run_id,
                experiment_id=None,
                split_version_id=None,
            )
        ),
    )
    return NS(
        factory=Factory(),
        settings=NS(storage_root="unused"),
        args={
            "owner_id": owner,
            "run_id": run_id,
            "resource_id": resource_id,
            "kind": "execution_snapshot",
        },
        state=state,
        events=events,
        verified=verified,
    )


def test_private_verification_precedes_independent_owner_first_commit(harness):
    h = harness
    result, ids = release.disclose(h.factory, h.settings, **h.args, create_release=True)
    assert result is h.verified and ids == (h.args["resource_id"],)
    assert h.events == [
        "private_verified_buffer",
        "lifecycle",
        "begin",
        "owner_lock",
        "exact_bindings",
        "record_exposure",
        "commit",
    ]


def test_get_without_receipt_never_verifies_or_records(harness):
    h = harness
    h.state.existing = False
    with pytest.raises(DomainError) as caught:
        release.disclose(h.factory, h.settings, **h.args, create_release=False)
    assert caught.value.code == "engine_result_release_required"
    assert h.events == []


@pytest.mark.parametrize("failure", ["fail_commit", "fail_binding"])
def test_failed_second_phase_never_returns_private_values(harness, failure):
    h = harness
    setattr(h.state, failure, True)
    with pytest.raises((RuntimeError, DomainError)):
        release.disclose(h.factory, h.settings, **h.args, create_release=True)
    assert h.events[-1] == "rollback" and "commit" not in h.events
    if failure == "fail_binding":
        assert "record_exposure" not in h.events


def test_exact_existing_receipt_is_rechecked_before_get_returns(harness):
    h = harness
    result, _ = release.disclose(h.factory, h.settings, **h.args, create_release=False)
    assert result is h.verified
    assert h.events[-3:] == ["exact_bindings", "record_exposure", "commit"]


def test_replayed_receipt_requires_actual_ledger_run_binding(harness):
    h = harness
    h.state.tamper_receipt = True
    with pytest.raises(DomainError) as caught:
        release.disclose(h.factory, h.settings, **h.args, create_release=False)
    assert caught.value.code == "engine_result_inconsistent"
    assert h.events[-1] == "rollback"
