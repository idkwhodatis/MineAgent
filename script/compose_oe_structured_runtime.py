"""Offline normal027 startup checks; no SDK request, engine launch or enablement."""

import os

from script.compose_provider_runtime import verify_sdk


def verify_images(profile):
    from backend.app.oe_structured_epoch_services import HOOK_MANIFEST_SHA256
    from runtimes.research_supervisor.structured_factor_supervisor import (
        StructuredSupervisor,
    )
    from runtimes.structured_factor_engine.recipe import dependency_lock_sha256
    from workers.research.oe_structured_controller import image_inventory

    pins = profile.runtime
    if pins.controller_inventory_sha256 != HOOK_MANIFEST_SHA256:
        raise ValueError("Exact normal controller hook inventory required")
    if pins.engine_dependency_lock_sha256 != dependency_lock_sha256():
        raise ValueError("Exact protected engine dependency lock required")
    if image_inventory(pins)["image_id"] != pins.controller_image_id:
        raise ValueError("Normal controller image differs")
    actual = StructuredSupervisor(
        pins.engine_image_id, pins.engine_installed_parent_image_id
    ).verified_inventory("engine")
    if actual["image_id"] != pins.engine_image_id:
        raise ValueError("Protected engine image differs")


def validate_enabled_profiles(settings, role):
    enabled = getattr(settings, "oe_structured_research_enabled", False) and (
        getattr(settings, "oe_structured_live_provider_enabled", False)
        or getattr(settings, "oe_structured_synthetic_provider_enabled", False)
    )
    if not enabled:
        return
    from backend.app.oe_structured_server_profile import resolve_runtime

    profile = resolve_runtime(settings)
    verify_sdk(settings.oe_structured_provider_python)
    if not settings.oe_structured_provider_profiles:
        raise ValueError("Enabled normal OE requires explicit operator profiles")
    seen = set()
    for model in settings.oe_structured_provider_profiles:
        if model.connection.id in seen:
            raise ValueError("Duplicate normal OE model profile")
        seen.add(model.connection.id)
        transport_enabled = (
            settings.oe_structured_synthetic_provider_enabled
            if model.connection.synthetic
            else settings.oe_structured_live_provider_enabled
        )
        if transport_enabled and not os.environ.get(model.connection.secret_env):
            raise ValueError("Enabled normal OE provider credential is unavailable")
    if role in {"research-worker", "evaluator-reconciler"}:
        verify_images(profile)
