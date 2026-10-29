"""Real metadata codecs/query construction with DB doubles, no physical eligibility."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from backend.app import oe_structured_catalog as service
from backend.app.services import DomainError
from packages.factors.contracts import digest
from tests.factors.test_execution_snapshot_contract import manifest as fixture_manifest


def fixture(monkeypatch):
    manifest = fixture_manifest()
    owner = manifest.owner_id
    publication = NS(
        id=manifest.snapshot_id,
        owner_id=owner,
        run_id=manifest.run_id,
        manifest=manifest.model_dump(mode="json"),
        manifest_sha256=digest(manifest),
    )
    dataset = NS(
        id=manifest.input.dataset.dataset_version_id,
        content_sha256=manifest.input.dataset.content_sha256,
        start_date=manifest.input.window.start,
        end_date=manifest.input.window.end,
    )
    run = NS(id=manifest.run_id, experiment_id=None, split_version_id=None)
    evidence = {"manifest_sha256": digest(manifest)}
    release = NS(
        id=uuid4(),
        owner_id=owner,
        dataset_version_id=dataset.id,
        run_id=run.id,
        experiment_id=None,
        split_version_id=None,
        start_date=dataset.start_date,
        end_date=dataset.end_date,
        purpose="engine_execution_snapshot_release",
        identities=["fixture"],
        provenance={
            **evidence,
            "role": "factor",
            "dataset_version_id": str(dataset.id),
            "dataset_content_sha256": dataset.content_sha256,
        },
    )
    version = NS(
        id=manifest.input.version.factor_version_id, owner_id=owner, factor_id=uuid4()
    )
    monkeypatch.setattr(
        service, "release_bindings", lambda *a: (run, [("factor", dataset, evidence)])
    )
    from backend.app import experiment_services

    monkeypatch.setattr(experiment_services, "_identities", lambda *a: ["fixture"])
    return (
        NS(scalar=lambda _: release, get=lambda *a: version),
        owner,
        publication,
        release,
        version,
    )


def test_released_choice_has_real_typed_ids_no_values_or_admission(monkeypatch):
    session, owner, publication, release, version = fixture(monkeypatch)
    item = service._option(session, owner, publication)
    assert item.snapshot_id == publication.id
    assert item.factor_id == version.factor_id
    assert item.release_receipt_id == release.id
    assert item.qualification == "released_historical_metadata_requires_fresh_preflight"
    assert not {"values", "rows", "score", "can_submit", "admission_id"} & set(
        item.model_dump()
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("purpose", "other"),
        ("dataset_version_id", uuid4()),
        ("run_id", uuid4()),
        ("provenance", {}),
    ],
)
def test_wrong_release_tuple_cannot_populate_selection(monkeypatch, field, value):
    session, owner, publication, release, _ = fixture(monkeypatch)
    setattr(release, field, value)
    with pytest.raises(DomainError):
        service._option(session, owner, publication)


def test_cross_owner_denied_before_release_lookup(monkeypatch):
    session, _, publication, _, _ = fixture(monkeypatch)
    monkeypatch.setattr(
        service, "release_bindings", lambda *a: pytest.fail("cross owner read")
    )
    with pytest.raises(DomainError) as error:
        service._option(session, uuid4(), publication)
    assert error.value.status_code == 404


def test_list_query_requires_completed_typed_outcome_and_existing_owned_release():
    statements = []

    def scalars(statement):
        statements.append(statement)
        return NS(all=list)

    result = service.list_snapshots(NS(scalars=scalars), uuid4(), offset=0, limit=20)
    sql = str(statements[0].compile(dialect=postgresql.dialect()))
    assert "JOIN factor_execution_snapshot_outcomes" in sql
    assert "JOIN data_usage_exposures" in sql
    assert (
        "data_usage_exposures.owner_id = factor_execution_snapshot_publications.owner_id"
        in sql
    )
    assert result.items == () and not result.has_more
