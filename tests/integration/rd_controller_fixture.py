"""Real single-day CoSTEER RPC, model ledger, numeric units and snapshot gate.

No precursor process receipts or source publisher fixtures are used here.
Requires all three exact built images and an actual migrated PostgreSQL server.
"""

import hashlib
import os
import re
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.orm import sessionmaker

from backend.app.models import DatasetVersion
from packages.factors.contracts import DateWindow, digest
from packages.factors.fixtures import demo_fixture, demo_inputs
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
from workers.research.rdagent_operational import (
    create_maintained_run,
    execute_maintained_controller,
)

ROOT = Path(__file__).resolve().parents[2]


def prepare_actual_generation(engine, settings, owner_id):
    if os.environ.get("MINEAGENT_RD_CONTROLLER_REQUIRED") != "1":
        pytest.skip("Explicit real controller Docker gate not requested")
    from rd_source_fixture import _config

    images = {
        role: os.environ.get("MINEAGENT_" + role.upper() + "_IMAGE", "")
        for role in ("candidate", "normalizer", "orchestrator")
    }
    assert all(re.fullmatch(r"sha256:[0-9a-f]{64}", image) for image in images.values())
    runtime = RuntimePins(
        **{role + "_image": image for role, image in images.items()},
        # Analysis is not invoked in this factor-generation experiment; its
        # reserved pin is explicit and cannot confer analysis launch authority.
        analysis_image=images["normalizer"],
        dependency_locks=tuple(
            DependencyLock(role=role, sha256=value)
            for role, value in (
                ("analysis", NORMALIZER_LOCK_SHA256),
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
    dataset_id = uuid4()
    sample = demo_fixture(
        factor_version_id=uuid4(), dataset_version_id=dataset_id
    ).manifest.input
    _, observations = demo_inputs()
    manifest = {
        "schema_version": "mineagent.factor-fixture-dataset.v1",
        "files": {
            "observations.json": digest(
                [row.model_dump(mode="json") for row in observations]
            )
        },
        "is_synthetic": True,
        "evaluation_compatibility": False,
    }
    assert digest(manifest) == sample.dataset.content_sha256
    dataset = DatasetVersion(
        id=dataset_id,
        owner_id=owner_id,
        name="Actual controller maintained synthetic dataset",
        market="US",
        start_date=sample.window.start,
        end_date=sample.window.end,
        source="synthetic_fixture",
        is_synthetic=True,
        content_sha256=sample.dataset.content_sha256,
        manifest=manifest,
        storage_key=f"datasets/{owner_id}/{dataset_id}",
    )
    dataset_dir = settings.storage_root / dataset.storage_key
    dataset_dir.mkdir(parents=True, exist_ok=False)
    from packages.factors.contracts import canonical_bytes

    (dataset_dir / "manifest.json").write_bytes(canonical_bytes(manifest))
    (dataset_dir / "observations.json").write_bytes(
        canonical_bytes([row.model_dump(mode="json") for row in observations])
    )
    with factory.begin() as session:
        session.add(dataset)
    config = _config(
        dataset,
        runtime=runtime,
        budget=BudgetVector(
            calls=32,
            tokens=200000,
            mutation_slots=1,
            evaluator_executions=1,
            wall_milliseconds=240000,
            storage_bytes=512 * 1024**2,
            process_slots=4,
        ),
        window=DateWindow(start=date(2024, 3, 15), end=date(2024, 3, 16)),
    )
    body = config.model_dump(mode="json")
    body["task"].update(
        name="maintained-momentum",
        description="Compute the two-prior-session close return on the maintained declared output day.",
        formulation="close[t] / close[t-2] - 1",
    )
    config = FactorResearchConfig.model_validate(body)
    fence = create_maintained_run(
        factory,
        owner_id=owner_id,
        dataset_id=dataset_id,
        config=config,
        settings=settings,
    )
    return factory, fence


def run_actual_generation(engine, settings, owner_id):
    factory, fence = prepare_actual_generation(engine, settings, owner_id)
    from rd_rpc_diagnostic import diagnose_rd_rpc

    with diagnose_rd_rpc():
        assert execute_maintained_controller(factory, fence, settings) == fence.run_id
    return factory, fence
