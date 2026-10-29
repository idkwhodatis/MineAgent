"""Public normal result replay after the real browser's explicit disclosures.

No direct ledger writes, generated results or provider/engine calls. The browser
must perform each first release; this helper only captures GETs and exact replays.
"""

from dataclasses import dataclass
from uuid import UUID

from packages.factors.contracts import canonical_bytes


@dataclass(frozen=True)
class NormalReleasedResults:
    responses: tuple[tuple[str, bytes], ...]
    receipts: tuple[tuple[str, bytes], ...]
    exposure_ids: frozenset[UUID]


def paths(run_id, execution_ids):
    before, after = tuple(execution_ids)
    assert before != after
    base = f"/api/v1/engine-runs/structured-search/runs/{run_id}"
    return (
        base + f"/executions/{before}",
        base + f"/executions/{after}",
        base + f"/comparison?before={before}&after={after}",
    )


def assert_unreleased(client, headers, other_headers, run_id, execution_ids):
    for path in paths(run_id, execution_ids):
        denied = client.get(path, headers=headers)
        assert denied.status_code == 409, denied.text
        assert denied.json()["code"] == "engine_result_release_required"
        assert client.get(path, headers=other_headers).status_code == 404
    for path in paths(run_id, execution_ids)[:2]:
        assert client.post(path + "/release", headers=other_headers).status_code == 404


def capture_released(client, headers, other_headers, run_id, execution_ids):
    responses, receipts, exposures, values = [], [], set(), []
    for path in paths(run_id, execution_ids):
        response = client.get(path, headers=headers)
        assert response.status_code == 200, response.text
        assert (
            response.json()["qualification"]
            == "fixed_snapshot_comparison_not_causal_gain"
        )
        responses.append((path, canonical_bytes(response.json())))
        values.append(response.json())
        assert client.get(path, headers=other_headers).status_code == 404
    assert values[2]["before"] == values[0]
    assert values[2]["after"] == values[1]
    for execution_id, value in zip(execution_ids, values[:2], strict=True):
        assert value["measurement"]["execution_id"] == str(execution_id)
        assert value["execution"]["execution_id"] == str(execution_id)
        # Actual synthetic positive factor causes submitted orders. This is an
        # observed engine assertion, not a precomputed performance score.
        assert value["execution"]["orders"]
    assert values[0]["candidate_id"] != values[1]["candidate_id"]
    for path in paths(run_id, execution_ids)[:2]:
        response = client.post(path + "/release", headers=headers)
        assert response.status_code == 200, response.text
        assert (
            response.json()["qualification"]
            == "exposure_record_not_execution_authority"
        )
        assert len(response.json()["exposure_ids"]) == 2
        exposures.update(UUID(key) for key in response.json()["exposure_ids"])
        receipts.append((path + "/release", canonical_bytes(response.json())))
    assert len(exposures) == 4
    return NormalReleasedResults(
        tuple(responses), tuple(receipts), frozenset(exposures)
    )
