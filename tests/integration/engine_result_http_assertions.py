"""Assertions to run ONLY against the actual four-unit persisted result fixture."""

from datetime import date

from sqlalchemy import select

from backend.app.experiment_services import _identities, classify_exposures
from backend.app.factor_analysis_models import FactorDirectAnalysisIntent
from backend.app.models import DatasetVersion, DataUsageExposure
from packages.factors.contracts import DateWindow


def assert_actual_engine_result_http(
    api,
    owner_headers,
    other_headers,
    factory,
    run_id,
    analysis_id,
    *,
    already_released=False,
):
    with factory() as session:
        intent = session.get(FactorDirectAnalysisIntent, analysis_id)
        snapshot_id = intent.snapshot_id
        dataset = session.get(DatasetVersion, intent.label_source_id)
        dataset_id = dataset.id
        window = DateWindow(start=dataset.start_date, end=dataset.end_date)
    base = f"/api/v1/engine-runs/{run_id}"
    snapshot = base + f"/execution-snapshots/{snapshot_id}"
    analysis = base + f"/analyses/{analysis_id}"
    metadata = api.get(snapshot, headers=owner_headers)
    assert metadata.status_code == 200, metadata.text
    assert all(
        metadata.json()[key] is None
        for key in ("row_count", "valid_count", "expected_cutoffs")
    )
    for path in (snapshot + "/values", snapshot + "/export", analysis):
        response = api.get(path, headers=owner_headers)
        if already_released:
            assert response.status_code == 200, response.text
        else:
            assert response.status_code == 409, response.text
            assert response.json()["code"] == "engine_result_release_required"
        assert api.get(path, headers=other_headers).status_code == 404
    for path in (snapshot, analysis):
        assert api.post(path + "/release", headers=other_headers).status_code == 404
        first = api.post(path + "/release", headers=owner_headers)
        assert first.status_code == 200, first.text
        repeated = api.post(path + "/release", headers=owner_headers)
        assert repeated.status_code == 200 and repeated.json() == first.json()
        assert (
            first.json()["qualification"] == "exposure_record_not_execution_authority"
        )
    values = api.get(snapshot + "/values", headers=owner_headers)
    assert values.status_code == 200, values.text
    assert values.json()["count"] == 3
    assert len(values.json()["data"]) == 3
    assert values.json()["execution_snapshot_id"] == str(snapshot_id)
    export = api.get(snapshot + "/export", headers=owner_headers)
    assert export.status_code == 200, export.text
    assert export.headers["cache-control"] == "no-store"
    assert export.json()["schema_version"] == "mineagent.execution-snapshot-export.v1"
    assert export.json()["rows"] == values.json()["data"]
    result = api.get(analysis, headers=owner_headers)
    assert result.status_code == 200, result.text
    assert result.json()["measurement"]["status"] == "passed"
    assert result.json()["measurement"]["value"] == 1.0
    assert result.json()["execution_snapshot_id"] == str(snapshot_id)
    assert api.get(analysis, headers=owner_headers).json() == result.json()
    with factory() as session:
        exposures = session.scalars(
            select(DataUsageExposure).where(
                DataUsageExposure.run_id == run_id,
                DataUsageExposure.source_key.like("engine-result:%"),
            )
        ).all()
        assert len(exposures) == 3  # snapshot-factor; analysis-factor and label
        assert all(
            row.dataset_version_id == dataset_id
            and row.start_date == window.start
            and row.end_date == window.end
            for row in exposures
        )
        dataset = session.get(DatasetVersion, dataset_id)
        classified = classify_exposures(
            _identities(session, dataset), window, exposures
        )
        assert classified["classification"] == "previously_exposed"
        assert classified["matching_count"] == 3
        future_labels = DateWindow(
            start=date.fromisoformat(metadata.json()["window_end"]), end=window.end
        )
        assert (
            classify_exposures(_identities(session, dataset), future_labels, exposures)[
                "classification"
            ]
            == "previously_exposed"
        )
