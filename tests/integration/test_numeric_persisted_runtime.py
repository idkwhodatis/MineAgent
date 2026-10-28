"""Required composed PostgreSQL+Docker maintained numeric execution.

Controller source-generation precursor is explicitly storage-only. Actual
candidate and normalizer units must run and stop; no mocked kernel/container,
source resolver, native publication or positive-stop receipt can satisfy these.
"""

import hashlib
import json
import os
import re
import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from backend.app.models import ExecutionAttempt, utcnow
from backend.app.numeric_process_services import read_numeric_output
from backend.app.research_lifecycle import neutral_transaction
from backend.app.research_process_models import (
    ResearchProcessAdmission,
    ResearchProcessReceipt,
    ResearchProcessState,
)
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
from packages.research_runtime.process_contracts import ProcessError
from runtimes.factor_execution.acceptance import (
    EXPECTED_PARQUET_SHA256,
    frozen_inputs,
)
from workers.research.numeric_fixture import (
    execute_fixed_candidate_from_source,
    execute_fixed_normalizer_from_native,
    maintained_projection,
)
from workers.research.numeric_process import (
    recover_numeric_unit,
    run_registered_numeric_unit,
)

pytestmark = pytest.mark.postgres


@pytest.fixture
def numeric_source(migrated_engine, settings, owner):
    if os.environ.get("MINEAGENT_PERSISTED_NUMERIC_REQUIRED") != "1":
        pytest.skip("Explicit real persisted numeric Docker gate was not requested")
    images = {
        role: os.environ.get("MINEAGENT_NUMERIC_" + role.upper() + "_IMAGE", "")
        for role in ("candidate", "normalizer")
    }
    assert all(
        re.fullmatch(r"sha256:[0-9a-f]{64}", value) for value in images.values()
    ), "Pinned actual numeric fixture images required"
    from rd_source_fixture import persist_maintained_source

    runtime = RuntimePins(
        candidate_image=images["candidate"],
        normalizer_image=images["normalizer"],
        analysis_image="sha256:" + "b" * 64,
        orchestrator_image="sha256:" + "b" * 64,
        dependency_locks=tuple(
            DependencyLock(
                role=role,
                sha256={
                    "candidate": CANDIDATE_LOCK_SHA256,
                    "normalizer": NORMALIZER_LOCK_SHA256,
                }.get(role, "a" * 64),
            )
            for role in ("analysis", "candidate", "normalizer", "orchestrator")
        ),
    )
    factory = sessionmaker(migrated_engine, expire_on_commit=False)
    fixed = (
        Path(__file__).resolve().parents[2] / "runtimes/factor_execution/abi_fixture.py"
    ).read_bytes()
    from uuid import uuid4

    from backend.app.models import DatasetVersion
    from packages.factors.contracts import digest
    from packages.factors.fixtures import demo_fixture, demo_inputs

    dataset_id = uuid4()
    sample = demo_fixture(
        factor_version_id=uuid4(), dataset_version_id=dataset_id
    ).manifest.input
    _calendar, observations = demo_inputs()
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
    with factory() as session:
        dataset = DatasetVersion(
            id=dataset_id,
            owner_id=owner.user.id,
            name="Maintained numeric synthetic inputs",
            market="US",
            start_date=sample.window.start,
            end_date=sample.window.end,
            source="synthetic_fixture",
            is_synthetic=True,
            content_sha256=sample.dataset.content_sha256,
            manifest=manifest,
            storage_key="datasets/maintained-" + str(dataset_id),
        )
        session.add(dataset)
        session.commit()
    source = persist_maintained_source(
        factory,
        owner=owner.user.id,
        dataset=dataset,
        settings=settings,
        source_utf8=fixed,
        runtime=runtime,
        budget=BudgetVector(
            calls=8,
            tokens=100000,
            mutation_slots=1,
            evaluator_executions=1,
            wall_milliseconds=240000,
            storage_bytes=256 * 1024**2,
            process_slots=4,
        ),
    )
    return factory, settings, source


def _candidate(factory, settings, source):
    return execute_fixed_candidate_from_source(
        factory,
        source.fence,
        settings,
        source_id=source.source.id,
        factor_version_id=source.version.id,
        code_object_id=source.codeobject.id,
    )


