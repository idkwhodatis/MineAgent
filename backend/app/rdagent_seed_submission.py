"""026 flush-only seeded submission and exact-key metadata replay.

No attempt/fence/controller is manufactured here. Both receipt families share
ResearchRun's owner/key namespace and a transaction-level owner lock. New work
rereads actual parent bytes; accepted replay does not depend on file availability.
"""

from uuid import uuid4

from sqlalchemy import select, text

from backend.app.factor_analysis_models import FactorAnalysisPlan
from backend.app.factor_models import FactorVersion
from backend.app.models import OutboxEvent, ResearchRun
from backend.app.rdagent_models import FactorResearchSpec
from backend.app.rdagent_seed_contracts import RDSeededSubmissionReceipt, RDSeededSubmit
from backend.app.rdagent_seed_models import FactorSeededSubmission
from backend.app.rdagent_seed_preflight import (
    REVISION,
    resolve_seeded_runtime,
    seeded_commitment,
    seeded_preflight,
)
from backend.app.rdagent_seed_runtime_profile import RDSeededServerRuntimeProfile
from backend.app.rdagent_seed_services import read_saved_seed, resolve_seed_parent
from backend.app.rdagent_submission_receipts import validate_key
from backend.app.rdagent_submission_services import _persist_queued_graph
from backend.app.research_lifecycle import _try_neutral_lock
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.rdagent_adapter.controller_profile import (
    controller_selection_fields,
)
from packages.rdagent_adapter.persisted_identity import require_source_identity


def validate_saved_seed_binding(row, run, spec, plan, owner_id, key):
    request, parent = read_saved_seed(row)
    try:
        config = require_source_identity(run)
        if (
            row.owner_id,
            run.owner_id,
            parent.owner_id,
            row.run_id,
            run.idempotency_key,
            row.idempotency_key,
            row.config_sha256,
        ) != (owner_id, owner_id, owner_id, run.id, key, key, config.sha256):
            raise ValueError("seeded run binding")
        if spec is None or (
            spec.id,
            spec.owner_id,
            spec.run_id,
            spec.config_sha256,
            spec.input_sha256,
        ) != (
            row.spec_id,
            owner_id,
            run.id,
            config.sha256,
            config.input.sha256,
        ):
            raise ValueError("seeded spec binding")
        if plan is None or (
            plan.id,
            plan.owner_id,
            plan.run_id,
            plan.spec_id,
            plan.config_sha256,
            plan.input_sha256,
            plan.objective_sha256,
            plan.source_policy_sha256,
        ) != (
            row.analysis_plan_id,
            owner_id,
            run.id,
            row.spec_id,
            config.sha256,
            config.input.sha256,
            request.objective.sha256,
            digest(request.source_policy),
        ):
            raise ValueError("seeded analysis plan binding")
        if (
            request.dataset_version_id,
            request.window,
            request.input_fields,
            request.lookback_sessions,
            request.lag_sessions,
            request.task,
            request.budget,
            request.max_iterations,
        ) != (
            config.input.dataset.dataset_version_id,
            config.input.window,
            tuple(field.name for field in config.input.input_fields),
            config.input.lookback_sessions,
            config.input.lag_sessions,
            config.task,
            config.budget,
            config.max_iterations,
        ):
            raise ValueError("seeded request/config binding")
        from packages.rdagent_adapter.provider_config import (
            ProviderFactorResearchConfig,
        )

        provider_extra = {}
        receipt_type = RDSeededSubmissionReceipt
        if isinstance(config, ProviderFactorResearchConfig):
            from backend.app.rdagent_provider_profile import (
                RDProviderServerRuntimeProfile,
                provider_runtime_selection_fields,
            )
            from backend.app.rdagent_provider_submission_contracts import (
                RDProviderSeedReceipt,
                RDProviderSeedRequest,
                provider_commitment,
            )

            if type(request) is not RDProviderSeedRequest or (
                request.model_profile_id != config.model_binding.profile_id
                or request.selected_steer_version_ids
                != tuple(v.version_id for v in config.steer_snapshot.versions)
                or config.steer_snapshot.owner_id != owner_id
            ):
                raise ValueError("Provider request/model/guidance binding")
            profile = RDProviderServerRuntimeProfile(
                runtime=config.runtime,
                **provider_runtime_selection_fields(config),
                qlib_installed_parent_image_id=plan.qlib_installed_parent_image_id,
            )
            expected = provider_commitment(
                owner_id,
                request,
                dataset_sha256=config.input.dataset.content_sha256,
                input_sha256=config.input.sha256,
                profile_sha256=digest(profile),
                model_binding_sha256=config.model_binding.sha256,
                steer_snapshot_sha256=config.steer_snapshot_sha256,
                parent_binding_sha256=parent.sha256,
            )
            receipt_type = RDProviderSeedReceipt
            provider_extra = {"model_binding_sha256": config.model_binding.sha256}
        else:
            from backend.app.rdagent_seed_contracts import RDSeededSubmissionRequest

            if type(request) is not RDSeededSubmissionRequest:
                raise ValueError(
                    "Historical seed cannot bind configured provider request"
                )
            profile = RDSeededServerRuntimeProfile(
                runtime=config.runtime,
                **controller_selection_fields(config),
                qlib_installed_parent_image_id=plan.qlib_installed_parent_image_id,
            )
            expected = seeded_commitment(
                owner_id,
                request,
                parent,
                dataset_sha256=config.input.dataset.content_sha256,
                input_sha256=config.input.sha256,
                profile_sha256=digest(profile),
            )
        if (
            row.runtime_profile_sha256 != digest(profile)
            or row.preflight_sha256 != expected
        ):
            raise ValueError("seeded preflight/profile binding")
        return receipt_type(
            **provider_extra,
            run_id=run.id,
            plan_id=plan.id,
            request_sha256=row.request_sha256,
            preflight_sha256=row.preflight_sha256,
            parent_factor_id=parent.factor_id,
            parent_factor_version_id=parent.factor_version_id,
            parent_binding_sha256=parent.sha256,
            replayed=True,
            status=run.status,
        )
    except (ValueError, TypeError, AttributeError):
        raise DomainError(
            "seed_submission_drift", "Retained seeded request binding differs", 409
        ) from None


