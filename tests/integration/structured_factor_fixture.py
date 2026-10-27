"""025 genuine two-factor precursor: eight actual configured runtime units.

Distinct declared lookbacks, same retained valid-OHLC synthetic dataset. No rows,
source outcomes or stopped receipts are fabricated. Not locally executed.
"""

import hashlib
import os
import re
from datetime import date
from pathlib import Path

from packages.qlib_adapter.objective import DirectObjectiveDeclaration
from sqlalchemy.orm import sessionmaker
from workers.research.rdagent_configured import create_configured_run

from packages.factors.contracts import DateWindow
from packages.rdagent_adapter.contracts import (
    DependencyLock,
    FactorResearchConfig,
    RuntimePins,
)
from packages.rdagent_adapter.numeric_profile import (
    CANDIDATE_LOCK_SHA256,
    NORMALIZER_LOCK_SHA256,
)
from packages.research_runtime.budget_contracts import BudgetVector

ROOT = Path(__file__).resolve().parents[2]


def _prepare_factor(factory, dataset, source, policy, settings, owner_id, lookback):
    """Return factory/fence only after real022 plan and retained dataset commit."""
    images = {
        role: os.environ.get("MINEAGENT_" + role.upper() + "_IMAGE", "")
        for role in ("candidate", "normalizer", "orchestrator", "analysis")
    }
    parent = os.environ.get("MINEAGENT_QLIB_INSTALLED_IMAGE", "")
    if not all(
        re.fullmatch(r"sha256:[0-9a-f]{64}", value)
        for value in (*images.values(), parent)
    ):
        raise ValueError(
            "Actual built controller/candidate/normalizer/analysis/installed Qlib pins required"
        )
    runtime = RuntimePins(
        **{role + "_image": image for role, image in images.items()},
        dependency_locks=tuple(
            DependencyLock(role=role, sha256=value)
            for role, value in (
                (
                    "analysis",
                    hashlib.sha256(
                        (ROOT / "runtimes/qlib/requirements.lock").read_bytes()
                    ).hexdigest(),
                ),
                ("candidate", CANDIDATE_LOCK_SHA256),
                ("normalizer", NORMALIZER_LOCK_SHA256),
                (
                    "orchestrator",
                    hashlib.sha256(
                        (ROOT / "runtimes/rdagent/requirements.lock").read_bytes()
                    ).hexdigest(),
                ),
            )
        ),
    )
    window = DateWindow(start=date(2024, 3, 13), end=date(2024, 3, 14))
    config = FactorResearchConfig.model_validate(
        {
            "input": {
                "dataset": source.dataset.model_dump(mode="json"),
                "calculation_universe": source.universe.model_dump(mode="json"),
                "window": window.model_dump(mode="json"),
                "calendar_sha256": source.calendar.sha256,
                "market_timezone": source.calendar.timezone,
                "input_fields": [
                    item.model_dump(mode="json") for item in source.input_fields
                ],
                "lookback_sessions": lookback,
                "lag_sessions": 0,
            },
            "task": {
                "name": f"configured-close-return-{lookback}",
                "description": f"Compute {lookback}-prior-session unadjusted close return from retained declared daily source.",
                "formulation": f"close[t]/close[t-{lookback}]-1",
                "factor_kind": "time_series",
                "output_unit": "return",
                "interpretation": "unsigned",
            },
            "runtime": runtime.model_dump(mode="json"),
            "budget": BudgetVector(
                calls=32,
                tokens=200000,
                mutation_slots=1,
                evaluator_executions=1,
                wall_milliseconds=240000,
                storage_bytes=512 * 1024**2,
                process_slots=4,
            ).model_dump(),
            "max_iterations": 1,
            "max_output_rows_per_cutoff": 10000,
            "max_output_bytes_per_cutoff": 16777216,
        }
    )
    objective = DirectObjectiveDeclaration(
        window=window,
        label_horizon_sessions=1,
        metric="rank_ic",
        direction="at_least",
        threshold=0.4,
        minimum_defined_days=1,
        minimum_pairs=3,
    )
    fence = create_configured_run(
        factory,
        owner_id=owner_id,
        dataset_id=dataset.id,
        config=config,
        objective=objective,
        source_policy=policy,
        qlib_installed_parent_image_id=parent,
        settings=settings,
    )
    return fence


