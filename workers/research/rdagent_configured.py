"""Configured internal run initialization; default public submission stays closed.

This initial bounded profile supports one declared output session and one CoSTEER
iteration under the explicit immutable controller deadline (legacy60s or composed120s). It is not a generic
queue/claim/resume implementation. Runtime catalog admission remains separate.
"""

from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import null, text

from backend.app.core.atomic_storage import provision_directory
from backend.app.factor_analysis_services import prepare_analysis_plan
from backend.app.models import (
    DatasetVersion,
    ExecutionAttempt,
    ResearchRun,
    User,
    utcnow,
)
from backend.app.rdagent_configured_source import resolve_dataset_preflight
from backend.app.rdagent_models import FactorResearchSpec
from backend.app.rdagent_services import bind_research_attempt
from backend.app.research_broker_models import ResearchBudgetControl
from backend.app.research_broker_services import initialize_budget
from backend.app.research_lifecycle import neutral_transaction
from packages.qlib_adapter.objective import DirectObjectiveDeclaration
from packages.rdagent_adapter.configured_source import ConfiguredSourcePolicy
from packages.rdagent_adapter.contracts import FactorResearchConfig
from packages.rdagent_adapter.controller_profile import (
    composed_wall_floor,
    controller_profile,
)
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.fences import AttemptFence
from packages.research_runtime.process_contracts import ProcessError


def validate_configured_work(config, source, objective):
    days = tuple(
        day
        for day in source.calendar.sessions
        if config.input.window.start <= day.session_date < config.input.window.end
    )
    if (
        config.max_iterations != 1
        or len(days) != 1
        or config.model_profile not in ("fixture.rdagent.v1", "configured.rdagent.v1")
    ):
        raise ProcessError("configured_work_exceeds_controller_profile")
    if config.model_profile == "configured.rdagent.v1":
        from backend.app.rdagent_provider_preflight import provider_budget_floor
        from packages.rdagent_adapter.provider_config import (
            ProviderFactorResearchConfig,
        )

        if type(config) is not ProviderFactorResearchConfig or not config.budget.covers(
            provider_budget_floor(
                config.model_binding, profile=controller_profile(config)
            )
        ):
            raise ProcessError("configured_model_budget_floor")
    if objective.window != config.input.window:
        raise ProcessError("configured_objective_window")
    if (
        source.dataset,
        source.universe,
        source.calendar.sha256,
        source.calendar.timezone,
        source.input_fields,
    ) != (
        config.input.dataset,
        config.input.calculation_universe,
        config.input.calendar_sha256,
        config.input.market_timezone,
        config.input.input_fields,
    ):
        raise ProcessError("configured_frozen_source_drift")
    # One controller plus three child units (candidate, normalizer, analysis).
    # These are reservation floors, not predictions or permission to extend60s.
    if (
        config.budget.process_slots < 4
        or config.budget.storage_bytes < 512 * 1024**2
        or config.budget.wall_milliseconds
        < composed_wall_floor(controller_profile(config))
    ):
        raise ProcessError("configured_work_budget_floor")


def require_controller_revision(session, config):
    revisions = session.scalars(text("SELECT version_num FROM alembic_version")).all()
    if revisions not in (
        ["022_factor_analysis"],
        ["023_rd_submissions"],
        ["024_openevolve_initial"],
        ["025_structured_factor_strategy"],
        ["026_rd_seeded_submissions"],
        ["027_rd_controller_v3"],
    ) or (
        config.controller_profile is not None and revisions != ["027_rd_controller_v3"]
    ):
        raise ProcessError("rd_controller_revision")


