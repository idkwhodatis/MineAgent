"""Real configured model eligibility; never substitute fixture task/source."""

from sqlalchemy import text

from backend.app.engine_run_preflight import RECOMMENDED_BUDGET, preflight
from backend.app.rdagent_configured_source import resolve_dataset_preflight
from backend.app.rdagent_provider_profile import (
    provider_seed_controller_profile,
    resolve_provider_declaration,
    resolve_provider_runtime,
)
from backend.app.rdagent_provider_submission_contracts import (
    ProviderPreflightReason,
    RDProviderPreflight,
    RDProviderSeedRequest,
    parse_provider_request,
    provider_commitment,
)
from backend.app.rdagent_seed_services import resolve_seed_parent
from backend.app.rdagent_steer_services import resolve_steer_snapshot
from backend.app.services import DomainError
from packages.factors.contracts import digest
from packages.rdagent_adapter.controller_profile import (
    LEGACY_CONTROLLER_PROFILE,
    composed_wall_floor,
    controller_profile,
)
from packages.rdagent_adapter.provider_prompt import first_provider_messages_for_task
from packages.rdagent_provider.accounting import INPUT_TOKEN_BOUND, PIPE_PEAK_BYTES
from packages.rdagent_provider.contracts import DeclaredTokenPricing
from packages.research_runtime.budget_contracts import BudgetVector

MODEL_CALL_BOUND = 32


