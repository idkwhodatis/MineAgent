"""Typed navigation projection checks; not PG/physical result authority."""

from datetime import UTC, datetime
from types import SimpleNamespace as Row
from uuid import uuid4

import pytest

from backend.app.factor_execution_views import project_record
from backend.app.services import DomainError
from packages.factors.execution_snapshot import ExecutionSnapshotManifest
from tests.factors.test_execution_snapshot_services import publication


def records():
    pub = publication()
    pub.created_at = datetime.now(UTC)
    manifest = ExecutionSnapshotManifest.model_validate(pub.manifest)
    run = Row(
        id=pub.run_id,
        owner_id=pub.owner_id,
        dataset_version_id=manifest.input.dataset.dataset_version_id,
    )
    version = Row(
        id=pub.factor_version_id,
        owner_id=pub.owner_id,
        factor_id=uuid4(),
        code_sha256=manifest.input.version.code_sha256,
        definition_sha256=manifest.input.version.definition_sha256,
    )
    scope = {
        k: getattr(pub, k) for k in ("owner_id", "run_id", "attempt_id", "generation")
    }
    out = Row(
        publication_id=pub.id,
        manifest_sha256=pub.manifest_sha256,
        input_sha256=manifest.input_sha256,
        output_sha256=manifest.output_rows_sha256,
        created_at=pub.created_at,
        **scope,
    )
    analysis = Row(id=uuid4(), snapshot_id=pub.id, **scope)
    result = Row(analysis_id=analysis.id, **scope)
    return run, version, pub, out, analysis, result


def test_navigation_uses_dedicated_identity_without_data_release():
    value = project_record(*records()).model_dump(mode="json")
    assert value["kind"] == "execution_snapshot"
    assert value["analysis_state"] == "result_recorded"
    assert value["qualification"] == "identity_metadata_not_result_release"
    assert not set(value) & {
        "snapshot_id",
        "row_count",
        "valid_count",
        "metrics",
        "result",
        "measurement",
        "storage_key",
        "values",
    }


@pytest.mark.parametrize(
    "index,field",
    [
        (1, "owner_id"),
        (1, "id"),
        (1, "code_sha256"),
        (1, "definition_sha256"),
        (4, "snapshot_id"),
        (4, "owner_id"),
        (5, "analysis_id"),
        (5, "generation"),
    ],
)
def test_projection_rejects_cross_identity(index, field):
    rows = records()
    setattr(rows[index], field, "f" * 64 if field == "code_sha256" else uuid4())
    with pytest.raises(DomainError):
        project_record(*rows)


def test_pending_and_undeclared_never_imply_measurement():
    run, version, pub, *_ = records()
    value = project_record(run, version, pub, None, None, None)
    assert value.publication_state == "publication_pending"
    assert value.analysis_state == "not_declared"
    assert value.analysis_id is None
