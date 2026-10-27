"""Explicit product availability, independent of installed schema capabilities.

The configured RD fixture requires explicit operator profile and exact schema.
Adding persistence or an image must never implicitly enable the public entry.
"""

from backend.app.engine_run_preflight import RECOMMENDED_BUDGET
from backend.app.engine_run_schemas import EngineCapabilities, EngineCapability


def runtime_catalog(settings=None, schema_revision=None, *, owner_id=None):
    from backend.app.rdagent_submission_services import resolve_submission_runtime
    from backend.app.services import DomainError

    enabled = False
    code = "production_submission_not_enabled"
    if settings is not None:
        try:
            resolve_submission_runtime(settings)
            if schema_revision in {
                "023_rd_submissions",
                "024_openevolve_initial",
                "025_structured_factor_strategy",
                "026_rd_seeded_submissions",
            }:
                enabled, code = True, "configured_fixture_profile_available"
            else:
                code = "runtime_schema_unavailable"
        except DomainError as exc:
            code = exc.code
    options = ()
    if settings is not None and owner_id is not None:
        from backend.app.rdagent_provider_catalog import provider_options

        options = provider_options(settings, owner_id, schema_revision)
    return EngineCapabilities(
        engines=[
            EngineCapability(
                purpose="factor_research",
                engine="rdagent",
                can_read_history=True,
                can_preflight=True,
                recommended_budget=RECOMMENDED_BUDGET,
                max_output_sessions=1,
                max_iterations=1,
                model_profile="fixture.rdagent.v1",
                can_create=enabled,
                configured_model_can_create=any(
                    option.can_create for option in options
                ),
                model_profiles=options,
                configured_model_reason_code=(
                    "configured_model_profile_available"
                    if any(option.can_create for option in options)
                    else (
                        "rd_model_unconfigured"
                        if not options
                        else "rd_model_profiles_unavailable"
                    )
                ),
                reason_code=code,
            ),
            EngineCapability(
                purpose="strategy_search",
                engine="openevolve",
                can_read_history=False,
                reason_code="engine_history_not_enabled",
            ),
        ]
    )
