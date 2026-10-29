"""Revision023 queued declaration boundary; runtime catalog remains closed.

An initial queued run has generation counter1 but NO attempt, lease or fence.
Only the worker's later atomic claim may create real execution authority. This
module is flush-only and never launches, commits, rolls back or fabricates a
neutral transaction to call an attempt-only plan service.
"""

import re
from uuid import uuid4

from sqlalchemy import func, null, select, text

from backend.app.engine_run_preflight import CATALOG_REVISION, preflight
from backend.app.engine_run_submission_schemas import RDSubmissionReceipt
from backend.app.experiment_services import _identities
from backend.app.factor_analysis_models import FactorAnalysisPlan
from backend.app.models import (
    DatasetVersion,
    DataUsageExposure,
    OutboxEvent,
    ResearchRun,
)
from backend.app.rdagent_configured_exposure import PURPOSE, exposure_provenance
from backend.app.rdagent_configured_source import (
    derive_configured_labels,
    resolve_dataset_preflight,
)
from backend.app.rdagent_models import FactorResearchSpec
from backend.app.rdagent_submission_models import FactorResearchSubmission
from backend.app.rdagent_submission_receipts import existing_receipt, validate_key
from backend.app.research_broker_models import ResearchBudgetControl
from backend.app.research_lifecycle import _try_neutral_lock
from backend.app.research_services import TERMINAL, quota_limit
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.rdagent_adapter.contracts import FactorResearchConfig, FrozenResearchInput
from packages.research_runtime.budget_contracts import BudgetVector

REVISION = "023_rd_submissions"


def resolve_submission_profile(settings):
    # Explicit operator configuration after real acceptance, never client authority.
    from backend.app.rdagent_server_profile import RDServerRuntimeProfile

    if getattr(settings, "rdagent_research_enabled", False) is not True:
        raise DomainError(
            "engine_submission_not_enabled",
            "Configured RD production submission is disabled",
            409,
        )
    profile = getattr(settings, "rdagent_runtime_profile", None)
    if profile is None:
        raise DomainError(
            "engine_runtime_unconfigured",
            "The server has no configured RD runtime profile",
            409,
        )
    try:
        profile = RDServerRuntimeProfile.model_validate(profile)
    except ValueError:
        raise DomainError(
            "engine_runtime_unsupported",
            "The server RD runtime profile is unsupported",
            409,
        ) from None
    return profile


def resolve_submission_runtime(settings):
    profile = resolve_submission_profile(settings)
    return profile.runtime, profile.qlib_installed_parent_image_id


def submit_configured_run(session, owner_id, submitted, idempotency_key, settings):
    validate_key(idempotency_key)
    if session.scalars(text("SELECT version_num FROM alembic_version")).all() not in (
        [REVISION],
        ["024_openevolve_initial"],
        ["025_structured_factor_strategy"],
        ["026_rd_seeded_submissions"],
        ["027_rd_controller_v3"],
        ["027a_contract_repairs"],
        ["027b_provider_material"],
        ["027c_seeded_controller_v4"],
    ):
        raise DomainError(
            "engine_submission_schema_unavailable",
            "RD submission schema is unavailable",
            409,
        )
    _try_neutral_lock(session, f"submit:{owner_id}")
    previous = existing_receipt(session, owner_id, idempotency_key, submitted)
    if previous is not None:
        return previous
    from backend.app.comparison_services import reject_comparison_key

    reject_comparison_key(session, owner_id, idempotency_key)
    selected_profile = resolve_submission_profile(settings)
    runtime, installed_parent = (
        selected_profile.runtime,
        selected_profile.qlib_installed_parent_image_id,
    )
    return _persist_queued(
        session,
        owner_id,
        submitted,
        idempotency_key,
        settings,
        runtime,
        installed_parent,
        controller_selection=selected_profile.controller_profile,
    )


