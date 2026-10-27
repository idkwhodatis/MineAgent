"""Operator-only model-backed runtime declaration and fresh owner resolution."""

import os
from pathlib import Path
from typing import Literal

from backend.app.rdagent_server_profile import RDServerRuntimeProfile
from packages.rdagent_provider.contracts import PricingUnavailable
from packages.rdagent_provider.operator_profiles import RDOperatorModelProfile


class RDProviderServerRuntimeProfile(RDServerRuntimeProfile):
    schema_version: Literal["mineagent.rdagent-provider-server-profile.v1"] = (
        "mineagent.rdagent-provider-server-profile.v1"
    )
    profile: Literal["configured_rd_model_single_output_session_v1"] = (
        "configured_rd_model_single_output_session_v1"
    )
    model_profile: Literal["configured.rdagent.v1"] = "configured.rdagent.v1"


def resolve_provider_declaration(
    settings, owner_id, profile_id, *, require_enabled=True
):
    from backend.app.services import DomainError

    values = [
        value
        for value in getattr(settings, "rdagent_provider_profiles", ())
        if value.connection.id == profile_id and owner_id in value.connection.owner_ids
    ]
    if len(values) != 1:
        raise DomainError(
            "rd_model_unavailable", "Owned chat and embedding profile unavailable", 409
        )
    profile = RDOperatorModelProfile.model_validate(values[0])
    binding = profile.public_binding(owner_id)
    if require_enabled:
        enabled = (
            getattr(settings, "rdagent_synthetic_provider_enabled", False)
            if binding.synthetic
            else getattr(settings, "rdagent_live_provider_enabled", False)
        )
        if enabled is not True:
            raise DomainError(
                "rd_provider_disabled", "This provider transport is not enabled", 409
            )
        if not os.environ.get(profile.connection.secret_env):
            raise DomainError(
                "rd_model_unavailable",
                "Host chat and embedding credentials are not configured",
                409,
            )
        if isinstance(binding.pricing, PricingUnavailable):
            raise DomainError(
                "rd_model_pricing_unavailable",
                "Declared price bounds are required before cost-capped model calls",
                409,
            )
    return profile, binding


def resolve_provider_runtime(settings):
    from backend.app.services import DomainError

    if getattr(settings, "rdagent_research_enabled", False) is not True:
        raise DomainError(
            "engine_submission_not_enabled", "RD research submission is disabled", 409
        )
    raw = getattr(settings, "rdagent_provider_runtime_profile", None)
    if raw is None:
        raise DomainError(
            "engine_runtime_unconfigured",
            "No model-backed RD runtime is configured",
            409,
        )
    executable = getattr(settings, "rdagent_provider_python", None)
    if (
        executable is None
        or not Path(executable).is_absolute()
        or not Path(executable).is_file()
        or not os.access(executable, os.X_OK)
    ):
        raise DomainError(
            "engine_runtime_unconfigured",
            "An explicit host SDK interpreter is required for model-backed research",
            409,
        )
    try:
        return RDProviderServerRuntimeProfile.model_validate(raw)
    except ValueError:
        raise DomainError(
            "engine_runtime_unsupported",
            "Model-backed RD runtime profile is unsupported",
            409,
        ) from None
