"""Explicit maintained source/DB/Docker acceptance, never production registration.

This entrypoint executes only repository-maintained ABI fixture bytes resolved
from the real RD source graph. Operator image IDs are test inputs, not runtime
acceptance or an installed production catalog. No API/dispatcher imports it.
"""

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from backend.app.core.maintenance import shared_lifecycle
from backend.app.numeric_process_services import persist_numeric_intent
from backend.app.research_broker_services import reserve_budget
from backend.app.research_lifecycle import neutral_transaction
from packages.factors.contracts import ExposureHistory
from packages.factors.fixtures import demo_fixture, demo_inputs
from packages.rdagent_adapter.contracts import FactorResearchConfig
from packages.rdagent_adapter.host_evidence import research_snapshot_input
from packages.rdagent_adapter.numeric_profile import (
    CANDIDATE_LOCK_SHA256,
    NORMALIZER_LOCK_SHA256,
    candidate_inputs,
)
from packages.rdagent_adapter.prefix import MarketObservation, project_prefix
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.numeric_process_journal import (
    NumericInput,
    NumericInputFile,
    NumericIntent,
)
from packages.research_runtime.process_contracts import ProcessError, digest
from packages.research_runtime.process_declarations_v2 import (
    NoIPC,
    ProcessDeclarationV2,
)
from runtimes.factor_execution.verify_inventory import (
    verify as verify_runtime_inventory,
)
from runtimes.research_supervisor.numeric_supervisor import NumericSupervisor
from workers.research.numeric_process import _execute_committed_numeric_unit


def maintained_projection(config, *, factor_version_id, source_utf8, snapshot=None):
    """Known fixture market rows projected against the persisted research config."""
    snapshot = snapshot or research_snapshot_input(
        config,
        factor_version_id=factor_version_id,
        code_sha256=hashlib.sha256(source_utf8).hexdigest(),
        exposure_history=ExposureHistory(),
    )
    # This explicit maintained provider serves only its known synthetic bytes.
    # An arbitrary owned DatasetVersion hash is not evidence for those bytes.
    maintained = demo_fixture(
        factor_version_id=factor_version_id,
        dataset_version_id=config.input.dataset.dataset_version_id,
    ).manifest.input
    if (
        config.input.dataset != maintained.dataset
        or config.input.calendar_sha256 != maintained.calendar_sha256
        or config.input.market_timezone != maintained.market_timezone
        or config.input.calculation_universe != maintained.calculation_universe
        or config.input.input_fields != maintained.definition.input_fields
        or not maintained.window.start
        <= config.input.window.start
        < config.input.window.end
        <= maintained.window.end
    ):
        raise ProcessError("numeric_fixture_dataset_identity")
    calendar, prices = demo_inputs()
    if calendar.sha256 != config.input.calendar_sha256:
        raise ProcessError("numeric_fixture_calendar_identity")
    days = {day.session_date: day for day in calendar.sessions}
    observations = tuple(
        MarketObservation(
            listing_id=row.listing_id,
            session=row.session_date,
            event_time=days[row.session_date].close_time,
            available_at=row.available_at,
            values=(row.close,),
        )
        for row in prices
    )
    day = [
        day
        for day in calendar.sessions
        if snapshot.window.start <= day.session_date < snapshot.window.end
    ][-1]
    prefix, projected = project_prefix(
        snapshot,
        host_cutoff=day.close_time,
        output_event_time=day.close_time,
        output_session=day.session_date,
        calendar_sessions=tuple(days),
        observations=observations,
        candidate_image=config.runtime.candidate_image,
        normalizer_image=config.runtime.normalizer_image,
        lag_available_at=day.close_time,
        max_output_rows=3,
        max_output_bytes=10000,
    )
    _, inputs, _ = candidate_inputs(
        prefix,
        snapshot_input=snapshot,
        source=source_utf8,
        expected_source_sha256=snapshot.definition.code_sha256,
        projected_files=projected,
    )
    return prefix, dict(inputs)


