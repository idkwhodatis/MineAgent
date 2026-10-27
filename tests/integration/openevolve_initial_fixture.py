"""Maintained real OE inputs/DB precursor; not normal product submission.

All candidate/score/checkpoint evidence is produced by run_initial_epoch, never
inserted here. The only fixture model responses are two bounded source diffs.
This module performs no source execution, dependency installation or network IO.
"""

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import null, text
from sqlalchemy.orm import sessionmaker

from backend.app.models import (
    DatasetVersion,
    ExecutionAttempt,
    ResearchRun,
    StrategyVersion,
    utcnow,
)
from backend.app.openevolve_broker_rpc import FixtureSchedule
from backend.app.openevolve_models import (
    SearchResolvedExecutionProfile,
    StrategySearchSpec,
)
from backend.app.research_broker_models import ResearchBudgetControl
from packages.openevolve_adapter.checkpoint_native import canonical
from packages.openevolve_adapter.contracts import (
    DevelopmentWindow,
    InputFile,
    NoFactors,
    ObjectiveProfile,
    SearchSpec,
    SeedPreparation,
    SourceBinding,
    StrategyExecutionPlan,
)
from packages.openevolve_adapter.execution_inputs import ResolvedNativeInputs
from packages.openevolve_adapter.operational_profile import require_initial_profile
from packages.openevolve_adapter.source import region, validate_descendant
from packages.research_runtime.broker_contracts import digest
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.engine_broker_contracts import OEChatResponse
from packages.research_runtime.fences import AttemptFence
from packages.tradegrad_adapter.config import MarketConfig, WarmupConfig
from packages.tradegrad_adapter.native_contract import NativeStrategyContract
from runtimes.openevolve_evaluator.recipe import wrapper_inventory_sha256
from runtimes.openevolve_operational.runtime_binding import NativeRuntimeBinding
from tests.tradegrad.test_config_windows import _bundle
from workers.openevolve.staging import calendar_projection, render_search_input

ROOT = Path(__file__).resolve().parents[2]
REVISION = "024_openevolve_initial"


def required_images():
    if os.environ.get("MINEAGENT_OE_INITIAL_REQUIRED") != "1":
        raise AssertionError("Actual initial epoch requires explicit dedicated gate")
    images = tuple(
        os.environ.get(name, "")
        for name in (
            "MINEAGENT_OE_CONTROLLER_IMAGE",
            "MINEAGENT_OE_NATIVE_IMAGE",
            "MINEAGENT_OE_NATIVE_PARENT_IMAGE",
        )
    )
    if not all(re.fullmatch(r"sha256:[a-f0-9]{64}", image) for image in images):
        raise AssertionError("Three actual built image IDs required")
    controller, protected, parent = images
    runtime = NativeRuntimeBinding(
        native_parent_image_id=parent,
        protected_image_id=protected,
        dependency_lock_sha256=hashlib.sha256(
            (ROOT / "runtimes/evaluator/requirements.lock").read_bytes()
        ).hexdigest(),
        wrapper_inventory_sha256=wrapper_inventory_sha256(),
    )
    return controller, runtime


def marked_seed():
    raw = (ROOT / "seed_strategies/research_cross.py").read_bytes()
    first = b'        "lookback": 3,\n'
    last = b'        "cash_reserve": 0.05,\n'
    assert raw.count(first) == raw.count(last) == 1
    raw = raw.replace(first, b"        # EVOLVE-BLOCK-START\n" + first)
    raw = raw.replace(last, last + b"        # EVOLVE-BLOCK-END\n")
    region(raw)  # Canonical source/markers are genuinely present before ownership.
    assert len(raw) <= 32768
    return raw


def fixture_schedule(seed):
    pairs = (
        ('        "gross_target": 0.8,', '        "gross_target": 0.6,'),
        ('        "cash_reserve": 0.05,', '        "cash_reserve": 0.10,'),
    )
    responses = []
    for old, new in pairs:
        assert seed.decode().count(old) == 1
        validate_descendant(seed.replace(old.encode(), new.encode()), seed)
        responses.append(
            OEChatResponse(
                content=f"<<<<<<< SEARCH\n{old}\n=======\n{new}\n>>>>>>> REPLACE\n"
            )
        )
    return FixtureSchedule(tuple(responses))


