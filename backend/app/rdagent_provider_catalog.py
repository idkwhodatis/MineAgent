"""Owner-scoped public model choices; never expose endpoint or secret references."""


def provider_options(settings, owner_id, revision):
    if not getattr(settings, "rdagent_provider_profiles", ()):
        return ()
    from backend.app.engine_run_schemas import RDModelProfileOption
    from backend.app.rdagent_provider_preflight import provider_budget_floor
    from backend.app.rdagent_provider_profile import (
        provider_seed_controller_profile,
        resolve_provider_declaration,
        resolve_provider_runtime,
    )
    from backend.app.services import DomainError
    from packages.rdagent_adapter.controller_profile import (
        LEGACY_CONTROLLER_PROFILE,
        SEEDED_CONTROLLER_PROFILE,
        controller_profile,
    )
    from packages.rdagent_provider.contracts import DeclaredTokenPricing

    result = []
    ids = sorted(
        {
            p.connection.id
            for p in settings.rdagent_provider_profiles
            if owner_id in p.connection.owner_ids
        }
    )
    for profile_id in ids:
        try:
            _, binding = resolve_provider_declaration(
                settings, owner_id, profile_id, require_enabled=False
            )
        except DomainError:
            continue
        reason = "configured_model_profile_available"
        enabled = True
        runtime = None
        try:
            resolve_provider_declaration(settings, owner_id, profile_id)
            runtime = resolve_provider_runtime(settings)
            if revision not in (
                "026_rd_seeded_submissions",
                "027_rd_controller_v3",
                "027a_contract_repairs",
                "027b_provider_material",
                "027c_seeded_controller_v4",
                "028_oe_structured_search",
                "029_research_workflow",
                "030_declared_upload_basket",
                "031_workflow_structured_stage",
                "032_normal_oe_note_pins",
                "032a_rd_terminal_results",
                "032b_oe_append_run_state",
            ) or (
                runtime.controller_profile is not None
                and revision
                not in (
                    "027_rd_controller_v3",
                    "027a_contract_repairs",
                    "027b_provider_material",
                    "027c_seeded_controller_v4",
                    "028_oe_structured_search",
                    "029_research_workflow",
                    "030_declared_upload_basket",
                    "031_workflow_structured_stage",
                    "032_normal_oe_note_pins",
                    "032a_rd_terminal_results",
                    "032b_oe_append_run_state",
                )
            ):
                reason, enabled = "runtime_schema_unavailable", False
        except DomainError as error:
            reason, enabled = error.code, False
        seeded_runtime = None
        seeded_enabled, seeded_reason = enabled, reason
        if enabled:
            try:
                seeded_runtime = resolve_provider_runtime(settings, seeded=True)
                if provider_seed_controller_profile(
                    seeded_runtime
                ) == SEEDED_CONTROLLER_PROFILE and revision not in (
                    "027c_seeded_controller_v4",
                    "028_oe_structured_search",
                    "029_research_workflow",
                    "030_declared_upload_basket",
                    "031_workflow_structured_stage",
                    "032_normal_oe_note_pins",
                    "032a_rd_terminal_results",
                    "032b_oe_append_run_state",
                ):
                    seeded_enabled = False
                    seeded_reason = "runtime_schema_unavailable"
            except DomainError as error:
                seeded_enabled, seeded_reason = False, error.code
        result.append(
            RDModelProfileOption(
                id=profile_id,
                model_binding_sha256=binding.sha256,
                chat_model=binding.chat_model,
                embedding_model=binding.embedding_model,
                synthetic=binding.synthetic,
                pricing_status=binding.pricing.status,
                recommended_budget=provider_budget_floor(
                    binding,
                    profile=controller_profile(runtime)
                    if runtime is not None
                    else LEGACY_CONTROLLER_PROFILE,
                )
                if isinstance(binding.pricing, DeclaredTokenPricing)
                else None,
                seeded_recommended_budget=provider_budget_floor(
                    binding,
                    profile=provider_seed_controller_profile(seeded_runtime)
                    or LEGACY_CONTROLLER_PROFILE,
                )
                if seeded_enabled
                and seeded_runtime is not None
                and isinstance(binding.pricing, DeclaredTokenPricing)
                else None,
                seeded_can_create=seeded_enabled,
                seeded_reason_code=seeded_reason,
                can_create=enabled,
                reason_code=reason,
            )
        )
    return tuple(result)