def provider_budget_floor(binding, *, profile=LEGACY_CONTROLLER_PROFILE):
    if type(binding.pricing) is not DeclaredTokenPricing:
        raise ValueError("Declared price estimate required")
    price = binding.pricing
    per_call = max(
        INPUT_TOKEN_BOUND * price.chat_input_microusd_per_million
        + binding.max_output_tokens * price.chat_output_microusd_per_million,
        INPUT_TOKEN_BOUND * price.embedding_microusd_per_million,
    )
    base = RECOMMENDED_BUDGET.model_dump()
    base.update(
        calls=MODEL_CALL_BOUND,
        tokens=MODEL_CALL_BOUND * (INPUT_TOKEN_BOUND + binding.max_output_tokens),
        cost_microunits=MODEL_CALL_BOUND * ((per_call + 999999) // 1000000),
        process_slots=4 + MODEL_CALL_BOUND,
        wall_milliseconds=composed_wall_floor(profile)
        + MODEL_CALL_BOUND * binding.request_deadline_ms,
        storage_bytes=RECOMMENDED_BUDGET.storage_bytes
        + MODEL_CALL_BOUND * PIPE_PEAK_BYTES,
    )
    return BudgetVector(**base)


def provider_preflight(
    session, owner_id, request, settings, *, accepted_steer_snapshot=None
):
    return _provider_preflight(
        session,
        owner_id,
        request,
        settings,
        accepted_steer_snapshot=accepted_steer_snapshot,
        seed_reader=resolve_seed_parent,
    )


def provider_preflight_under_native_guard(session, owner_id, request, settings):
    """Internal workflow declaration using an actually held native guard."""
    from backend.app.factor_native_io import require_native_storage_guard
    from backend.app.rdagent_seed_services import resolve_seed_parent_under_native_guard

    require_native_storage_guard(settings.storage_root)
    result = _provider_preflight(
        session,
        owner_id,
        request,
        settings,
        accepted_steer_snapshot=None,
        seed_reader=resolve_seed_parent_under_native_guard,
    )
    require_native_storage_guard(settings.storage_root)
    return result


def _provider_preflight(
    session, owner_id, request, settings, *, accepted_steer_snapshot, seed_reader
):
    request = parse_provider_request(request.model_dump(mode="json"))
    base = preflight(session, owner_id, request.research_declaration(), settings)
    ignored = {
        "runtime_disabled",
        "runtime_unconfigured",
        "runtime_unsupported",
        "runtime_schema_unavailable",
        "task_profile_unsupported",
        "budget_below_profile",
    }
    reasons = [
        ProviderPreflightReason.model_validate(row.model_dump())
        for row in base.reasons
        if row.code not in ignored
    ]
    data_eligible = not reasons
    if accepted_steer_snapshot is None:
        snapshot = resolve_steer_snapshot(
            session, owner_id, request.selected_steer_version_ids
        )
    else:
        from backend.app.rdagent_steer_services import verify_steer_snapshot

        snapshot = accepted_steer_snapshot
        verify_steer_snapshot(session, owner_id, snapshot)
        if (
            tuple(row.version_id for row in snapshot.versions)
            != request.selected_steer_version_ids
        ):
            raise DomainError("steer_snapshot_drift", "Accepted selection differs", 409)
    parent = (
        seed_reader(session, owner_id, request.parent_factor_version_id, settings)
        if isinstance(request, RDProviderSeedRequest)
        else None
    )
    binding = runtime = None
    try:
        _, binding = resolve_provider_declaration(
            settings, owner_id, request.model_profile_id
        )
        runtime = resolve_provider_runtime(
            settings, seeded=isinstance(request, RDProviderSeedRequest)
        )
    except DomainError as exc:
        code = {
            "engine_submission_not_enabled": "runtime_disabled",
            "engine_runtime_unconfigured": "runtime_unconfigured",
            "engine_runtime_unsupported": "runtime_unsupported",
        }.get(exc.code, exc.code)
        reasons.append(ProviderPreflightReason(code=code, message=exc.message))
    if (
        binding is not None
        and isinstance(binding.pricing, DeclaredTokenPricing)
        and not request.budget.covers(
            provider_budget_floor(
                binding,
                profile=(
                    provider_seed_controller_profile(runtime)
                    or LEGACY_CONTROLLER_PROFILE
                )
                if runtime is not None and isinstance(request, RDProviderSeedRequest)
                else controller_profile(runtime)
                if runtime is not None
                else LEGACY_CONTROLLER_PROFILE,
            )
        )
    ):
        reasons.append(
            ProviderPreflightReason(
                code="budget_below_profile",
                message="Budget must cover declared SDK process, token, price-estimate and retained evidence bounds as well as the four research units.",
            )
        )
    if base.frozen_input_sha256 is not None and not any(
        row.code
        in {
            "dataset_format_unsupported",
            "dataset_bytes_unavailable",
            "source_projection_unsupported",
        }
        for row in reasons
    ):
        source = resolve_dataset_preflight(
            session,
            owner_id=owner_id,
            dataset_id=request.dataset_version_id,
            policy=request.source_policy,
            input_names=request.input_fields,
            settings=settings,
        )
        try:
            first_provider_messages_for_task(
                request.task, source.input_fields, snapshot, parent
            )
        except ValueError:
            reasons.append(
                ProviderPreflightReason(
                    code="model_prompt_unsupported",
                    message="Complete task, selected guidance and parent exceed the fixed model message limit; nothing is truncated.",
                )
            )
    if session.scalars(text("SELECT version_num FROM alembic_version")).all() not in (
        ["026_rd_seeded_submissions"],
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
    ) or (
        runtime is not None
        and runtime.controller_profile is not None
        and session.scalars(text("SELECT version_num FROM alembic_version")).all()
        not in (
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
        )
    ):
        reasons.append(
            ProviderPreflightReason(
                code="runtime_schema_unavailable",
                message="The versioned model-backed submission schema is not installed.",
            )
        )
    if (
        runtime is not None
        and isinstance(request, RDProviderSeedRequest)
        and runtime.seeded_controller_profile is not None
        and session.scalars(text("SELECT version_num FROM alembic_version")).all()
        not in (
            ["027c_seeded_controller_v4"],
            ["028_oe_structured_search"],
            ["029_research_workflow"],
            ["030_declared_upload_basket"],
            ["031_workflow_structured_stage"],
            ["032_normal_oe_note_pins"],
            ["032a_rd_terminal_results"],
            ["032b_oe_append_run_state"],
        )
    ):
        reasons.append(
            ProviderPreflightReason(
                code="runtime_schema_unavailable",
                message="Explicit seeded controller schema is not installed.",
            )
        )
    profile_sha = None if runtime is None else digest(runtime)
    commitment = provider_commitment(
        owner_id,
        request,
        dataset_sha256=base.dataset_content_sha256,
        input_sha256=base.frozen_input_sha256,
        profile_sha256=profile_sha,
        model_binding_sha256=None if binding is None else binding.sha256,
        steer_snapshot_sha256=snapshot.sha256,
        parent_binding_sha256=None if parent is None else parent.reference.sha256,
    )
    return RDProviderPreflight(
        normalized_request=request,
        request_sha256=request.sha256,
        preflight_sha256=commitment,
        dataset_content_sha256=base.dataset_content_sha256,
        frozen_input_sha256=base.frozen_input_sha256,
        runtime_profile_sha256=profile_sha,
        model_binding=binding,
        model_binding_sha256=None if binding is None else binding.sha256,
        parent=None if parent is None else parent.reference,
        parent_binding_sha256=None if parent is None else parent.reference.sha256,
        steer_snapshot=snapshot,
        steer_snapshot_sha256=snapshot.sha256,
        data_eligible=data_eligible,
        can_submit=not reasons and runtime is not None and binding is not None,
        reasons=tuple(reasons),
        cutoff_count=base.cutoff_count,
        required_process_units=4 + MODEL_CALL_BOUND,
        output_window=request.window,
    )
