"""Real single-day CoSTEER RPC, model ledger, numeric units and snapshot gate.

No precursor process receipts or source publisher fixtures are used here.
Requires all three exact built images and an actual migrated PostgreSQL server.
"""

import pytest
from sqlalchemy import select

from backend.app.models import ExecutionAttempt, ResearchRun
from backend.app.rdagent_models import FactorGenerationOutcome, FactorGenerationSource
from backend.app.research_process_models import (
    ResearchProcessAdmission,
    ResearchProcessState,
)

pytestmark = pytest.mark.postgres


def test_actual_costeer_source_numeric_snapshot_generation(
    migrated_engine, settings, owner, api, owner_headers, other_headers
):
    from rd_controller_fixture import run_actual_generation

    from backend.app.execution_snapshot_models import FactorExecutionEvaluationOutcome

    factory, fence = run_actual_generation(migrated_engine, settings, owner.user.id)
    with factory() as session:
        run = session.get(ResearchRun, fence.run_id)
        attempt = session.get(ExecutionAttempt, fence.attempt_id)
        assert run.status == attempt.status == "succeeded"
        assert attempt.sandbox_stopped is True
        sources = session.scalars(
            select(FactorGenerationSource).filter_by(run_id=fence.run_id)
        ).all()
        outcomes = session.scalars(
            select(FactorGenerationOutcome).filter_by(run_id=fence.run_id)
        ).all()
        results = session.scalars(
            select(FactorExecutionEvaluationOutcome).filter_by(run_id=fence.run_id)
        ).all()
        assert len(sources) == len(outcomes) == len(results) == 1
        assert outcomes[0].outcome == "accepted" and results[0].status == "valid"
        admissions = session.scalars(
            select(ResearchProcessAdmission).filter_by(run_id=fence.run_id)
        ).all()
        assert sorted(row.role for row in admissions) == [
            "factor_cutoff_candidate",
            "factor_normalizer",
            "rd_orchestrator",
        ]
        assert all(
            session.get(ResearchProcessState, row.id).status == "stopped"
            for row in admissions
        )

    # Read the actual completed021 result over authenticated production HTTP.
    # No DTO fixture may substitute for the persisted protected-controller graph.
    path = f"/api/v1/engine-runs/{fence.run_id}"
    response = api.get(path + "/history", headers=owner_headers)
    assert response.status_code == 200
    rows = response.json()["data"]
    assert len(rows) == 1
    row = rows[0]
    assert row["evaluation_protocol"] == "execution_result"
    assert row["evaluation_status"] == "valid"
    assert row["execution_snapshot_state"] == "completed"
    assert row["execution_snapshot_id"] == str(results[0].snapshot_id)
    assert row["execution_snapshot_input_sha256"] == results[0].snapshot_input_sha256
    assert row["execution_snapshot_output_sha256"] == results[0].snapshot_output_sha256
    assert (
        row["execution_snapshot_manifest_sha256"] == results[0].snapshot_manifest_sha256
    )
    assert row["snapshot_id"] is None  # Never coerce into feature_snapshots.
    assert row["generation_outcome"] == "accepted"
    assert api.get(path + "/history", headers=owner_headers).json() == response.json()
    assert api.get(path + "/history", headers=other_headers).status_code == 404

    snapshot_path = path + f"/execution-snapshots/{results[0].snapshot_id}"
    metadata = api.get(snapshot_path, headers=owner_headers)
    assert metadata.status_code == 200
    value = metadata.json()
    assert value["id"] == str(results[0].snapshot_id)
    assert value["schema_version"] == "mineagent.factor-execution-snapshot.v1"
    assert value["state"] == "completed"
    assert value["output_sha256"] == results[0].snapshot_output_sha256
    assert value["values_available"] is value["analysis_results_available"] is False
    assert value["qualification"] == "persisted_metadata_not_reexecuted_or_reverified"
    assert api.get(snapshot_path, headers=owner_headers).json() == value
    assert api.get(snapshot_path, headers=other_headers).status_code == 404
