"""Pre-admission transaction/guard order with explicit DB and byte-reader doubles."""

from contextlib import contextmanager
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import oe_structured_provider_input as service
from backend.app.services import DomainError
from tests.openevolve.test_structured_provider_submission import request
from tests.openevolve.test_structured_submission import request as fixture_request


def test_fixture_request_cannot_enter_normal_input_reader():
    with pytest.raises(DomainError):
        service.resolve_submission_data(NS(), uuid4(), fixture_request(), NS())


def test_missing_release_does_not_read_bytes_or_implicitly_disclose(monkeypatch):
    declaration = request()
    rows = iter((NS(id=declaration.snapshot_id, run_id=uuid4()), None))
    monkeypatch.setattr(service, "_try_neutral_lock", lambda *a: None)
    monkeypatch.setattr(
        service,
        "read_snapshot_in_session",
        lambda *a: pytest.fail("missing release read"),
    )
    with pytest.raises(DomainError) as error:
        service.resolve_submission_data(
            NS(scalar=lambda _: next(rows)), uuid4(), declaration, NS()
        )
    assert error.value.code == "structured_snapshot_release_required"


def test_owner_lock_precedes_guard_and_existing_release_is_revalidated(monkeypatch):
    declaration = request()
    owner = uuid4()
    run = NS(
        id=uuid4(),
        experiment_id=None,
        split_version_id=None,
        purpose="factor_research",
        engine="rdagent",
        config={"schema_version": "mineagent.factor-research.v2"},
    )
    publication = NS(id=declaration.snapshot_id, run_id=run.id)
    receipt = NS(
        id=uuid4(),
        owner_id=owner,
        run_id=run.id,
        experiment_id=None,
        split_version_id=None,
    )
    from datetime import date

    dataset = NS(
        id=uuid4(),
        start_date=date(2024, 3, 11),
        end_date=date(2024, 3, 15),
        content_sha256="a" * 64,
    )
    rows = iter((publication, receipt, run))
    events = []
    monkeypatch.setattr(
        service, "_try_neutral_lock", lambda *a: events.append("owner-lock")
    )

    @contextmanager
    def guard(_):
        assert events == ["owner-lock"]
        events.append("guard")
        yield
        events.append("guard-release")

    monkeypatch.setattr(service, "native_storage_guard", guard)
    monkeypatch.setattr(service, "read_snapshot_in_session", lambda *a: NS())
    monkeypatch.setattr(
        service,
        "release_bindings",
        lambda *a: (run, [("factor", dataset, {"measured": "fixture-double"})]),
    )

    def exposure(*a, **kw):
        assert kw["purpose"] == "engine_execution_snapshot_release"
        assert kw["provenance"]["dataset_content_sha256"] == dataset.content_sha256
        events.append("existing-release-check")
        return receipt

    monkeypatch.setattr(service, "record_exposure", exposure)

    def read(*args):
        assert events == ["owner-lock", "guard", "existing-release-check"]
        assert args[-2] == (receipt.id,)
        return "actual-reader-double"

    monkeypatch.setattr(service, "_resolve_retained_data", read)
    assert (
        service.resolve_submission_data(
            NS(scalar=lambda _: next(rows)),
            owner,
            declaration,
            NS(storage_root="unused"),
        )
        == "actual-reader-double"
    )
    assert events[-1] == "guard-release"