def maintained_dataset():
    _legacy, manifest, files = _bundle()
    manifest["price_semantics"] = {"adjustment": "unadjusted", "currency": "CNY"}
    # Replace the pure staging fixture's placeholder universe digest with an
    # explicit retained membership declaration, not a claimed external authority.
    definition = {
        "schema_version": "mineagent.maintained-membership.v1",
        "listing_ids": sorted(manifest["evaluator_symbol_mapping"]),
        "membership_files": {
            name: sha
            for name, sha in manifest["files"].items()
            if name.startswith("universe/")
        },
        "history": "synthetic_maintained_fixture_not_verified_pit",
    }
    manifest["universe"] = {
        "id": "maintained-cn-membership.v1",
        "content_sha256": digest(definition),
        "state": "historical_membership",
        "definition": definition,
    }
    return manifest, files


@dataclass(frozen=True)
class InitialFixture:
    factory: object
    fence: AttemptFence
    spec: SearchSpec
    native: ResolvedNativeInputs
    runtime: NativeRuntimeBinding
    seed: bytes
    schedule: FixtureSchedule


def prepare_initial_search(
    engine, settings, owner_id, *, controller_image, runtime, profile_commitments
):
    """One actual owned precursor transaction, no candidate/result/stop insertion.

    profile_commitments must come from the reviewed operational build inventory;
    this helper never invents a runtime hash or uses the pure test's a*64 pins.
    """
    required = {"runtime_lock_sha256", "adapter_patch_sha256", "prompt_profile_sha256"}
    if set(profile_commitments) != required or not all(
        re.fullmatch(r"[a-f0-9]{64}", v) for v in profile_commitments.values()
    ):
        raise ValueError("Exact reviewed runtime commitments required")
    factory = sessionmaker(engine, expire_on_commit=False)
    owner, run_id, attempt_id, dataset_id, strategy_id = (
        owner_id,
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    manifest, files = maintained_dataset()
    seed = marked_seed()
    source_sha = hashlib.sha256(seed).hexdigest()
    dataset_sha = hashlib.sha256(canonical(manifest)).hexdigest()
    storage_key = f"datasets/{owner}/{dataset_id}"
    directory = settings.storage_root / storage_key
    directory.mkdir(parents=True, mode=0o700)
    for name, raw in {**files, "manifest.json": canonical(manifest)}.items():
        destination = directory / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(raw)
    native = ResolvedNativeInputs(
        task="cn_cross",
        universe_id=manifest["universe"]["id"],
        market_profile=MarketConfig.for_market("CN"),
        warmup=WarmupConfig(sessions=3),
        strategy_contract=NativeStrategyContract(
            lookback_bars=3, warmup_bars=3, requires_shorting=False
        ),
    )
    window = DevelopmentWindow(
        id="dev",
        start=date(2024, 1, 5),
        end=date(2024, 1, 8),
        weight_millionths=1000000,
    )
    plan = StrategyExecutionPlan(
        dataset_version_id=dataset_id,
        dataset_sha256=dataset_sha,
        files=tuple(
            InputFile(
                logical_name=f"input_{i}",
                sha256=hashlib.sha256(raw).hexdigest(),
                size_bytes=len(raw),
            )
            for i, (_, raw) in enumerate(sorted(files.items()))
        ),
        universe_sha256=manifest["universe"]["content_sha256"],
        listing_map_sha256=digest(manifest["evaluator_symbol_mapping"]),
        calendar_sha256=digest(calendar_projection(manifest)),
        price_semantics_sha256=digest(manifest["price_semantics"]),
        engine_config_sha256=digest(native.execution_projection()),
        parameter_sha256=digest([]),
        state_schema_sha256=digest({"state_schema": native.state_schema}),
        capture_profile_sha256=digest(native.capture.materialize()),
        evaluator_image=runtime.protected_image_id,
        market="CN",
        warmup_sessions=3,
        rng_seed=42,
        windows=(window,),
    )
    source_manifest = {
        "schema_version": "mineagent.maintained-oe-seed.v1",
        "source_sha256": source_sha,
        "task": "cn_cross",
        "prepared": "already_marked_owned_source",
    }
    binding = SourceBinding(
        strategy_version_id=strategy_id,
        source_sha256=source_sha,
        manifest_sha256=digest(source_manifest),
    )
    budget = BudgetVector(
        calls=2,
        tokens=262144,
        mutation_slots=2,
        evaluator_executions=3,
        wall_milliseconds=1200000,
        storage_bytes=2 * 1024**3,
        process_slots=4,
    )
    spec = SearchSpec(
        owner_id=owner,
        run_id=run_id,
        preparation=SeedPreparation(kind="none", original=binding, prepared=binding),
        execution=plan,
        objective=ObjectiveProfile(windows=(window,)),
        factors=NoFactors(),
        runtime_image=controller_image,
        **profile_commitments,
        budget=budget,
        maximum_mutation_slots=2,
        population_size=8,
        archive_size=8,
    )
    require_initial_profile(spec, native)
    # Real fixed-data staging preflight, not an evaluator or source execution.
    render_search_input(
        plan=plan,
        inputs=native,
        window=window,
        manifest=manifest,
        storage_root=settings.storage_root,
        dataset_storage_key=storage_key,
        source=seed,
        source_sha256=source_sha,
        max_bytes=64 * 1024**2,
    )
    token = uuid4()
    with factory.begin() as session:
        assert session.scalars(
            text("SELECT version_num FROM alembic_version")
        ).all() == [REVISION]
        session.add(
            DatasetVersion(
                id=dataset_id,
                owner_id=owner,
                name="Maintained OE native data",
                market="CN",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 13),
                source="synthetic_fixture",
                is_synthetic=True,
                content_sha256=dataset_sha,
                manifest=manifest,
                storage_key=storage_key,
            )
        )
        session.add(
            StrategyVersion(
                id=strategy_id,
                owner_id=owner,
                name="Maintained marked native seed",
                task="cn_cross",
                mode="native_akquant",
                content_sha256=source_sha,
                code=seed.decode(),
                manifest=source_manifest,
                parameters={},
            )
        )
        session.flush()
        run = ResearchRun(
            id=run_id,
            owner_id=owner,
            idempotency_key="oe-initial-" + run_id.hex,
            request_sha256=spec.sha256,
            config=spec.model_dump(mode="json"),
            purpose="strategy_search",
            engine="openevolve",
            dataset_version_id=dataset_id,
            strategy_version_id=strategy_id,
            submission_binding=null(),
            status="running",
            generation=1,
            research_image_digest=controller_image,
        )
        session.add(run)
        session.flush()
        session.add(
            ExecutionAttempt(
                id=attempt_id,
                run_id=run_id,
                generation=1,
                worker_id="maintained-actual-oe-initial",
                lease_token=token,
                lease_expires_at=utcnow() + timedelta(seconds=1200),
                status="running",
                sandbox_id="not-launched:" + attempt_id.hex,
                sandbox_stopped=False,
            )
        )
        session.flush()
        run.active_attempt_id = attempt_id
        session.add(
            StrategySearchSpec(
                owner_id=owner,
                run_id=run_id,
                spec_sha256=spec.sha256,
                spec=spec.model_dump(mode="json"),
                original_strategy_id=strategy_id,
                original_source_sha256=source_sha,
                prepared_strategy_id=strategy_id,
                prepared_source_sha256=source_sha,
                dataset_id=dataset_id,
                dataset_sha256=dataset_sha,
                objective_sha256=digest(spec.objective),
                execution_sha256=digest(plan),
            )
        )
        session.flush()
        session.add(
            SearchResolvedExecutionProfile(
                owner_id=owner,
                run_id=run_id,
                execution_sha256=digest(plan),
                profile_sha256=digest(native),
                profile=native.model_dump(mode="json"),
                profile_canonical=canonical(native.model_dump(mode="json")).decode(),
            )
        )
        session.add(
            ResearchBudgetControl(
                id=uuid4(),
                owner_id=owner,
                run_id=run_id,
                limits=budget.model_dump(),
                used=BudgetVector().model_dump(),
                reserved=BudgetVector().model_dump(),
            )
        )
    return InitialFixture(
        factory,
        AttemptFence(owner, run_id, attempt_id, 1, token),
        spec,
        native,
        runtime,
        seed,
        fixture_schedule(seed),
    )