def _receipts(factory, admission_id):
    with factory() as session:
        state = session.get(ResearchProcessState, admission_id)
        assert state.status == "stopped"
        rows = session.scalars(
            select(ResearchProcessReceipt).where(
                ResearchProcessReceipt.admission_id == admission_id
            )
        ).all()
        by_kind = {row.kind: row for row in rows}
        if "extraction" in by_kind:
            from packages.research_runtime.numeric_process_journal import (
                NumericIntent,
                NumericStop,
            )
            from packages.research_runtime.process_contracts import canonical

            intent = NumericIntent.model_validate_json(
                canonical(by_kind["launch_intent"].payload)
            )
            stop = NumericStop.model_validate_json(canonical(by_kind["stop"].payload))
            assert stop.deadline_bound_met is True
            assert (
                intent.issued_at
                <= stop.observed_at
                <= intent.not_after + timedelta(seconds=1)
            )
        return {row.kind: (row.id, row.payload_sha256) for row in rows}


def _assert_persisted_oracle_inputs(
    config, factor_version_id, actual_inputs, expected_inputs
):
    """Full manifest equality plus independent fixed market/source bytes."""
    for path in (
        "/input/source.py",
        "/input/observations.json",
        "/input/listings.json",
    ):
        assert actual_inputs[path] == expected_inputs[path]
    actual_manifest = json.loads(actual_inputs["/input/manifest.json"])
    expected_manifest = json.loads(expected_inputs["/input/manifest.json"])
    # The independent primitive oracle keeps its original tight limits. Bind only
    # declaration-dependent fields to the actual immutable persisted config;
    # never derive expectations from the production projection under test.
    from packages.factors.contracts import ExposureHistory, digest
    from packages.rdagent_adapter.host_evidence import research_snapshot_input

    snapshot = research_snapshot_input(
        config,
        factor_version_id=factor_version_id,
        code_sha256=hashlib.sha256(expected_inputs["/input/source.py"]).hexdigest(),
        exposure_history=ExposureHistory(),
    )
    expected_manifest["snapshot_input_sha256"] = digest(snapshot)
    expected_manifest["max_output_rows"] = config.max_output_rows_per_cutoff
    expected_manifest["max_output_bytes"] = config.max_output_bytes_per_cutoff
    assert actual_manifest == expected_manifest