def _persist_queued(
    session,
    owner_id,
    submitted,
    key,
    settings,
    runtime,
    installed_parent,
    *,
    controller_selection=None,
):
    """Internal writer after server runtime selection; no runtime parameter on API.

    Reacquiring the transaction-level owner lock is harmless and makes this helper
    safe against a future internal caller forgetting the serialization boundary.
    """
    from backend.app.rdagent_server_profile import RDServerRuntimeProfile
    from packages.rdagent_adapter.contracts import RuntimePins

    validate_key(key)
    runtime = RuntimePins.model_validate(runtime)
    if (
        type(installed_parent) is not str
        or re.fullmatch(r"sha256:[a-f0-9]{64}", installed_parent) is None
    ):
        raise DomainError(
            "runtime_profile_invalid", "Exact server runtime pins required", 409
        )

    if session.scalars(text("SELECT version_num FROM alembic_version")).all() not in (
        [REVISION],
        ["024_openevolve_initial"],
        ["025_structured_factor_strategy"],
        ["026_rd_seeded_submissions"],
        ["027_rd_controller_v3"],
        ["027a_contract_repairs"],
        ["027b_provider_material"],
        ["027c_seeded_controller_v4"],
    ):
        raise DomainError(
            "engine_submission_schema_unavailable",
            "RD submission schema is unavailable",
            409,
        )
    _try_neutral_lock(session, f"submit:{owner_id}")
    request = submitted.request()
    checked = preflight(session, owner_id, request, settings)
    if checked.preflight_sha256 != submitted.preflight_sha256:
        raise DomainError(
            "preflight_changed",
            "Retained source or request changed; repeat preflight",
            409,
        )
    selected_profile_sha = digest(
        RDServerRuntimeProfile(
            runtime=runtime,
            qlib_installed_parent_image_id=installed_parent,
            **(
                {}
                if controller_selection is None
                else {"controller_profile": controller_selection}
            ),
        )
    )
    if not checked.can_submit or checked.runtime_profile_sha256 != selected_profile_sha:
        raise DomainError(
            "runtime_profile_changed",
            "Current server runtime is unavailable or changed",
            409,
        )
    if not checked.data_eligible:
        raise DomainError(
            "engine_request_ineligible",
            "Declared source or workload is not eligible",
            422,
        )
    run, spec, plan = _persist_queued_graph(
        session,
        owner_id,
        request,
        key,
        settings,
        runtime,
        installed_parent,
        expected_input_sha256=checked.frozen_input_sha256,
        controller_selection=controller_selection,
    )
    row = FactorResearchSubmission(
        id=uuid4(),
        owner_id=owner_id,
        run_id=run.id,
        plan_id=plan.id,
        idempotency_key=key,
        request_canonical=canonical_bytes(request).decode("utf-8"),
        request_sha256=request.sha256,
        preflight_sha256=checked.preflight_sha256,
        config_sha256=spec.config_sha256,
        catalog_revision=CATALOG_REVISION,
        runtime_profile_sha256=selected_profile_sha,
    )
    session.add(row)
    session.add(OutboxEvent(id=uuid4(), run_id=run.id, generation=1))
    session.flush()
    return RDSubmissionReceipt(
        run_id=run.id,
        plan_id=plan.id,
        request_sha256=row.request_sha256,
        preflight_sha256=row.preflight_sha256,
        replayed=False,
        status="queued",
    )


