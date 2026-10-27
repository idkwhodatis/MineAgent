"""Real retained configured dataset/run/plan precursor for the022 controller gate.

No fake controller identity, source UUID or label authority is created. Source,
cutoff, snapshot and analysis must still be produced by actual protected units.
"""

import hashlib
import os
import re
from datetime import date
from pathlib import Path

from rd_configured_dataset_fixture import persist_configured_dataset
from sqlalchemy.orm import sessionmaker

from packages.factors.contracts import DateWindow
from packages.qlib_adapter.objective import DirectObjectiveDeclaration
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
from workers.research.rdagent_configured import create_configured_run

ROOT = Path(__file__).resolve().parents[2]


def prepare_configured_generation(engine, settings, owner_id):
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
    factory = sessionmaker(engine, expire_on_commit=False)
    dataset, source, policy = persist_configured_dataset(factory, owner_id, settings)
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
                "lookback_sessions": 2,
                "lag_sessions": 0,
            },
            "task": {
                "name": "configured-close-return",
                "description": "Compute two-prior-session unadjusted close return from retained declared daily source.",
                "formulation": "close[t]/close[t-2]-1",
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
        threshold=0.9,
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
    return factory, fence


def run_configured_generation(engine, settings, owner_id):
    from workers.research.rdagent_configured import execute_configured_controller

    factory, fence = prepare_configured_generation(engine, settings, owner_id)
    from direct_analysis_diagnostic import diagnose_direct_outcomes

    with diagnose_direct_outcomes():
        assert execute_configured_controller(factory, fence, settings) == fence.run_id
    return factory, fence