def test_actual_source_candidate_native_claim_normalizer_stops(
    numeric_source, monkeypatch
):
    from backend.app import factor_native_services
    from backend.app.factor_native_models import (
        NativeHDFOutcome,
        NativeHDFPublication,
        NativeNormalizerInput,
    )
    from backend.app.factor_native_services import publish_completed_candidate
    from backend.app.rdagent_models import FactorCutoffExecution
    from backend.app.research_broker_models import ResearchBudgetReservation

    factory, settings, source = numeric_source
    # Bind the inherited independently calculated three-row oracle to this real
    # source graph's inputs. Random source/dataset identities change only the
    # snapshot commitment, never the fixed numeric inputs or Parquet schema.
    config = FactorResearchConfig.model_validate(source.spec.config)
    _expected_prefix, expected_inputs, _event_ns = frozen_inputs(
        config.runtime.candidate_image, config.runtime.normalizer_image
    )
    _actual_prefix, actual_inputs = maintained_projection(
        config,
        factor_version_id=source.version.id,
        source_utf8=expected_inputs["/input/source.py"],
    )
    _assert_persisted_oracle_inputs(
        config, source.version.id, actual_inputs, expected_inputs
    )
    candidate_id = _candidate(factory, settings, source)
    before = _receipts(factory, candidate_id)
    assert set(before) == {"launch_intent", "launch_identity", "stop", "extraction"}
    assert recover_numeric_unit(factory, settings.storage_root, candidate_id)
    assert _receipts(factory, candidate_id) == before

    class InjectedPublicationCrash(Exception):
        pass

    # Real files and durable pre-admission precede this forced finalize crash;
    # no successful outcome or released charge may appear after rollback.
    with monkeypatch.context() as patch:

        def crash_before_finalize(*_args, **_kwargs):
            raise InjectedPublicationCrash

        patch.setattr(factor_native_services, "_finalize", crash_before_finalize)
        with pytest.raises(InjectedPublicationCrash):
            publish_completed_candidate(factory, source.fence, candidate_id, settings)
    with factory() as session:
        pending = session.scalar(
            select(NativeHDFPublication).where(
                NativeHDFPublication.admission_id == candidate_id
            )
        )
        assert pending is not None
        assert session.get(NativeHDFOutcome, pending.id) is None
        assert (
            session.get(ResearchBudgetReservation, pending.reservation_id).state
            == "reserved"
        )
        pending_id = pending.id
    publication_id = publish_completed_candidate(
        factory, source.fence, candidate_id, settings
    )
    assert publication_id == pending_id
    assert (
        publish_completed_candidate(factory, source.fence, candidate_id, settings)
        == publication_id
    )
    normalizer_id = execute_fixed_normalizer_from_native(
        factory,
        source.fence,
        settings,
        publication_id=publication_id,
        primitive_report=True,
    )
    assert set(_receipts(factory, normalizer_id)) == set(before)
    with neutral_transaction(
        factory, source.fence, resource_scope="processes"
    ) as session:
        receipt, raw = read_numeric_output(
            session, source.fence, normalizer_id, storage_root=settings.storage_root
        )
        assert receipt.basename == "values.parquet" and len(raw) > 0
        # No host HDF/Parquet parser: fixed PyArrow 26 encoding of the three
        # independently hand-calculated momentum rows, not a self-hash oracle.
        assert receipt.sha256 == hashlib.sha256(raw).hexdigest()
        assert receipt.sha256 == EXPECTED_PARQUET_SHA256
        assert receipt.inputs.source_object_id == source.codeobject.id
        assert receipt.inputs.source_sha256 == source.version.code_sha256
    from backend.app.core.maintenance import shared_lifecycle
    from backend.app.factor_native_io import native_storage_guard
    from backend.app.normalized_cutoff_services import read_normalized_cutoff

    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        native_storage_guard(settings.storage_root),
        neutral_transaction(
            factory, source.fence, resource_scope="factor_numeric"
        ) as session,
    ):
        result = read_normalized_cutoff(session, source.fence, normalizer_id, settings)
        # Independently calculated from the fixed three prices per listing.
        assert result.rows == (
            (1710532800000000000, 0, 132.0 / 130.0 - 1, 0),
            (1710532800000000000, 1, 126.0 / 120.0 - 1, 0),
            (1710532800000000000, 2, 108.0 / 104.0 - 1, 0),
        )
        assert result.parquet_bytes == raw
        assert result.parquet_sha256 == EXPECTED_PARQUET_SHA256
        assert result.native_publication_id == publication_id
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        native_storage_guard(settings.storage_root),
        neutral_transaction(
            factory, source.fence, resource_scope="factor_numeric"
        ) as session,
    ):
        # Fresh re-resolution uses retained completed result, never another launch.
        assert (
            read_normalized_cutoff(session, source.fence, normalizer_id, settings)
            == result
        )
    from backend.app.execution_snapshot_models import ExecutionSnapshotPublication
    from backend.app.execution_snapshot_services import publish_execution_snapshot
    from backend.app.services import DomainError

    # This maintained test executes the last cutoff of a multi-day window.
    # Correct primitive rows for that one day must never become full evaluation
    # success or a published complete snapshot for the unexecuted other cutoffs.
    with pytest.raises(DomainError) as incomplete:
        publish_execution_snapshot(
            factory, source.fence, result.evaluation_id, settings
        )
    assert incomplete.value.code == "factor_evaluation_incomplete"
    with factory() as session:
        assert (
            session.scalar(
                select(ExecutionSnapshotPublication).where(
                    ExecutionSnapshotPublication.evaluation_id == result.evaluation_id
                )
            )
            is None
        )
        candidate = session.get(ResearchProcessAdmission, candidate_id)
        normalizer = session.get(ResearchProcessAdmission, normalizer_id)
        cutoff = session.get(FactorCutoffExecution, candidate.logical_operation_id)
        assert cutoff.candidate_admission_id == candidate_id
        assert normalizer.logical_operation_id == cutoff.id
        assert candidate.ticket_sha256 is normalizer.ticket_sha256 is None
        claim = session.scalar(
            select(NativeNormalizerInput).where(
                NativeNormalizerInput.admission_id == normalizer_id
            )
        )
        assert claim.publication_id == publication_id
        # Never fabricate actual controller containment or a successful full run.
        assert (
            session.get(ResearchProcessState, source.controller_id).status == "admitted"
        )
    _assert_second_native_consumer_rejected(
        factory, settings, source.fence, publication_id, normalizer_id
    )
    with pytest.raises(ProcessError, match="catalog_not_registered"):
        run_registered_numeric_unit(candidate_id)