def prepare_structured_pair(engine, settings, owner_id):
    """Compute two real versions, validate retained rows, then declare two units."""
    import json
    from uuid import uuid4

    import pytest
    from backend.app.execution_snapshot_history import (
        read_historical_execution_snapshot,
    )
    from backend.app.execution_snapshot_models import ExecutionSnapshotPublication
    from packages.rdagent_adapter.configured_source import parse_retained_source
    from sqlalchemy import select
    from structured_market_fixture import encoded_structured_market
    from workers.research.rdagent_configured import execute_configured_controller

    from backend.app.core.atomic_storage import provision_directory
    from backend.app.models import DatasetVersion, ResearchRun
    from backend.app.research_process_models import (
        ResearchProcessAdmission,
        ResearchProcessState,
    )
    from packages.factors.contracts import digest
    from packages.structured_factor_strategy.contracts import StructuredProgram
    from packages.structured_factor_strategy.run import StructuredRunConfig
    from runtimes.structured_factor_engine.recipe import dependency_lock_sha256
    from workers.research.structured_factor import create_structured_acceptance_run

    pins = tuple(
        os.environ.get(name, "")
        for name in (
            "MINEAGENT_STRUCTURED_ENGINE_IMAGE",
            "MINEAGENT_STRUCTURED_ENGINE_PARENT_IMAGE",
        )
    )
    assert all(re.fullmatch(r"sha256:[0-9a-f]{64}", p) for p in pins), (
        "Measured structured engine image/parent required"
    )
    factory = sessionmaker(engine, expire_on_commit=False)
    args = encoded_structured_market()
    key = f"datasets/{owner_id}/{args['dataset_id']}"
    directory = settings.storage_root / key
    provision_directory(directory, durable_parent=settings.storage_root)
    for name, raw in {
        "manifest.json": args["manifest_bytes"],
        **args["artifacts"],
    }.items():
        with (directory / name).open("xb") as stream:
            stream.write(raw)
    manifest = json.loads(args["manifest_bytes"])
    dataset = DatasetVersion(
        id=args["dataset_id"],
        owner_id=owner_id,
        name="Structured pair valid-OHLC synthetic fixture",
        market="US",
        start_date=date(2024, 3, 11),
        end_date=date(2024, 3, 15),
        source="fixed_fixture",
        is_synthetic=True,
        content_sha256=args["manifest_sha256"],
        manifest=manifest,
        storage_key=key,
    )
    with factory.begin() as session:
        session.add(dataset)
    # The factor reader deliberately consumes only its two primitive artifacts;
    # the unchanged manifest still commits the separate structured actions proof.
    source = parse_retained_source(
        **{
            **args,
            "artifacts": {
                k: args["artifacts"][k] for k in ("preview.json", "sessions.json")
            },
        }
    )
    snapshots = []
    histories = []
    expected = (
        {"AAA": 104 / 100 - 1, "BBB": 102 / 100 - 1, "CCC": 98 / 100 - 1},
        {"AAA": 104 / 103 - 1, "BBB": 102 / 101 - 1, "CCC": 98 / 99 - 1},
    )
    names = {item["listing_id"]: item["symbol"] for item in manifest["listings"]}
    for lookback, values in zip((2, 1), expected, strict=True):
        fence = _prepare_factor(
            factory, dataset, source, args["policy"], settings, owner_id, lookback
        )
        assert execute_configured_controller(factory, fence, settings) == fence.run_id
        with factory() as session:
            assert session.get(ResearchRun, fence.run_id).status == "succeeded"
            units = session.scalars(
                select(ResearchProcessAdmission).filter_by(run_id=fence.run_id)
            ).all()
            assert len(units) == 4
            assert {unit.role for unit in units} == {
                "rd_orchestrator",
                "factor_cutoff_candidate",
                "factor_normalizer",
                "native_evaluator",
            }
            assert all(
                session.get(ResearchProcessState, unit.id).status == "stopped"
                for unit in units
            )
            rows = session.scalars(
                select(ExecutionSnapshotPublication).filter_by(
                    owner_id=owner_id, run_id=fence.run_id
                )
            ).all()
            assert len(rows) == 1
            snapshot_id = rows[0].id
        history = read_historical_execution_snapshot(
            factory, owner_id, snapshot_id, settings
        )
        assert len(history.rows) == 3
        assert {row.session_date for row in history.rows} == {date(2024, 3, 13)}
        assert all(
            row.value_state == "valid"
            and row.factor_version_id == history.factor_version_id
            for row in history.rows
        )
        assert {
            names[row.listing_id]: row.value for row in history.rows
        } == pytest.approx(values, abs=1e-12, rel=0)
        snapshots.append(snapshot_id)
        histories.append(history)
    assert len({h.source_id for h in histories}) == 2
    assert len({h.factor_version_id for h in histories}) == 2
    assert histories[0].output_sha256 != histories[1].output_sha256
    program = StructuredProgram(
        signal_window=DateWindow(start=date(2024, 3, 13), end=date(2024, 3, 14)),
        threshold=0.0,
        top_k=1,
        maximum_weight=0.5,
        maximum_gross_weight=0.5,
    )
    config = StructuredRunConfig(
        structured_strategy_version_id=uuid4(),
        program_sha256=digest(program),
        dataset_version_id=dataset.id,
        dataset_sha256=dataset.content_sha256,
        snapshots=tuple(snapshots),
        execution_window=DateWindow(start=date(2024, 3, 13), end=date(2024, 3, 15)),
        initial_cash=10000.0,
        source_policy=args["policy"],
        engine_image_id=pins[0],
        engine_installed_parent_image_id=pins[1],
        engine_dependency_lock_sha256=dependency_lock_sha256(),
        budget=BudgetVector(
            process_slots=2,
            evaluator_executions=2,
            wall_milliseconds=180000,
            storage_bytes=2 * 268500992,
        ),
    )
    fence, executions = create_structured_acceptance_run(
        factory, owner_id=owner_id, program=program, config=config, settings=settings
    )
    assert len(executions) == 2 and len(set(executions)) == 2
    return factory, fence, executions
