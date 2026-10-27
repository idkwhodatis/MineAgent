"""Real authenticated source-result disclosure replay for the 025 cold drill.

No model/process result is fabricated here. The caller first runs the two real
configured generations and engines, then supplies their exact snapshot IDs.
This helper does not imply that internal history reads are disclosure grants.
"""

from dataclasses import dataclass
from uuid import UUID

import pytest
from sqlalchemy import select


@dataclass(frozen=True)
class ReleasedSourceResults:
    # Immutable response buffers compare pre-backup and restored public reads.
    receipts: tuple[tuple[str, bytes], ...]
    responses: tuple[tuple[str, bytes], ...]
    exposure_ids: frozenset[UUID]


def _body(response):
    from packages.factors.contracts import canonical_bytes

    assert response.status_code == 200, response.text
    return canonical_bytes(response.json())


def release_source_results(api, headers, other_headers, factory, snapshot_ids):
    """Explicit POST release precedes every real values/metrics/export read."""
    from backend.app.execution_snapshot_models import ExecutionSnapshotPublication
    from backend.app.factor_analysis_models import FactorDirectAnalysisIntent

    receipts, responses, exposure_ids = [], [], set()
    assert len(snapshot_ids) == len(set(snapshot_ids)) == 2
    with factory() as session:
        identities = []
        for snapshot_id in snapshot_ids:
            publication = session.get(ExecutionSnapshotPublication, snapshot_id)
            assert publication is not None
            analyses = session.scalars(
                select(FactorDirectAnalysisIntent).filter_by(snapshot_id=snapshot_id)
            ).all()
            assert len(analyses) == 1
            assert analyses[0].run_id == publication.run_id
            identities.append((publication.run_id, snapshot_id, analyses[0].id))
    for (run_id, snapshot_id, analysis_id), expected_rank in zip(
        identities, (1.0, 0.5), strict=True
    ):
        base = f"/api/v1/engine-runs/{run_id}"
        snapshot = base + f"/execution-snapshots/{snapshot_id}"
        analysis = base + f"/analyses/{analysis_id}"
        for path in (snapshot + "/values", snapshot + "/export", analysis):
            response = api.get(path, headers=headers)
            assert response.status_code == 409, response.text
            assert response.json()["code"] == "engine_result_release_required"
            assert api.get(path, headers=other_headers).status_code == 404
        for path in (snapshot, analysis):
            release = path + "/release"
            assert api.post(release, headers=other_headers).status_code == 404
            response = api.post(release, headers=headers)
            raw = _body(response)
            assert (
                response.json()["qualification"]
                == "exposure_record_not_execution_authority"
            )
            exposure_ids.update(UUID(x) for x in response.json()["exposure_ids"])
            assert _body(api.post(release, headers=headers)) == raw
            receipts.append((release, raw))
        for path in (snapshot + "/values", snapshot + "/export", analysis):
            response = api.get(path, headers=headers)
            raw = _body(response)
            if path == analysis:
                body = response.json()
                assert body["measurement"]["status"] == "passed"
                assert body["measurement"]["value"] == pytest.approx(
                    expected_rank, abs=1e-12, rel=0
                )
                assert body["result"]["rank_ic"] == pytest.approx(
                    expected_rank, abs=1e-12, rel=0
                )
                assert body["result"]["total_pairs"] == 3
            responses.append((path, raw))
    assert exposure_ids
    return ReleasedSourceResults(
        tuple(receipts), tuple(responses), frozenset(exposure_ids)
    )


def assert_restored_source_results(api, headers, other_headers, before):
    """GET works before replay POST; restore must not need a new disclosure grant."""
    for path, raw in before.responses:
        assert _body(api.get(path, headers=headers)) == raw
        assert api.get(path, headers=other_headers).status_code == 404
    for path, raw in before.receipts:
        assert _body(api.post(path, headers=headers)) == raw
        assert api.post(path, headers=other_headers).status_code == 404
    for path, raw in before.responses:
        assert _body(api.get(path, headers=headers)) == raw


def release_structured_results(api, headers, other_headers, run_id, execution_ids):
    """Both real execution receipts are necessary before any pair-derived flag."""
    assert len(execution_ids) == len(set(execution_ids)) == 2
    base = f"/api/v1/engine-runs/{run_id}"
    paths = tuple(base + f"/structured-executions/{eid}" for eid in execution_ids)
    pair = (
        base
        + f"/structured-comparison?before={execution_ids[0]}&after={execution_ids[1]}"
    )
    receipts, responses, exposure_ids = [], [], set()
    for path in (*paths, pair):
        assert api.get(path, headers=headers).status_code == 409
        assert api.get(path, headers=other_headers).status_code == 404
    for ordinal, path in enumerate(paths):
        release = path + "/release"
        assert api.post(release, headers=other_headers).status_code == 404
        response = api.post(release, headers=headers)
        raw = _body(response)
        exposure_ids.update(UUID(x) for x in response.json()["exposure_ids"])
        receipts.append((release, raw))
        assert _body(api.post(release, headers=headers)) == raw
        if ordinal == 0:
            assert api.get(pair, headers=headers).status_code == 409
    for path in (*paths, pair):
        responses.append((path, _body(api.get(path, headers=headers))))
    comparison = api.get(pair, headers=headers).json()
    assert comparison["before"]["execution_id"] == str(execution_ids[0])
    assert comparison["after"]["execution_id"] == str(execution_ids[1])
    assert (
        comparison["before"]["factor_version_id"]
        != comparison["after"]["factor_version_id"]
    )
    assert comparison["before"]["market_sha256"] == comparison["after"]["market_sha256"]
    assert comparison["eligibility_changed"] is False
    assert all(
        comparison[key] is True
        for key in (
            "score_values_changed",
            "ranking_changed",
            "targets_changed",
            "observed_orders_changed",
            "observed_fills_changed",
        )
    )
    assert exposure_ids
    return ReleasedSourceResults(
        tuple(receipts), tuple(responses), frozenset(exposure_ids)
    )