def _assert_second_native_consumer_rejected(
    factory, settings, fence, publication_id, first_id
):
    from uuid import uuid4

    from sqlalchemy.exc import IntegrityError

    from backend.app.core.maintenance import shared_lifecycle
    from backend.app.factor_native_io import native_storage_guard
    from backend.app.native_normalizer_claim import claim_native_normalizer
    from backend.app.numeric_process_services import persist_numeric_intent
    from backend.app.research_broker_services import reserve_budget
    from packages.research_runtime.numeric_process_journal import NumericIntent

    with factory() as session:
        row = session.scalar(
            select(ResearchProcessReceipt).where(
                ResearchProcessReceipt.admission_id == first_id,
                ResearchProcessReceipt.kind == "launch_intent",
            )
        )
        payload = dict(row.payload)
    attempted_id = uuid4()
    # A genuine separately reserved/admitted prospective consumer, not a forged
    # positive output. The immediate publication UNIQUE must reject it before
    # any launch claim/Docker mutation; the entire second admission rolls back.
    with (
        pytest.raises(IntegrityError) as rejected,
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        native_storage_guard(settings.storage_root),
        neutral_transaction(factory, fence, resource_scope="factor_numeric") as session,
    ):
        reservation = reserve_budget(
            session,
            fence,
            purpose="process_unit",
            settings=settings,
            logical_key="numeric-duplicate:" + uuid4().hex,
            amounts=BudgetVector(
                process_slots=1, wall_milliseconds=60000, storage_bytes=67_174_400
            ),
        )
        admission = dict(payload["admission"])
        admission.update(
            id=str(attempted_id),
            reservation_id=str(reservation.id),
            launch_ordinal=2,
        )
        now = utcnow()
        payload.update(
            id=str(uuid4()),
            admission=admission,
            issued_at=now.isoformat(),
            not_after=(now + timedelta(seconds=8)).isoformat(),
        )
        # The retained receipt is JSON, not a strict Python model dictionary.
        intent = NumericIntent.model_validate_json(json.dumps(payload, allow_nan=False))
        persist_numeric_intent(session, fence, intent)
        claim_native_normalizer(session, fence, publication_id, attempted_id, settings)
    assert (
        rejected.value.orig.diag.constraint_name
        == "factor_native_normalizer_inputs_publication_id_key"
    )
    with factory() as session:
        assert session.get(ResearchProcessAdmission, attempted_id) is None


def test_actual_lost_fence_keeps_containment_without_output_import(numeric_source):
    factory, settings, source = numeric_source
    failures = []

    def actor():
        try:
            _candidate(factory, settings, source)
        except Exception as exc:  # noqa: BLE001 - capture background failure type, never credentials
            failures.append(type(exc).__name__)

    thread = threading.Thread(target=actor, daemon=True)
    thread.start()
    admission_id = None
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        with factory() as session:
            row = session.scalar(
                select(ResearchProcessAdmission)
                .join(
                    ResearchProcessState,
                    ResearchProcessState.admission_id == ResearchProcessAdmission.id,
                )
                .where(
                    ResearchProcessAdmission.run_id == source.fence.run_id,
                    ResearchProcessAdmission.role == "factor_cutoff_candidate",
                    ResearchProcessState.status == "running",
                )
            )
            if row is not None:
                admission_id = row.id
                attempt = session.get(
                    ExecutionAttempt, source.fence.attempt_id, with_for_update=True
                )
                attempt.lease_expires_at = utcnow() - timedelta(seconds=1)
                session.commit()
                break
        if not thread.is_alive():
            break
        time.sleep(0.01)
    thread.join(20)
    assert not thread.is_alive(), (
        "Numeric actor failed to finish within bounded unit deadline"
    )
    assert admission_id is not None, "Actual running unit was never durably observed"
    assert failures, "A lost fence must not report successful output publication"
    assert recover_numeric_unit(factory, settings.storage_root, admission_id)
    assert set(_receipts(factory, admission_id)) == {
        "launch_intent",
        "launch_identity",
        "stop",
    }