def create_configured_run(
    factory,
    *,
    owner_id,
    dataset_id,
    config,
    objective,
    source_policy,
    qlib_installed_parent_image_id,
    settings,
):
    config = FactorResearchConfig.model_validate(config)
    objective = DirectObjectiveDeclaration.model_validate(objective)
    source_policy = ConfiguredSourcePolicy.model_validate(source_policy)
    if (
        type(owner_id) is not UUID
        or type(dataset_id) is not UUID
        or config.input.dataset.dataset_version_id != dataset_id
    ):
        raise ProcessError("configured_owned_dataset")
    with factory.begin() as session:
        require_controller_revision(session, config)
        source = resolve_dataset_preflight(
            session,
            owner_id=owner_id,
            dataset_id=dataset_id,
            policy=source_policy,
            input_names=tuple(field.name for field in config.input.input_fields),
            settings=settings,
        )
        validate_configured_work(config, source, objective)
    for directory in ("factors", "research-objects", "research-processes"):
        provision_directory(
            settings.storage_root / directory, durable_parent=settings.storage_root
        )
    run_id, attempt_id, token = uuid4(), uuid4(), uuid4()
    fence = AttemptFence(owner_id, run_id, attempt_id, 1, token)
    with factory.begin() as session:
        require_controller_revision(session, config)
        dataset = session.get(DatasetVersion, dataset_id)
        if (
            session.get(User, owner_id) is None
            or dataset is None
            or dataset.owner_id != owner_id
            or dataset.content_sha256 != config.input.dataset.content_sha256
        ):
            raise ProcessError("rd_controller_owned_dataset")
        run = ResearchRun(
            id=run_id,
            owner_id=owner_id,
            idempotency_key=uuid4().hex,
            request_sha256=config.sha256,
            config=config.model_dump(mode="json"),
            purpose="factor_research",
            engine="rdagent",
            dataset_version_id=dataset_id,
            strategy_version_id=None,
            submission_binding=null(),
            status="running",
            generation=1,
            research_image_digest=config.runtime.orchestrator_image,
        )
        session.add(run)
        session.flush()
        session.add(
            ExecutionAttempt(
                id=attempt_id,
                run_id=run_id,
                generation=1,
                worker_id="configured_rdagent_internal_acceptance_v1",
                lease_token=token,
                lease_expires_at=utcnow() + timedelta(minutes=10),
                status="running",
                sandbox_id="not-launched:" + attempt_id.hex,
                sandbox_stopped=False,
            )
        )
        session.flush()
        run.active_attempt_id = attempt_id
        session.add(
            FactorResearchSpec(
                id=uuid4(),
                owner_id=owner_id,
                run_id=run_id,
                config=config.model_dump(mode="json"),
                config_sha256=config.sha256,
                input_sha256=config.input.sha256,
            )
        )
        session.add(
            ResearchBudgetControl(
                id=uuid4(),
                owner_id=owner_id,
                run_id=run_id,
                limits=config.budget.model_dump(),
                used=BudgetVector().model_dump(),
                reserved=BudgetVector().model_dump(),
            )
        )
    with neutral_transaction(
        factory, fence, resource_scope="factor_numeric"
    ) as session:
        initialize_budget(session, fence, config.budget)
        bind_research_attempt(session, fence)
        prepare_analysis_plan(
            session,
            fence,
            objective=objective,
            source_policy=source_policy,
            qlib_installed_parent_image_id=qlib_installed_parent_image_id,
            settings=settings,
        )
    return fence


def execute_configured_controller(factory, fence, settings):
    from backend.app.factor_analysis_services import _plan
    from backend.app.rdagent_configured_handler import ConfiguredHandler
    from backend.app.rdagent_configured_source import resolve_configured_source
    from backend.app.rdagent_services import require_research_spec
    from workers.research.rdagent_operational import _execute_controller

    with neutral_transaction(
        factory, fence, resource_scope="factor_numeric"
    ) as session:
        _, config = require_research_spec(session, fence)
        source = resolve_configured_source(session, fence, settings)
        _plan_row, objective = _plan(session, fence)
        validate_configured_work(config, source, objective)
    handler_type = ConfiguredHandler
    if config.schema_version == "mineagent.factor-research.v2":
        from backend.app.rdagent_provider_handler import ProviderHandler

        handler_type = ProviderHandler
    return _execute_controller(factory, fence, settings, handler_type=handler_type)