def existing_seeded_receipt(session, owner_id, key, submitted):
    validate_key(key)
    row = session.scalar(
        select(FactorSeededSubmission).filter_by(owner_id=owner_id, idempotency_key=key)
    )
    if row is None:
        if (
            session.scalar(
                select(ResearchRun.id).filter_by(owner_id=owner_id, idempotency_key=key)
            )
            is not None
        ):
            raise DomainError(
                "idempotency_conflict",
                "This key already identifies another request",
                409,
            )
        return None
    saved, _parent = read_saved_seed(row)
    if (
        canonical_bytes(saved) != canonical_bytes(submitted.request())
        or row.preflight_sha256 != submitted.preflight_sha256
    ):
        raise DomainError(
            "idempotency_conflict",
            "This key identifies a different request or preflight",
            409,
        )
    return validate_saved_seed_binding(
        row,
        session.get(ResearchRun, row.run_id),
        session.get(FactorResearchSpec, row.spec_id),
        session.get(FactorAnalysisPlan, row.analysis_plan_id),
        owner_id,
        key,
    )


def submit_seeded_run(session, owner_id, submitted, idempotency_key, settings):
    submitted = RDSeededSubmit.model_validate(submitted)
    validate_key(idempotency_key)
    if session.scalars(text("SELECT version_num FROM alembic_version")).all() not in (
        [REVISION],
        ["027_rd_controller_v3"],
        ["027a_contract_repairs"],
        ["027b_provider_material"],
        ["027c_seeded_controller_v4"],
        ["028_oe_structured_search"],
        ["029_research_workflow"],
        ["030_declared_upload_basket"],
        ["031_workflow_structured_stage"],
        ["032_normal_oe_note_pins"],
        ["032a_rd_terminal_results"],
        ["032b_oe_append_run_state"],
    ):
        raise DomainError(
            "engine_submission_schema_unavailable",
            "Explicit seeded submission schema unavailable",
            409,
        )
    _try_neutral_lock(session, f"submit:{owner_id}")
    previous = existing_seeded_receipt(session, owner_id, idempotency_key, submitted)
    if previous is not None:
        return previous
    from backend.app.comparison_services import reject_comparison_key

    reject_comparison_key(session, owner_id, idempotency_key)
    profile = resolve_seeded_runtime(settings)
    request = submitted.request()
    checked = seeded_preflight(session, owner_id, request, settings)
    if checked.preflight_sha256 != submitted.preflight_sha256:
        raise DomainError(
            "preflight_changed",
            "Parent or retained declaration changed; repeat preflight",
            409,
        )
    if not checked.can_submit or checked.runtime_profile_sha256 != digest(profile):
        raise DomainError(
            "runtime_profile_changed",
            "Seeded runtime or declaration is unavailable",
            409,
        )
    parent = resolve_seed_parent(
        session, owner_id, request.parent_factor_version_id, settings
    )
    if parent.reference != checked.parent:
        raise DomainError(
            "seed_parent_drift", "Parent binding changed during submission", 409
        )
    run, spec, plan = _persist_queued_graph(
        session,
        owner_id,
        request,
        idempotency_key,
        settings,
        profile.runtime,
        profile.qlib_installed_parent_image_id,
        expected_input_sha256=checked.frozen_input_sha256,
        controller_selection=profile.controller_profile,
    )
    version = session.get(FactorVersion, parent.reference.factor_version_id)
    provenance = canonical_bytes(version.source_provenance)
    if digest(version.source_provenance) != parent.reference.source_provenance_sha256:
        raise DomainError(
            "seed_parent_drift", "Parent provenance changed during submission", 409
        )
    ref = parent.reference
    row = FactorSeededSubmission(
        id=uuid4(),
        owner_id=owner_id,
        run_id=run.id,
        spec_id=spec.id,
        analysis_plan_id=plan.id,
        idempotency_key=idempotency_key,
        request_canonical=canonical_bytes(request).decode(),
        request_sha256=request.sha256,
        preflight_sha256=checked.preflight_sha256,
        config_sha256=spec.config_sha256,
        runtime_profile_sha256=digest(profile),
        parent_factor_id=ref.factor_id,
        parent_factor_version_id=ref.factor_version_id,
        parent_ordinal=ref.ordinal,
        parent_code_object_id=ref.code_object_id,
        parent_code_sha256=ref.code_sha256,
        parent_code_size_bytes=ref.code_size_bytes,
        parent_definition_sha256=ref.definition_sha256,
        parent_provenance_sha256=ref.source_provenance_sha256,
        parent_provenance_canonical=provenance.decode(),
        parent_binding_sha256=ref.sha256,
    )
    session.add(row)
    session.add(OutboxEvent(id=uuid4(), run_id=run.id, generation=1))
    session.flush()
    receipt = validate_saved_seed_binding(
        row, run, spec, plan, owner_id, idempotency_key
    )
    return receipt.model_copy(update={"replayed": False})
