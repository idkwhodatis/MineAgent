"""Fresh027 eligibility/quote; every submit repeats this under retained guards."""

from sqlalchemy import text

from backend.app.oe_structured_provider_submission import (
    ProviderPreflightReason,
    ProviderStructuredPreflight,
)
from backend.app.oe_structured_server_profile import (
    resolve_operator_profile,
    resolve_runtime,
)
from backend.app.services import DomainError
from packages.factors.contracts import digest
from packages.openevolve_structured.provider_accounting import (
    INPUT_TOKEN_BOUND,
    PIPE_PEAK_BYTES,
)
from packages.openevolve_structured.provider_run import FixedExecutionInputs
from packages.research_runtime.budget_contracts import BudgetVector
from runtimes.openevolve_structured.profile import MAX_WALL_MS, STORAGE_FLOOR

REVISION = "028_oe_structured_search"
CATALOG = "mineagent.oe-structured-operator-catalog.v1"


def freeze_inputs(data):
    envelope = data.envelope()
    declared = data.market.qualification == "user_declaration_unverified"
    return FixedExecutionInputs(
        schema_version="mineagent.oe-structured-retained-inputs.v2"
        if declared
        else "mineagent.oe-structured-retained-inputs.v1",
        market_qualification="user_declaration_unverified"
        if declared
        else "synthetic_by_construction",
        snapshot_id=data.manifest.snapshot_id,
        snapshot_manifest_sha256=envelope["snapshot_manifest_sha256"],
        snapshot_input_sha256=data.manifest.input_sha256,
        snapshot_output_sha256=data.manifest.output_rows_sha256,
        factor_version_id=data.manifest.input.version.factor_version_id,
        dataset_version_id=data.source.dataset.dataset_version_id,
        dataset_sha256=data.source.dataset.content_sha256,
        source_policy=data.source_policy,
        market_sha256=envelope["market_sha256"],
        market_size_bytes=envelope["market_size_bytes"],
        release_receipt_ids=tuple(sorted(data.release_receipt_ids, key=str)),
    )


def budget_floor(request, binding):
    rates = binding.pricing
    if rates.status != "operator_declared":
        raise ValueError("Declared price estimate required")
    mutations = request.maximum_mutation_slots
    evaluations = 1 + mutations
    estimate = (
        INPUT_TOKEN_BOUND * rates.input_microusd_per_million
        + binding.max_output_tokens * rates.output_microusd_per_million
    )
    # Includes conservative controller/native physical floors and each SDK pipe;
    # native output/checkpoint object holds remain separately charged at runtime.
    return BudgetVector(
        calls=mutations,
        tokens=mutations * (INPUT_TOKEN_BOUND + binding.max_output_tokens),
        cost_microunits=mutations * ((estimate + 999999) // 1000000),
        mutation_slots=mutations,
        evaluator_executions=evaluations,
        process_slots=request.required_process_units,
        wall_milliseconds=MAX_WALL_MS
        + 90000 * evaluations
        + mutations * binding.request_deadline_ms,
        storage_bytes=STORAGE_FLOOR
        + 268500992 * evaluations
        + mutations * PIPE_PEAK_BYTES
        + 64 * 1024**2,
    )


def evaluate(session, owner_id, request, settings, data):
    """Internal current-read evaluation. `data` never bypasses submit's own reader."""
    reasons = []
    runtime = binding = None
    required = None
    if session.scalars(text("SELECT version_num FROM alembic_version")).all() not in (
        [REVISION],
        ["029_research_workflow"],
        ["030_declared_upload_basket"],
        ["031_workflow_structured_stage"],
        ["032_normal_oe_note_pins"],
        ["032a_rd_terminal_results"],
    ):
        reasons.append(
            ProviderPreflightReason(
                code="runtime_schema_unavailable",
                message="Structured search schema is unavailable",
            )
        )
    try:
        runtime = resolve_runtime(settings)
        _, binding = resolve_operator_profile(
            settings, owner_id, request.model_profile_id
        )
        request.require_binding(binding)
        required = budget_floor(request, binding)
        if not request.budget.covers(required):
            reasons.append(
                ProviderPreflightReason(
                    code="budget_below_profile",
                    message="Budget cannot cover the declared controller, native evaluations and SDK calls",
                )
            )
    except DomainError as error:
        reasons.append(
            ProviderPreflightReason(
                code=error.code,
                message="Owned operator runtime/model configuration is unavailable",
            )
        )
    except ValueError:
        binding = None
        reasons.append(
            ProviderPreflightReason(
                code="model_binding_changed",
                message="Operator model binding changed; preflight again",
            )
        )
    fixed = freeze_inputs(data)
    declaration = {
        "catalog": CATALOG,
        "request_sha256": request.sha256,
        "fixed_input_sha256": digest(fixed),
        "runtime_profile_sha256": digest(runtime) if runtime else None,
        "model_binding_sha256": binding.sha256 if binding else None,
        "required_budget": required.model_dump() if required else None,
        "reasons": [x.model_dump(mode="json") for x in reasons],
    }
    checked = ProviderStructuredPreflight(
        normalized_request=request,
        request_sha256=request.sha256,
        preflight_sha256=digest(declaration),
        fixed_input_sha256=digest(fixed),
        runtime_profile_sha256=declaration["runtime_profile_sha256"],
        model_binding=binding,
        required_budget=required,
        required_process_units=request.required_process_units,
        data_eligible=True,
        data_qualification=fixed.market_qualification,
        can_submit=not reasons,
        reasons=tuple(reasons),
    )
    return checked, fixed, runtime, binding


def preflight(factory, owner_id, request, settings):
    """Public normal preflight, never queue/execute or create a missing release."""
    from backend.app.core.maintenance import shared_lifecycle
    from backend.app.oe_structured_provider_input import retained_submission_data
    from backend.app.oe_structured_provider_submission import (
        ProviderStructuredSearchRequest,
    )
    from packages.openevolve_structured.candidate import _raw_fields

    if type(request) is not ProviderStructuredSearchRequest:
        raise DomainError(
            "invalid_request", "Exact structured provider request required", 422
        )
    request = ProviderStructuredSearchRequest.model_validate(_raw_fields(request))
    try:
        with (
            shared_lifecycle(factory.kw["bind"], settings.storage_root),
            factory() as session,
            retained_submission_data(session, owner_id, request, settings) as data,
        ):
            checked, _, _, _ = evaluate(session, owner_id, request, settings, data)
            return checked
    except DomainError as error:
        # The owned-reader uses fixed public domain codes, not raw paths or model
        # responses. No fake hashes/eligible input appear when reconstruction fails.
        reason = ProviderPreflightReason(
            code=error.code,
            message="Owned released snapshot or supported retained market is unavailable",
        )
        return ProviderStructuredPreflight(
            normalized_request=request,
            request_sha256=request.sha256,
            preflight_sha256=digest(
                {
                    "catalog": CATALOG,
                    "request_sha256": request.sha256,
                    "source_error": reason.code,
                }
            ),
            fixed_input_sha256=None,
            runtime_profile_sha256=None,
            model_binding=None,
            required_budget=None,
            required_process_units=request.required_process_units,
            data_eligible=False,
            data_qualification=None,
            can_submit=False,
            reasons=(reason,),
        )
