"""Owned retained-data preflight. Read-only; never creates a run, plan or grant."""

from sqlalchemy import select, text

from backend.app.engine_run_submission_schemas import (
    EngineDatasetOption,
    PreflightReason,
    RDPreflight,
    RDSubmissionRequest,
)
from backend.app.models import DatasetVersion
from backend.app.rdagent_configured_source import resolve_dataset_preflight
from backend.app.services import DomainError
from packages.factors.contracts import digest
from packages.rdagent_adapter.contracts import FrozenResearchInput
from packages.rdagent_adapter.controller_profile import (
    composed_wall_floor,
    controller_profile,
    controller_selection_fields,
)
from packages.research_runtime.budget_contracts import BudgetVector

CATALOG_REVISION = "configured_rd_single_output_session_v1"
RECOMMENDED_BUDGET = BudgetVector(
    calls=32,
    tokens=200000,
    mutation_slots=1,
    evaluator_executions=1,
    wall_milliseconds=240000,
    storage_bytes=536870912,
    process_slots=4,
)


def reason(code, message):
    return PreflightReason(code=code, message=message)


def dataset_options(session, owner_id, offset=0, limit=25):
    from backend.app.rdagent_snapshot_config import declared_upload_in_session

    declared_installed = declared_upload_in_session(session)

    def supported(row):
        return row.manifest.get("schema_version") == "marketdata.v1" or (
            declared_installed
            and row.manifest.get("schema_version") == 3
            and row.manifest.get("profile") == "mineagent.declared-upload-basket.v1"
        )

    rows = session.scalars(
        select(DatasetVersion)
        .filter_by(owner_id=owner_id)
        .order_by(DatasetVersion.created_at.desc(), DatasetVersion.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return [
        EngineDatasetOption(
            id=row.id,
            source=row.source,
            name=row.name,
            start_date=row.start_date,
            end_date=row.end_date,
            market=row.market,
            is_synthetic=row.is_synthetic,
            content_sha256=row.content_sha256,
            eligibility="not_checked" if supported(row) else "unsupported",
            reason=None
            if supported(row)
            else reason(
                "dataset_format_unsupported",
                "This initial profile requires retained marketdata.v1 daily source artifacts.",
            ),
        )
        for row in rows
    ]


def preflight(session, owner_id, request, settings):
    request = RDSubmissionRequest.model_validate(request)
    dataset = session.scalar(
        select(DatasetVersion).filter_by(
            owner_id=owner_id, id=request.dataset_version_id
        )
    )
    if dataset is None:
        raise DomainError("not_found", "Dataset not found", 404)
    reasons = []
    frozen = None
    cutoffs = 0
    if dataset.manifest.get("schema_version") != "marketdata.v1":
        reasons.append(
            reason(
                "dataset_format_unsupported",
                "This profile requires retained marketdata.v1 daily source artifacts.",
            )
        )
    else:
        try:
            source = resolve_dataset_preflight(
                session,
                owner_id=owner_id,
                dataset_id=dataset.id,
                policy=request.source_policy,
                input_names=request.input_fields,
                settings=settings,
            )
        except DomainError:
            reasons.append(
                reason(
                    "dataset_bytes_unavailable",
                    "The retained source bytes, daily calendar, listing identity or declared price semantics cannot be verified.",
                )
            )
        else:
            days = tuple(day.session_date for day in source.calendar.sessions)
            selected = [
                day for day in days if request.window.start <= day < request.window.end
            ]
            cutoffs = len(selected)
            try:
                frozen = FrozenResearchInput(
                    dataset=source.dataset,
                    calculation_universe=source.universe,
                    window=request.window,
                    calendar_sha256=source.calendar.sha256,
                    market_timezone=source.calendar.timezone,
                    input_fields=source.input_fields,
                    lookback_sessions=request.lookback_sessions,
                    lag_sessions=request.lag_sessions,
                )
            except ValueError:
                reasons.append(
                    reason(
                        "source_projection_unsupported",
                        "The requested field, price, membership or temporal declaration is unsupported.",
                    )
                )
            if (
                not source.source_start
                <= request.window.start
                < request.window.end
                <= source.source_end
                or len(selected) != 1
                or request.max_iterations != 1
            ):
                reasons.append(
                    reason(
                        "output_window_unsupported",
                        "The accepted deadline profile supports exactly one output session and one iteration; historical lookback is separate and is not truncated.",
                    )
                )
            if selected and days.index(selected[0]) < request.lookback_sessions:
                reasons.append(
                    reason(
                        "insufficient_history",
                        "Retained sessions do not cover the declared prior-session lookback.",
                    )
                )
            if selected and days.index(
                selected[-1]
            ) + request.objective.label_horizon_sessions >= len(days):
                reasons.append(
                    reason(
                        "insufficient_label_horizon",
                        "Retained sessions do not cover the entire declared forward-label horizon.",
                    )
                )
            if request.lag_sessions != 0:
                reasons.append(
                    reason(
                        "delayed_output_unsupported",
                        "Initial direct-factor analysis requires features available at their output event; delayed outputs need a separately accepted alignment profile.",
                    )
                )
            if request.objective.minimum_defined_days > len(
                selected
            ) or request.objective.minimum_pairs > len(selected) * len(
                source.universe.listing_ids
            ):
                reasons.append(
                    reason(
                        "objective_coverage_impossible",
                        "The declared minimum analysis coverage exceeds the possible output sessions or listing pairs.",
                    )
                )
    if not request.budget.covers(RECOMMENDED_BUDGET):
        reasons.append(
            reason(
                "budget_below_profile",
                "The declared budget is below the server profile floor for controller, candidate, normalizer, analysis and retained evidence.",
            )
        )
    eligible = not reasons
    profile_sha = None
    runtime_available = False
    from backend.app.rdagent_server_profile import RDServerRuntimeProfile
    from backend.app.rdagent_submission_services import resolve_submission_runtime

    try:
        runtime, parent = resolve_submission_runtime(settings)
        profile_sha = digest(
            RDServerRuntimeProfile(
                runtime=runtime,
                qlib_installed_parent_image_id=parent,
                **controller_selection_fields(
                    RDServerRuntimeProfile.model_validate(
                        settings.rdagent_runtime_profile
                    )
                ),
            )
        )
        selected_profile = RDServerRuntimeProfile.model_validate(
            settings.rdagent_runtime_profile
        )
        if request.budget.wall_milliseconds < composed_wall_floor(
            controller_profile(selected_profile)
        ):
            eligible = False
            if not any(item.code == "budget_below_profile" for item in reasons):
                reasons.append(
                    reason(
                        "budget_below_profile",
                        "Budget is below the explicitly selected controller profile floor.",
                    )
                )
        if session.scalars(text("SELECT version_num FROM alembic_version")).all() in (
            ["023_rd_submissions"],
            ["024_openevolve_initial"],
            ["025_structured_factor_strategy"],
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
        ) and (
            selected_profile.controller_profile is None
            or session.scalars(text("SELECT version_num FROM alembic_version")).all()
            in (
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
            )
        ):
            runtime_available = True
        else:
            reasons.append(
                reason(
                    "runtime_schema_unavailable",
                    "The configured RD submission schema is not installed.",
                )
            )
    except DomainError as exc:
        code = {
            "engine_submission_not_enabled": "runtime_disabled",
            "engine_runtime_unconfigured": "runtime_unconfigured",
            "engine_runtime_unsupported": "runtime_unsupported",
        }[exc.code]
        reasons.append(reason(code, exc.message))
    # The fixture model is not a general text-to-factor provider. Never accept
    # an unrelated task and silently substitute its fixed maintained source.
    task_supported = (
        "close" in request.input_fields
        and request.task.factor_kind == "time_series"
        and request.task.formulation
        == f"close[t]/close[t-{request.lookback_sessions}]-1"
        and request.task.output_unit == "return"
    )
    if not task_supported:
        runtime_available = False
        reasons.append(
            reason(
                "task_profile_unsupported",
                "The fixed fixture model supports only time-series close[t]/close[t-L]-1 with the declared lookback L, close input and return output; arbitrary task generation is not enabled.",
            )
        )
    commitment = {
        "schema_version": "mineagent.rdagent-preflight-binding.v1",
        "owner_id": str(owner_id),
        "request_sha256": request.sha256,
        "dataset_content_sha256": dataset.content_sha256,
        "frozen_input_sha256": None if frozen is None else frozen.sha256,
        "catalog_revision": CATALOG_REVISION,
        "runtime_profile_sha256": profile_sha,
    }
    return RDPreflight(
        normalized_request=request,
        request_sha256=request.sha256,
        preflight_sha256=digest(commitment),
        dataset_content_sha256=dataset.content_sha256,
        frozen_input_sha256=None if frozen is None else frozen.sha256,
        data_eligible=eligible,
        can_submit=eligible and runtime_available,
        runtime_profile_sha256=profile_sha,
        reasons=tuple(reasons),
        cutoff_count=cutoffs,
        required_process_units=1 + request.max_iterations * (2 * cutoffs + 1),
        output_window=request.window,
    )
