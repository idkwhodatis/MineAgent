"""Metadata/projection contract doubles; physical result authority is the reader."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import oe_structured_result_views as views
from backend.app.execution_snapshot_models import ExecutionSnapshotPublication
from backend.app.models import ResearchRun
from backend.app.oe_structured_models import (
    OEStructuredCandidate,
    OEStructuredExecution,
)
from backend.app.services import DomainError
from packages.factors.contracts import digest
from packages.openevolve_structured.objective import StructuredMeasurement
from tests.openevolve.test_structured_persisted_identity import graph
from tests.structured.test_views import histories


def metadata():
    session, run, spec, _, candidate, rows = graph()
    rows[(ExecutionSnapshotPublication, spec.snapshot_id)] = NS(
        owner_id=run.owner_id,
        factor_version_id=run.config["fixed_inputs"]["factor_version_id"],
        run_id=uuid4(),
    )
    # ORM UUIDs, not transport JSON strings.
    from uuid import UUID

    rows[(ExecutionSnapshotPublication, spec.snapshot_id)].factor_version_id = UUID(
        run.config["fixed_inputs"]["factor_version_id"]
    )
    execution = NS(
        id=uuid4(),
        owner_id=run.owner_id,
        run_id=run.id,
        attempt_id=candidate.attempt_id,
        generation=1,
        candidate_id=candidate.id,
        program_version_id=candidate.program_version_id,
        snapshot_id=spec.snapshot_id,
    )
    groups = {OEStructuredCandidate: [candidate], OEStructuredExecution: [execution]}
    session.scalar = lambda q: (
        run if q.column_descriptions[0]["entity"] is ResearchRun else None
    )
    session.scalars = lambda q: NS(
        all=lambda: groups[q.column_descriptions[0]["entity"]]
    )
    return session, run, candidate, execution, groups


def test_metadata_exposes_actual_ids_without_scores_or_result_availability():
    session, run, candidate, execution, _ = metadata()
    (row,) = views.list_records(session, run.owner_id, run.id)
    assert (row.candidate_id, row.execution_id) == (candidate.id, execution.id)
    assert row.origin == "authenticated_user_seed"
    assert set(row.model_dump()).isdisjoint(
        {"score", "result", "measurement", "eligible", "accepted"}
    )


@pytest.mark.parametrize(
    "drift", ["owner", "snapshot", "program", "duplicate", "orphan"]
)
def test_metadata_same_scope_swaps_and_hidden_extra_execution_rejected(drift):
    session, run, _, execution, groups = metadata()
    if drift == "owner":
        execution.owner_id = uuid4()
    elif drift == "snapshot":
        execution.snapshot_id = uuid4()
    elif drift == "program":
        execution.program_version_id = uuid4()
    elif drift == "duplicate":
        groups[OEStructuredExecution].append(execution)
    else:
        execution.candidate_id = uuid4()
    with pytest.raises(DomainError):
        views.list_records(session, run.owner_id, run.id)


def pair():
    before, after = histories(changed=True)
    after.factor_version_id = before.factor_version_id
    after.factor_id = before.factor_id
    after.snapshot_id = before.snapshot_id
    after.snapshot_input_sha256 = before.snapshot_input_sha256
    after.structured_strategy_version_id = uuid4()
    after.result = after.result.model_copy(
        update={
            "snapshot_id": before.snapshot_id,
            "structured_strategy_version_id": after.structured_strategy_version_id,
        }
    )
    values = []
    for history, score in ((before, 2.0), (after, 3.0)):
        measurement = StructuredMeasurement(
            execution_id=history.execution_id,
            structured_strategy_version_id=history.structured_strategy_version_id,
            snapshot_id=history.snapshot_id,
            strategy_input_sha256=history.result.strategy_input_sha256,
            market_sha256=history.result.market_sha256,
            result_sha256=digest(history.result),
            objective_sha256="a" * 64,
            total_return_pct=score + 1,
            max_drawdown_pct=1.0,
            closed_trade_count=1,
            eligible=True,
            combined_score=score,
            reason="eligible",
        )
        values.append(
            NS(
                execution=history,
                candidate_id=uuid4(),
                measurement=measurement,
                measurement_sha256=digest(measurement),
                config_sha256="b" * 64,
            )
        )
    return values


def test_same_factor_program_pair_projects_observed_orders_and_noncausal_score_delta():
    before, after = pair()
    value = views.compare_released(before, after)
    assert (
        value.before.execution.factor_version_id
        == value.after.execution.factor_version_id
    )
    assert value.score_delta_percentage_points == 1.0
    assert (
        value.before.execution.orders[0].quantity
        != value.after.execution.orders[0].quantity
    )
    assert value.qualification == "fixed_snapshot_comparison_not_causal_gain"


@pytest.mark.parametrize(
    "drift", ["snapshot", "factor", "config", "objective", "window"]
)
def test_normal_pair_never_compares_other_snapshot_objective_or_window(drift):
    before, after = pair()
    if drift == "snapshot":
        after.execution.snapshot_id = uuid4()
    elif drift == "factor":
        after.execution.factor_version_id = uuid4()
    elif drift == "config":
        after.config_sha256 = "c" * 64
    elif drift == "objective":
        after.measurement = after.measurement.model_copy(
            update={"objective_sha256": "c" * 64}
        )
    else:
        after.execution.result.accounting_tables["window"] = {"different": True}
    with pytest.raises(DomainError):
        views.compare_released(before, after)


def test_ineligible_measurement_has_no_numeric_delta():
    before, after = pair()
    after.measurement = after.measurement.model_copy(
        update={
            "eligible": False,
            "combined_score": None,
            "reason": "minimum_closed_trades",
        }
    )
    assert views.compare_released(before, after).score_delta_percentage_points is None


def test_finite_extreme_scores_cannot_emit_infinite_delta():
    before, after = pair()
    before.measurement = before.measurement.model_copy(
        update={"combined_score": -1e308}
    )
    after.measurement = after.measurement.model_copy(update={"combined_score": 1e308})
    with pytest.raises(DomainError):
        views.compare_released(before, after)
