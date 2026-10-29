"""Fresh server-owned provider declaration for workflow preflight, not admission."""

from dataclasses import dataclass

from backend.app.rdagent_provider_preflight import provider_preflight
from backend.app.rdagent_provider_profile import resolve_provider_runtime
from backend.app.rdagent_provider_submission_contracts import parse_provider_request
from backend.app.rdagent_submission_services import resolve_research_declaration
from backend.app.research_lifecycle import _try_neutral_lock
from backend.app.services import DomainError
from packages.factors.contracts import digest


@dataclass(frozen=True)
class PreparedProviderDeclaration:
    request: object
    config: object
    runtime_profile: object
    preflight: object


def prepare_provider_declaration(session, owner_id, request, settings):
    """Load operator/source/selected immutable inputs; never accept client pins.

    Must be called inside the normal owned shared lifecycle transaction. This is
    a fresh declaration, not accepted-run replay: selected guidance must still
    be active/current at this new boundary. Historical child replay uses its own
    immutable receipt instead. No model/provider process is started here.
    """
    return _prepare_provider_declaration(
        session, owner_id, request, settings, preflight_reader=provider_preflight
    )


def prepare_provider_declaration_under_native_guard(
    session, owner_id, request, settings
):
    """New workflow admission under the actual borrowed publication/native guard."""
    from backend.app.factor_native_io import require_native_storage_guard
    from backend.app.rdagent_provider_preflight import (
        provider_preflight_under_native_guard,
    )

    require_native_storage_guard(settings.storage_root)
    result = _prepare_provider_declaration(
        session,
        owner_id,
        request,
        settings,
        preflight_reader=provider_preflight_under_native_guard,
    )
    require_native_storage_guard(settings.storage_root)
    return result


def _prepare_provider_declaration(
    session, owner_id, request, settings, *, preflight_reader
):
    request = parse_provider_request(request.model_dump(mode="json"))
    _try_neutral_lock(session, f"submit:{owner_id}")
    checked = preflight_reader(session, owner_id, request, settings)
    if not checked.can_submit:
        raise DomainError(
            "engine_request_ineligible", "Provider declaration is not eligible", 409
        )
    profile = resolve_provider_runtime(settings)
    if digest(profile) != checked.runtime_profile_sha256:
        raise DomainError("runtime_profile_changed", "Server runtime changed", 409)
    config, _source = resolve_research_declaration(
        session,
        owner_id,
        request,
        settings,
        profile.runtime,
        expected_input_sha256=checked.frozen_input_sha256,
        model_binding=checked.model_binding,
        steer_snapshot=checked.steer_snapshot,
        controller_selection=profile.controller_profile,
    )
    return PreparedProviderDeclaration(request, config, profile, checked)


class _FrozenControllerSettings:
    """Internal read-through view: only a verified plan's controller selection.

    Research enablement, executable, provider transport and credentials remain
    current settings. Image/runtime pin changes remain detectable by comparing
    the freshly prepared declaration to the complete immutable plan template.
    """

    def __init__(self, settings, profile):
        self._settings = settings
        self.rdagent_provider_runtime_profile = profile

    def __getattr__(self, name):
        return getattr(self._settings, name)


def settings_for_frozen_controller(settings, template):
    from backend.app.rdagent_provider_profile import RDProviderServerRuntimeProfile
    from packages.rdagent_adapter.controller_profile import controller_selection_fields
    from packages.rdagent_adapter.provider_config import ProviderFactorResearchConfig

    # This is not accepted API authority. Caller has read the actual immutable
    # workflow plan; revalidation prevents model_copy/duck-typed config shortcuts.
    template = ProviderFactorResearchConfig.model_validate(template)
    current = resolve_provider_runtime(settings)
    value = current.model_dump(mode="python")
    value.pop("controller_profile", None)
    value.update(controller_selection_fields(template))
    profile = RDProviderServerRuntimeProfile.model_validate(value)
    return _FrozenControllerSettings(settings, profile)