def execute_fixed_candidate_from_source(
    factory, fence, settings, *, source_id, factor_version_id, code_object_id
):
    """Real persisted candidate path; explicit fixture-only, never arbitrary code."""
    # Composed revision018 dependencies are imported only at the explicit test
    # entrypoint, not by production/default dispatch or numeric journal readers.
    from backend.app.rdagent_evaluation_services import open_generation_evaluation
    from backend.app.rdagent_models import FactorCutoffExecution, FactorResearchSpec
    from backend.app.rdagent_source_resolution import require_source_code
    from packages.factors.contracts import SnapshotInput

    verify_runtime_inventory()
    fixed = (
        Path(__file__).resolve().parents[2] / "runtimes/factor_execution/abi_fixture.py"
    ).read_bytes()
    source_sha = hashlib.sha256(fixed).hexdigest()
    with shared_lifecycle(factory.kw["bind"], settings.storage_root):
        with neutral_transaction(
            factory, fence, resource_scope="factor_objects"
        ) as session:
            resolved = require_source_code(
                session,
                fence,
                source_id=source_id,
                expected_factor_version_id=factor_version_id,
                expected_code_object_id=code_object_id,
                expected_sha256=source_sha,
                settings=settings,
            )
            if resolved.code_utf8 != fixed:
                raise ProcessError("numeric_fixture_source_only")
            spec = session.scalar(
                select(FactorResearchSpec).where(
                    FactorResearchSpec.owner_id == fence.owner_id,
                    FactorResearchSpec.run_id == fence.run_id,
                )
            )
            if spec is None:
                raise ProcessError("numeric_process_source")
            config = FactorResearchConfig.model_validate(spec.config)
            if config.sha256 != spec.config_sha256:
                raise ProcessError("numeric_process_source")
            locks = {item.role: item.sha256 for item in config.runtime.dependency_locks}
            if (
                locks["candidate"] != CANDIDATE_LOCK_SHA256
                or locks["normalizer"] != NORMALIZER_LOCK_SHA256
            ):
                raise ProcessError("numeric_fixture_dependency")
            prefix, inputs = maintained_projection(
                config,
                factor_version_id=factor_version_id,
                source_utf8=resolved.code_utf8,
            )
        # Actual local image inspection precedes issuing the short original deadline.
        supervisor = NumericSupervisor(config.runtime.candidate_image)
        inventory = supervisor.verified_inventory("candidate")
        NumericSupervisor(config.runtime.normalizer_image).verified_inventory(
            "normalizer"
        )
        with neutral_transaction(
            factory, fence, resource_scope="factor_numeric"
        ) as session:
            # Fresh source resolution and the actual evaluation/cutoff obligation
            # commit atomically with admission; this is a maintained fixture path,
            # not a bypass of the disabled authenticated controller cutoff API.
            resolved = require_source_code(
                session,
                fence,
                source_id=source_id,
                expected_factor_version_id=factor_version_id,
                expected_code_object_id=code_object_id,
                expected_sha256=source_sha,
                settings=settings,
            )
            calendar, _prices = demo_inputs()
            evaluation = open_generation_evaluation(
                session,
                fence,
                start_id=resolved.start_id,
                source_id=source_id,
                calendar=calendar,
                settings=settings,
            )
            snapshot = SnapshotInput.model_validate(evaluation.snapshot_input)
            prefix, inputs = maintained_projection(
                config,
                factor_version_id=factor_version_id,
                source_utf8=resolved.code_utf8,
                snapshot=snapshot,
            )
            if prefix.snapshot_input_sha256 != evaluation.input_sha256:
                raise ProcessError("numeric_process_evaluation")
            binding = NumericInput(
                schema_version="mineagent.numeric-process-input.v2",
                source_object_id=code_object_id,
                source_sha256=source_sha,
                prefix_sha256=prefix.sha256,
                cutoff=prefix.host_cutoff,
                files=tuple(
                    NumericInputFile(
                        destination=name,
                        sha256=hashlib.sha256(value).hexdigest(),
                        size_bytes=len(value),
                    )
                    for name, value in sorted(inputs.items())
                ),
            )
            cutoff_id = uuid4()
            reservation = reserve_budget(
                session,
                fence,
                purpose="process_unit",
                logical_key="numeric-fixture:" + uuid4().hex,
                amounts=BudgetVector(
                    process_slots=1, wall_milliseconds=60000, storage_bytes=67_174_400
                ),
            )
            admission = ProcessDeclarationV2(
                schema_version="mineagent.process-admission.v2",
                id=uuid4(),
                owner_id=fence.owner_id,
                run_id=fence.run_id,
                attempt_id=fence.attempt_id,
                generation=fence.generation,
                role="factor_cutoff_candidate",
                logical_operation_id=cutoff_id,
                launch_ordinal=1,
                profile=config.runtime.candidate_profile,
                input_sha256=digest(binding.model_dump(mode="json")),
                reservation_id=reservation.id,
                image_id=config.runtime.candidate_image,
                ipc=NoIPC(ipc_policy="none", ticket_sha256=None),
            )
            now = datetime.now(UTC)
            intent = NumericIntent(
                schema_version="mineagent.numeric-process-intent.v2",
                id=uuid4(),
                admission=admission,
                inputs=binding,
                issued_at=now,
                not_after=now + timedelta(seconds=8),
            )
            persist_numeric_intent(session, fence, intent)
            session.add(
                FactorCutoffExecution(
                    id=cutoff_id,
                    owner_id=fence.owner_id,
                    run_id=fence.run_id,
                    attempt_id=fence.attempt_id,
                    generation=fence.generation,
                    evaluation_id=evaluation.id,
                    start_id=resolved.start_id,
                    factor_version_id=factor_version_id,
                    cutoff_ordinal=evaluation.expected_cutoffs - 1,
                    projection=prefix.model_dump(mode="json"),
                    projection_sha256=prefix.sha256,
                    candidate_admission_id=admission.id,
                    evaluated_source_id=source_id,
                )
            )
            session.flush()
        _execute_committed_numeric_unit(
            factory,
            fence,
            settings.storage_root,
            admission.id,
            input_bytes=inputs,
            inventory=inventory,
        )
        return admission.id