def _persist_queued_graph(
    session,
    owner_id,
    request,
    key,
    settings,
    runtime,
    installed_parent,
    *,
    expected_input_sha256,
    model_binding=None,
    steer_snapshot=None,
    controller_selection=None,
):
    """Shared flush-only graph, called after a versioned declaration preflight.

    This does not manufacture a submission receipt or execution authority. The
    caller must insert its exact versioned receipt and outbox in this same owner-
    serialized transaction; deferred SQL rejects an unclosed queued graph.
    """
    from workers.research.rdagent_configured import validate_configured_work

    if controller_selection == "mineagent.rdagent-orchestrator.v4":
        from backend.app.rdagent_provider_submission_contracts import (
            RDProviderSeedRequest,
        )

        if type(request) is not RDProviderSeedRequest or model_binding is None:
            raise DomainError(
                "seeded_controller_required",
                "Seeded v4 requires exact provider seed request",
                409,
            )
    validate_key(key)
    _try_neutral_lock(session, f"submit:{owner_id}")
    source = resolve_dataset_preflight(
        session,
        owner_id=owner_id,
        dataset_id=request.dataset_version_id,
        policy=request.source_policy,
        input_names=request.input_fields,
        settings=settings,
    )
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
    if frozen.sha256 != expected_input_sha256:
        raise DomainError(
            "preflight_changed", "Retained source changed during submission", 409
        )
    config_type = FactorResearchConfig
    extra = {}
    if model_binding is not None:
        from backend.app.rdagent_steer_services import verify_steer_snapshot
        from packages.rdagent_adapter.provider_config import (
            ProviderFactorResearchConfig,
        )

        if steer_snapshot is None:
            raise DomainError(
                "steer_snapshot_required", "Frozen guidance required", 409
            )
        verify_steer_snapshot(session, owner_id, steer_snapshot)
        config_type = ProviderFactorResearchConfig
        extra = {
            "model_binding": model_binding,
            "steer_snapshot": steer_snapshot,
            "steer_snapshot_sha256": steer_snapshot.sha256,
        }
    elif steer_snapshot is not None:
        raise DomainError("request_drift", "Fixture cannot consume model guidance", 409)
    if controller_selection is not None:
        extra["controller_profile"] = controller_selection
    config = config_type(
        **extra,
        input=frozen,
        task=request.task,
        runtime=runtime,
        budget=request.budget,
        max_iterations=request.max_iterations,
        max_output_rows_per_cutoff=10000,
        max_output_bytes_per_cutoff=16777216,
    )
    validate_configured_work(config, source, request.objective)
    label_source = resolve_dataset_preflight(
        session,
        owner_id=owner_id,
        dataset_id=request.dataset_version_id,
        policy=request.source_policy,
        input_names=("close",),
        settings=settings,
    )
    if (label_source.dataset, label_source.universe, label_source.calendar.sha256) != (
        source.dataset,
        source.universe,
        source.calendar.sha256,
    ):
        raise DomainError(
            "preflight_changed", "Separate retained label source changed", 409
        )
    derive_configured_labels(label_source, request.objective)
    active = session.scalar(
        select(func.count())
        .select_from(ResearchRun)
        .where(ResearchRun.owner_id == owner_id, ResearchRun.status.not_in(TERMINAL))
    )
    if active >= quota_limit(session, owner_id, settings):
        raise DomainError("run_quota", "Active run limit reached", 429)
    run = ResearchRun(
        id=uuid4(),
        owner_id=owner_id,
        idempotency_key=key,
        request_sha256=config.sha256,
        config=config.model_dump(mode="json"),
        purpose="factor_research",
        engine="rdagent",
        dataset_version_id=request.dataset_version_id,
        strategy_version_id=None,
        submission_binding=null(),
        status="queued",
        generation=1,
        active_attempt_id=None,
        research_image_digest=config.runtime.orchestrator_image,
    )
    session.add(run)
    session.flush()
    spec = FactorResearchSpec(
        id=uuid4(),
        owner_id=owner_id,
        run_id=run.id,
        config=config.model_dump(mode="json"),
        config_sha256=config.sha256,
        input_sha256=config.input.sha256,
    )
    session.add(spec)
    session.flush()
    plan = FactorAnalysisPlan(
        id=uuid4(),
        owner_id=owner_id,
        run_id=run.id,
        spec_id=spec.id,
        config_sha256=config.sha256,
        input_sha256=config.input.sha256,
        objective=request.objective.model_dump(mode="json"),
        objective_canonical=canonical_bytes(request.objective).decode("utf-8"),
        objective_sha256=request.objective.sha256,
        source_policy=request.source_policy.model_dump(mode="json"),
        source_policy_sha256=digest(request.source_policy),
        label_source_dataset_id=request.dataset_version_id,
        label_source_manifest_sha256=source.dataset.content_sha256,
        qlib_image_id=runtime.analysis_image,
        qlib_installed_parent_image_id=installed_parent,
        analysis_dependency_lock_sha256={
            item.role: item.sha256 for item in runtime.dependency_locks
        }["analysis"],
    )
    session.add(plan)
    session.flush()
    dataset = session.get(DatasetVersion, request.dataset_version_id)
    session.add(
        DataUsageExposure(
            id=uuid4(),
            owner_id=owner_id,
            dataset_version_id=dataset.id,
            experiment_id=None,
            split_version_id=None,
            run_id=run.id,
            source_key="rd-configured-source:" + str(plan.id),
            identities=_identities(session, dataset),
            start_date=dataset.start_date,
            end_date=dataset.end_date,
            purpose=PURPOSE,
            provenance=exposure_provenance(plan),
        )
    )
    session.add(
        ResearchBudgetControl(
            id=uuid4(),
            owner_id=owner_id,
            run_id=run.id,
            limits=config.budget.model_dump(),
            used=BudgetVector().model_dump(),
            reserved=BudgetVector().model_dump(),
        )
    )
    return run, spec, plan
