"""026 read-only parent+data+exact first-prompt preflight; no execution authority."""

from sqlalchemy import text

from backend.app.engine_run_preflight import preflight
from backend.app.rdagent_configured_source import resolve_dataset_preflight
from backend.app.rdagent_seed_contracts import (
    CATALOG_REVISION,
    RDSeededPreflight,
    RDSeededSubmissionRequest,
    SeedPreflightReason,
)
from backend.app.rdagent_seed_runtime_profile import RDSeededServerRuntimeProfile
from backend.app.rdagent_seed_services import resolve_seed_parent
from backend.app.services import DomainError
from packages.factors.contracts import digest
from packages.rdagent_adapter.controller_profile import (
    composed_wall_floor,
    controller_profile,
)
from packages.rdagent_adapter.seed_prompt_budget import first_prompt_messages_for_task

REVISION = "026_rd_seeded_submissions"
RUNTIME_REASONS = {
    "runtime_disabled",
    "runtime_unconfigured",
    "runtime_unsupported",
    "runtime_schema_unavailable",
}


def resolve_seeded_runtime(settings):
    if getattr(settings, "rdagent_research_enabled", False) is not True:
        raise DomainError(
            "engine_submission_not_enabled", "RD research submission is disabled", 409
        )
    value = getattr(settings, "rdagent_seeded_runtime_profile", None)
    if value is None:
        raise DomainError(
            "engine_runtime_unconfigured",
            "No accepted seeded RD runtime is configured",
            409,
        )
    try:
        return RDSeededServerRuntimeProfile.model_validate(value)
    except ValueError:
        raise DomainError(
            "engine_runtime_unsupported",
            "Seeded RD runtime profile is unsupported",
            409,
        ) from None


def seeded_commitment(
    owner_id, request, parent, *, dataset_sha256, input_sha256, profile_sha256
):
    return digest(
        {
            "schema_version": "mineagent.rdagent-seeded-preflight-binding.v1",
            "owner_id": str(owner_id),
            "request_sha256": request.sha256,
            "parent_binding_sha256": parent.sha256,
            "dataset_content_sha256": dataset_sha256,
            "frozen_input_sha256": input_sha256,
            "catalog_revision": CATALOG_REVISION,
            "runtime_profile_sha256": profile_sha256,
        }
    )


def seeded_preflight(session, owner_id, request, settings):
    request = RDSeededSubmissionRequest.model_validate(request)
    # Shared projection is used solely for existing data/workload checks. It is
    # never stored or submitted as a fresh023 declaration.
    base = preflight(session, owner_id, request.research_declaration(), settings)
    parent = resolve_seed_parent(
        session, owner_id, request.parent_factor_version_id, settings
    )
    reasons = [
        SeedPreflightReason.shared(value)
        for value in base.reasons
        if value.code not in RUNTIME_REASONS
    ]
    eligible = base.data_eligible
    if eligible:
        source = resolve_dataset_preflight(
            session,
            owner_id=owner_id,
            dataset_id=request.dataset_version_id,
            policy=request.source_policy,
            input_names=request.input_fields,
            settings=settings,
        )
        try:
            first_prompt_messages_for_task(
                request.task,
                source.input_fields,
                parent,
                max_iterations=request.max_iterations,
            )
        except ValueError:
            eligible = False
            reasons.append(
                SeedPreflightReason(
                    code="seed_prompt_unsupported",
                    message="The complete parent and declared task exceed the fixed first-call prompt limits; source is never truncated.",
                )
            )
    profile_sha = None
    available = False
    try:
        profile = resolve_seeded_runtime(settings)
        profile_sha = digest(profile)
        if request.budget.wall_milliseconds < composed_wall_floor(
            controller_profile(profile)
        ):
            eligible = False
            reasons.append(
                SeedPreflightReason(
                    code="budget_below_profile",
                    message="Budget is below selected controller profile floor.",
                )
            )
        if session.scalars(text("SELECT version_num FROM alembic_version")).all() in (
            [REVISION],
            ["027_rd_controller_v3"],
            ["027a_contract_repairs"],
            ["027b_provider_material"],
            ["027c_seeded_controller_v4"],
        ) and (
            profile.controller_profile is None
            or session.scalars(text("SELECT version_num FROM alembic_version")).all()
            in (
                ["027_rd_controller_v3"],
                ["027a_contract_repairs"],
                ["027b_provider_material"],
                ["027c_seeded_controller_v4"],
            )
        ):
            available = True
        else:
            reasons.append(
                SeedPreflightReason(
                    code="runtime_schema_unavailable",
                    message="The explicit seeded submission schema is not installed.",
                )
            )
    except DomainError as exc:
        reasons.append(
            SeedPreflightReason(
                code={
                    "engine_submission_not_enabled": "runtime_disabled",
                    "engine_runtime_unconfigured": "runtime_unconfigured",
                    "engine_runtime_unsupported": "runtime_unsupported",
                }[exc.code],
                message=exc.message,
            )
        )
    return RDSeededPreflight(
        normalized_request=request,
        parent=parent.reference,
        parent_binding_sha256=parent.reference.sha256,
        request_sha256=request.sha256,
        preflight_sha256=seeded_commitment(
            owner_id,
            request,
            parent.reference,
            dataset_sha256=base.dataset_content_sha256,
            input_sha256=base.frozen_input_sha256,
            profile_sha256=profile_sha,
        ),
        dataset_content_sha256=base.dataset_content_sha256,
        frozen_input_sha256=base.frozen_input_sha256,
        runtime_profile_sha256=profile_sha,
        data_eligible=eligible,
        can_submit=eligible and available and not reasons,
        reasons=tuple(reasons),
        cutoff_count=base.cutoff_count,
        required_process_units=base.required_process_units,
        output_window=request.window,
    )
