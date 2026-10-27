"""Projection/route contracts only; real execution stays in the actual controller gate."""

from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import engine_snapshot_routes as routes
from backend.app.services import DomainError
from packages.factors.contracts import digest
from tests.factors.test_execution_snapshot_contract import manifest


def recorded():
    value = manifest()
    run = NS(
        id=value.run_id,
        owner_id=value.owner_id,
        dataset_version_id=value.input.dataset.dataset_version_id,
    )
    publication = NS(
        id=value.snapshot_id,
        owner_id=value.owner_id,
        run_id=value.run_id,
        attempt_id=value.attempt_id,
        generation=value.generation,
        start_id=value.generation_start_id,
        source_id=value.source_id,
        evaluation_id=value.evaluation_id,
        factor_version_id=value.input.version.factor_version_id,
        manifest=value.model_dump(mode="json"),
        manifest_sha256=digest(value),
        created_at=datetime.now(UTC),
    )
    outcome = NS(
        publication_id=value.snapshot_id,
        owner_id=value.owner_id,
        run_id=value.run_id,
        attempt_id=value.attempt_id,
        generation=value.generation,
        manifest_sha256=digest(value),
        input_sha256=value.input_sha256,
        output_sha256=value.output_rows_sha256,
        created_at=datetime.now(UTC),
    )
    return run, publication, outcome


def test_completed_metadata_does_not_release_values_or_claim_reverification():
    run, publication, outcome = recorded()
    before = deepcopy(publication.manifest)
    result = routes.metadata_projection(run, publication, outcome)
    assert publication.manifest == before
    assert result.id == publication.id
    assert result.state == "completed"
    assert result.row_count is result.valid_count is result.expected_cutoffs is None
    assert not result.values_available and not result.analysis_results_available
    assert result.qualification == "persisted_metadata_not_reexecuted_or_reverified"
    assert result.dependency_claim == "provided_not_proven_read"
    assert (
        not {"storage_key", "rows", "values", "metrics", "measurement"}
        & result.model_dump().keys()
    )


def test_pending_metadata_never_has_completed_output_or_time():
    run, publication, _ = recorded()
    result = routes.metadata_projection(run, publication, None)
    assert result.state == "publication_pending"
    assert result.output_sha256 is None and result.completed_at is None


@pytest.mark.parametrize(
    "field",
    [
        "owner_id",
        "run_id",
        "attempt_id",
        "id",
        "source_id",
        "evaluation_id",
        "factor_version_id",
    ],
)
def test_metadata_refuses_substituted_publication_identity(field):
    run, publication, outcome = recorded()
    setattr(publication, field, uuid4())
    with pytest.raises(DomainError) as error:
        routes.metadata_projection(run, publication, outcome)
    assert error.value.code == "engine_snapshot_metadata_inconsistent"


@pytest.mark.parametrize("field", ["manifest_sha256", "input_sha256", "output_sha256"])
def test_metadata_refuses_completed_hash_drift(field):
    run, publication, outcome = recorded()
    setattr(outcome, field, "f" * 64)
    with pytest.raises(DomainError):
        routes.metadata_projection(run, publication, outcome)


def test_owner_check_precedes_snapshot_lookup(monkeypatch):
    def denied(*args):
        raise DomainError("not_found", "Run not found", 404)

    monkeypatch.setattr(routes, "owned_engine_run", denied)
    with pytest.raises(DomainError) as error:
        routes.read_execution_snapshot_metadata(
            uuid4(),
            uuid4(),
            NS(id=uuid4()),
            NS(execute=lambda *args: pytest.fail("foreign snapshot queried")),
        )
    assert error.value.code == "not_found"


def test_snapshot_lookup_repeats_run_owner_scope_and_no_legacy_table(monkeypatch):
    from sqlalchemy.dialects import postgresql

    run, _, _ = recorded()
    monkeypatch.setattr(routes, "owned_engine_run", lambda *args: run)
    captured = []

    def execute(query):
        captured.append(str(query.compile(dialect=postgresql.dialect())))
        return NS(one_or_none=lambda: None)

    with pytest.raises(DomainError):
        routes.read_execution_snapshot_metadata(
            run.id, uuid4(), NS(id=run.owner_id), NS(execute=execute)
        )
    where = captured[0].split("WHERE")[1]
    assert "factor_execution_snapshot_publications.owner_id =" in where
    assert "factor_execution_snapshot_publications.run_id =" in where
    assert "feature_snapshots" not in captured[0]
