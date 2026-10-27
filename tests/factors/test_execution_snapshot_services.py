"""Authority-double negatives; not PostgreSQL publication acceptance."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app import execution_snapshot_services as service
from backend.app.services import DomainError
from packages.research_runtime.budget_contracts import BudgetVector
from tests.factors.test_execution_snapshot_io import physical_fixture


def publication():
    journal, _ = physical_fixture()
    manifest = journal.manifest
    key, sha, _ = service.journal_address(journal)
    return SimpleNamespace(
        id=journal.publication_id,
        evaluation_id=manifest.evaluation_id,
        source_id=manifest.source_id,
        start_id=manifest.generation_start_id,
        factor_version_id=manifest.input.version.factor_version_id,
        reservation_id=journal.reservation_id,
        manifest=manifest.model_dump(mode="json"),
        manifest_sha256=journal.manifest_object.sha256,
        manifest_object_id=journal.manifest_object.object_id,
        manifest_size_bytes=journal.manifest_object.size_bytes,
        manifest_storage_key=journal.manifest_object.storage_key,
        journal=journal.model_dump(mode="json"),
        journal_sha256=sha,
        journal_key=key,
        peak_bytes=journal.peak_bytes,
        peak_entries=journal.peak_entries,
        **{
            k: getattr(manifest, k)
            for k in ("owner_id", "run_id", "attempt_id", "generation")
        },
    )


def test_decode_exact_journal_is_metadata_only():
    row = publication()
    assert service.decode_publication(row).publication_id == row.id


@pytest.mark.parametrize(
    "field",
    [
        "owner_id",
        "run_id",
        "attempt_id",
        "evaluation_id",
        "source_id",
        "start_id",
        "factor_version_id",
        "reservation_id",
        "manifest_object_id",
    ],
)
def test_decode_rejects_rebound_scope_or_identity(field):
    row = publication()
    setattr(row, field, uuid4())
    with pytest.raises(DomainError):
        service.decode_publication(row)


@pytest.mark.parametrize(
    "field",
    ["manifest_sha256", "journal_sha256", "journal_key", "manifest_storage_key"],
)
def test_decode_rejects_commitment_or_address_drift(field):
    row = publication()
    setattr(row, field, "f" * 64)
    with pytest.raises(DomainError):
        service.decode_publication(row)


@pytest.mark.parametrize(
    "change",
    [
        {"logical_key": "unrelated"},
        {"amounts": BudgetVector(storage_bytes=1).model_dump()},
    ],
)
def test_pending_requires_exact_reservation_not_just_minimum(monkeypatch, change):
    from backend.app import research_broker_services

    row = publication()
    reservation = SimpleNamespace(
        logical_key="execution-snapshot:" + str(row.id),
        amounts=BudgetVector(storage_bytes=row.peak_bytes).model_dump(),
    )
    for key, value in change.items():
        setattr(reservation, key, value)
    monkeypatch.setattr(
        research_broker_services, "require_reservation", lambda *a, **kw: reservation
    )
    with pytest.raises(DomainError):
        service._require_pending_reservation(object(), object(), row)