def runtime_commitments():
    """Verify reviewed recipe bytes, never trust an arbitrary caller hash."""
    from runtimes.openevolve_operational.recipe_pins import PINS

    result = {}
    for key, (path, expected) in PINS.items():
        actual = hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        assert actual == expected
        result[key] = actual
    return result


def assert_completed_epoch(factory, fence, settings):
    """Reread actual immutable objects after stop; no current launch authority."""
    import json
    import math

    from sqlalchemy import select

    from backend.app import openevolve_models as m
    from backend.app.research_broker_models import (
        ResearchBudgetReservation,
        ResearchModelOperation,
    )
    from backend.app.research_object_models import ResearchObject
    from backend.app.research_process_models import (
        ResearchProcessAdmission,
        ResearchProcessState,
    )
    from backend.app.storage import read_verified
    from packages.openevolve_adapter.host_contracts import (
        CheckpointFileEnvelope,
        SourceEnvelope,
    )
    from packages.openevolve_adapter.objective import native_observation, score

    with factory() as session:

        def rows(model):
            return session.scalars(select(model).filter_by(run_id=fence.run_id)).all()

        def object_bytes(identity):
            obj = session.get(ResearchObject, identity)
            assert obj.owner_id == fence.owner_id
            return read_verified(
                settings.storage_root, obj.storage_key, obj.sha256, obj.size_bytes
            )

        run = session.get(ResearchRun, fence.run_id)
        attempt = session.get(ExecutionAttempt, fence.attempt_id)
        assert run.status == attempt.status == "succeeded"
        assert attempt.sandbox_stopped is True and attempt.finished_at is not None
        spec = SearchSpec.model_validate_json(
            canonical(session.get(StrategySearchSpec, run.id).spec)
        )
        candidates = rows(m.SearchCandidate)
        assert len(candidates) == 3
        assert sorted(c.kind for c in candidates) == ["mutation", "mutation", "seed"]
        assert len({c.source_sha256 for c in candidates}) == 3
        by_id = {c.id: c for c in candidates}
        sources = {}
        for c in candidates:
            envelope = SourceEnvelope.model_validate_json(
                object_bytes(c.source_object_id)
            )
            assert envelope.source_sha256 == c.source_sha256
            sources[c.id] = envelope.source.encode()
        seed = next(c for c in candidates if c.kind == "seed")
        assert sources[seed.id] == marked_seed()
        for c in candidates:
            if c.kind == "mutation":
                assert (
                    by_id[c.parent_candidate_id].source_sha256 == c.parent_source_sha256
                )
                validate_descendant(sources[c.id], sources[c.parent_candidate_id])
        evaluations = rows(m.SearchEvaluation)
        outcomes = rows(m.SearchEvaluationOutcome)
        objectives = rows(m.SearchObjectiveReceipt)
        assert len(evaluations) == len(outcomes) == len(objectives) == 3
        for evaluation in evaluations:
            outcome = next(o for o in outcomes if o.evaluation_id == evaluation.id)
            assert (
                outcome.status == "observed"
                and outcome.bundle_kind == "search_native_observation"
            )
            evidence = json.loads(object_bytes(outcome.bundle_object_id))
            observed = native_observation(
                evidence["bundle"],
                window_id=evaluation.window_id,
                evaluation_id=evaluation.id,
                expected_bundle_sha256=outcome.native_bundle_sha256,
            )
            expected = score(spec.objective, (observed,))
            actual = next(
                o for o in objectives if o.candidate_id == evaluation.candidate_id
            )
            assert expected.eligible and actual.status == "eligible"
            assert math.isfinite(actual.combined_score)
            assert actual.combined_score == expected.combined_score
        terminals = sorted(rows(m.SearchTerminalApplication), key=lambda r: r.frontier)
        assert [r.frontier for r in terminals] == [0, 1, 2]
        saves = sorted(rows(m.SearchCheckpointIntent), key=lambda r: r.save_ordinal)
        assert [r.save_ordinal for r in saves] == [1, 2, 3, 4]
        assert [r.frontier for r in saves] == [0, 1, 2, 2]
        publications = rows(m.SearchCheckpointPublication)
        assert len(publications) == 4
        files = rows(m.SearchCheckpointFile)
        final_infos = []
        for intent in saves[-2:]:
            publication = next(p for p in publications if p.intent_id == intent.id)
            item = next(
                f
                for f in files
                if f.publication_id == publication.id
                and f.name == "best_program_info.json"
            )
            envelope = CheckpointFileEnvelope.model_validate_json(
                object_bytes(item.object_id)
            )
            assert envelope.sha256 == item.file_sha256
            final_infos.append(json.loads(envelope.bytes()))
        assert final_infos[0]["saved_at"] != final_infos[1]["saved_at"]
        assert saves[-2].id != saves[-1].id
        operations = rows(ResearchModelOperation)
        assert len(operations) == 2 and all(o.state == "completed" for o in operations)
        holds = rows(ResearchBudgetReservation)
        assert holds and all(h.state == "settled" for h in holds)
        admissions = rows(ResearchProcessAdmission)
        assert sorted(a.role for a in admissions) == ["native_evaluator"] * 3 + [
            "openevolve_orchestrator"
        ]
        assert all(
            session.get(ResearchProcessState, a.id).status == "stopped"
            for a in admissions
        )
        control = session.scalar(select(ResearchBudgetControl).filter_by(run_id=run.id))
        assert control.reserved == BudgetVector().model_dump()
        assert control.used["calls"] == 2 and control.used["mutation_slots"] == 2
        assert (
            control.used["evaluator_executions"] == 3
            and control.used["process_slots"] == 4
        )
        return {
            "run_id": str(run.id),
            "candidates": 3,
            "model_calls": 2,
            "native_evaluations": 3,
            "checkpoint_save_ordinals": [1, 2, 3, 4],
            "checkpoint_frontiers": [0, 1, 2, 2],
            "all_processes_stopped": True,
        }