def execute_fixed_normalizer_from_native(factory, fence, settings, *, publication_id):
    """Real native consumer claim→normalizer; fixed maintained source only."""
    from backend.app.factor_native_io import native_storage_guard
    from backend.app.factor_native_services import (
        _publication,
        decode_intent,
        read_completed_native,
    )
    from backend.app.native_normalizer_claim import claim_native_normalizer
    from backend.app.numeric_launch_services import claim_numeric_launch
    from backend.app.rdagent_models import FactorCutoffExecution
    from backend.app.rdagent_services import _scope_row
    from packages.factors.contracts import canonical_bytes
    from packages.rdagent_adapter.numeric_profile import normalizer_inputs
    from packages.rdagent_adapter.prefix import PrefixManifest
    from workers.research.numeric_process import _execute_claimed_numeric_unit

    verify_runtime_inventory()
    fixed = (
        Path(__file__).resolve().parents[2] / "runtimes/factor_execution/abi_fixture.py"
    ).read_bytes()
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        native_storage_guard(settings.storage_root),
    ):
        with neutral_transaction(
            factory, fence, resource_scope="factor_objects"
        ) as session:
            _identity, _raw = read_completed_native(
                session, fence, publication_id, settings
            )
            publication = _publication(session, fence, publication_id)
            candidate = decode_intent(publication)
            if candidate.source_sha256 != hashlib.sha256(fixed).hexdigest():
                raise ProcessError("numeric_fixture_source_only")
            cutoff = _scope_row(
                session, FactorCutoffExecution, publication.cutoff_id, fence, lock=True
            )
            prefix = PrefixManifest.model_validate(cutoff.projection)
        inventory = NumericSupervisor(prefix.normalizer_image).verified_inventory(
            "normalizer"
        )
        with neutral_transaction(
            factory, fence, resource_scope="factor_numeric"
        ) as session:
            identity, raw = read_completed_native(
                session, fence, publication_id, settings
            )
            publication = _publication(session, fence, publication_id)
            candidate = decode_intent(publication)
            cutoff = _scope_row(
                session, FactorCutoffExecution, publication.cutoff_id, fence, lock=True
            )
            prefix = PrefixManifest.model_validate(cutoff.projection)
            _, inputs, _ = normalizer_inputs(
                prefix,
                hdf=raw,
                expected_hdf_sha256=identity.object.sha256,
                listings=canonical_bytes(
                    [item.model_dump(mode="json") for item in prefix.listings]
                ),
            )
            binding = NumericInput(
                schema_version="mineagent.numeric-process-input.v2",
                source_object_id=candidate.source_object_id,
                source_sha256=candidate.source_sha256,
                prefix_sha256=prefix.sha256,
                cutoff=prefix.host_cutoff,
                files=tuple(
                    NumericInputFile(
                        destination=name,
                        sha256=hashlib.sha256(value).hexdigest(),
                        size_bytes=len(value),
                    )
                    for name, value in sorted(inputs.items())
                ),
            )
            reservation = reserve_budget(
                session,
                fence,
                purpose="process_unit",
                logical_key="numeric-normalizer:" + uuid4().hex,
                amounts=BudgetVector(
                    process_slots=1, wall_milliseconds=60000, storage_bytes=67_174_400
                ),
            )
            admission = ProcessDeclarationV2(
                schema_version="mineagent.process-admission.v2",
                id=uuid4(),
                owner_id=fence.owner_id,
                run_id=fence.run_id,
                attempt_id=fence.attempt_id,
                generation=fence.generation,
                role="factor_normalizer",
                logical_operation_id=cutoff.id,
                launch_ordinal=1,
                profile=prefix.normalizer_profile,
                input_sha256=digest(binding.model_dump(mode="json")),
                reservation_id=reservation.id,
                image_id=prefix.normalizer_image,
                ipc=NoIPC(ipc_policy="none", ticket_sha256=None),
            )
            now = datetime.now(UTC)
            intent = NumericIntent(
                schema_version="mineagent.numeric-process-intent.v2",
                id=uuid4(),
                admission=admission,
                inputs=binding,
                issued_at=now,
                not_after=now + timedelta(seconds=8),
            )
            persist_numeric_intent(session, fence, intent)
            claim_native_normalizer(
                session, fence, publication_id, admission.id, settings
            )
            committed_intent = claim_numeric_launch(session, fence, admission.id)
        _execute_claimed_numeric_unit(
            factory,
            fence,
            settings.storage_root,
            committed_intent,
            input_bytes=inputs,
            inventory=inventory,
            settings=settings,
        )
        return admission.id
