"""Operator-only027 configuration. No settings defaults or activation performed here."""

import os
from pathlib import Path

from backend.app.oe_structured_server_schemas import OEStructuredServerProfile
from backend.app.services import DomainError
from packages.openevolve_structured.candidate import _raw_fields
from packages.openevolve_structured.provider_operator import OEOperatorChatProfile


def resolve_operator_profile(settings, owner_id, profile_id):
    """Fresh host-only lookup; never return endpoint or secret in a public receipt."""
    candidates = []
    for raw in getattr(settings, "oe_structured_provider_profiles", ()):
        try:
            value = OEOperatorChatProfile.model_validate(_raw_fields(raw))
        except (ValueError, TypeError):
            continue
        if value.connection.id == profile_id and owner_id in value.connection.owner_ids:
            candidates.append(value)
    if len(candidates) != 1:
        raise DomainError(
            "oe_model_unavailable", "Owned OE model profile unavailable", 409
        )
    profile = candidates[0]
    binding = profile.public_binding(owner_id)
    key = (
        "oe_structured_synthetic_provider_enabled"
        if binding.synthetic
        else "oe_structured_live_provider_enabled"
    )
    if getattr(settings, key, False) is not True:
        raise DomainError(
            "oe_provider_disabled", "This OE provider transport is disabled", 409
        )
    if binding.pricing.status != "operator_declared":
        raise DomainError(
            "oe_model_pricing_unavailable",
            "Declared price estimate required before cost-capped calls",
            409,
        )
    # Only the explicitly named operator secret is consulted at runtime; never
    # enumerate environment variables or return its value to the controller.
    if not os.environ.get(profile.connection.secret_env):
        raise DomainError(
            "oe_model_unavailable", "Host provider credentials are not configured", 409
        )
    return profile, binding


def resolve_runtime(settings):
    if getattr(settings, "oe_structured_research_enabled", False) is not True:
        raise DomainError(
            "oe_submission_disabled", "Structured OE submission is disabled", 409
        )
    executable = getattr(settings, "oe_structured_provider_python", None)
    if (
        type(executable) is not str
        or not Path(executable).is_absolute()
        or not Path(executable).is_file()
        or not os.access(executable, os.X_OK)
    ):
        raise DomainError(
            "oe_runtime_unconfigured",
            "Explicit controlled SDK interpreter required",
            409,
        )
    try:
        return OEStructuredServerProfile.model_validate(
            _raw_fields(getattr(settings, "oe_structured_runtime_profile", None))
        )
    except (ValueError, TypeError):
        raise DomainError(
            "oe_runtime_unconfigured", "Structured OE runtime is not configured", 409
        ) from None
