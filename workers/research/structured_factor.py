"""025 bounded maintained structured pair, never an arbitrary-policy dispatcher."""

from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import null, text

from backend.app.core.atomic_storage import provision_directory
from backend.app.core.maintenance import guarded_worker
from backend.app.factor_native_io import native_storage_guard
from backend.app.models import (
    DatasetVersion,
    ExecutionAttempt,
    ResearchRun,
    User,
    utcnow,
)
from backend.app.research_broker_models import ResearchBudgetControl
from backend.app.research_broker_services import initialize_budget
from backend.app.research_lifecycle import _try_neutral_lock, neutral_transaction
from backend.app.structured_factor_models import (
    StructuredStrategySpec,
    StructuredStrategyVersion,
)
from backend.app.structured_factor_services import prepare_structured_execution
from packages.factors.contracts import canonical_bytes, digest
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.fences import AttemptFence
from packages.research_runtime.process_contracts import ProcessError
from packages.research_runtime.resource_scopes import advisory_lock_plan
from packages.structured_factor_strategy.contracts import StructuredProgram
from packages.structured_factor_strategy.run import StructuredRunConfig
from runtimes.structured_factor_engine.recipe import dependency_lock_sha256


def run_registered_structured(*_args, **_kwargs):
    raise ProcessError("structured_catalog_not_registered")


@guarded_worker
def create_structured_acceptance_run(factory, *, owner_id, program, config, settings):
    """Explicit local/hosted acceptance initialization, not a user-facing launcher.

    Inputs must still pass fresh completed-snapshot and retained market checks in
    a current fenced transaction. A partial declaration grants no process claim.
    """
    program = StructuredProgram.model_validate(program)
    config = StructuredRunConfig.model_validate(config)
    if (
        type(owner_id) is not UUID
        or config.program_sha256 != digest(program)
        or config.engine_dependency_lock_sha256 != dependency_lock_sha256()
    ):
        raise ProcessError("structured_declaration")
    for directory in ("factors", "research-objects", "research-processes"):
        provision_directory(
            settings.storage_root / directory, durable_parent=settings.storage_root
        )
    run_id, attempt_id, token = uuid4(), uuid4(), uuid4()
    fence = AttemptFence(owner_id, run_id, attempt_id, 1, token)
    with factory.begin() as session:
        for lock in advisory_lock_plan(owner_id, "factor_numeric"):
            _try_neutral_lock(session, lock)
        if session.scalars(
            text("SELECT version_num FROM alembic_version")
        ).all() not in (
            ["025_structured_factor_strategy"],
            ["026_rd_seeded_submissions"],
            ["027_rd_controller_v3"],
            ["027a_contract_repairs"],
            ["027b_provider_material"],
        ):
            raise ProcessError("structured_schema_unsupported")
        dataset = session.get(DatasetVersion, config.dataset_version_id)
        if (
            session.get(User, owner_id) is None
            or dataset is None
            or dataset.owner_id != owner_id
            or dataset.content_sha256 != config.dataset_sha256
        ):
            raise ProcessError("structured_owned_dataset")
        version = session.get(
            StructuredStrategyVersion, config.structured_strategy_version_id
        )
        if version is None:
            version = StructuredStrategyVersion(
                id=config.structured_strategy_version_id,
                owner_id=owner_id,
                parent_version_id=None,
                program=program.model_dump(mode="json"),
                program_canonical=canonical_bytes(program).decode("ascii"),
                program_sha256=digest(program),
            )
            session.add(version)
            session.flush()
        elif (
            version.owner_id,
            version.program,
            version.program_canonical,
            version.program_sha256,
        ) != (
            owner_id,
            program.model_dump(mode="json"),
            canonical_bytes(program).decode("ascii"),
            digest(program),
        ):
            raise ProcessError("structured_program_drift")
        run = ResearchRun(
            id=run_id,
            owner_id=owner_id,
            idempotency_key=uuid4().hex,
            request_sha256=digest(config),
            config=config.model_dump(mode="json"),
            purpose="factor_strategy",
            engine="structured",
            dataset_version_id=dataset.id,
            strategy_version_id=None,
            submission_binding=null(),
            status="running",
            generation=1,
            research_image_digest=config.engine_image_id,
        )
        session.add(run)
        session.flush()
        session.add(
            ExecutionAttempt(
                id=attempt_id,
                run_id=run_id,
                generation=1,
                worker_id="structured_factor_acceptance_v1",
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
            StructuredStrategySpec(
                run_id=run_id,
                owner_id=owner_id,
                structured_strategy_version_id=version.id,
                config=config.model_dump(mode="json"),
                config_canonical=canonical_bytes(config).decode("ascii"),
                config_sha256=digest(config),
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
    with (
        native_storage_guard(settings.storage_root),
        neutral_transaction(factory, fence, resource_scope="factor_numeric") as session,
    ):
        initialize_budget(session, fence, config.budget)
        rows = tuple(
            prepare_structured_execution(session, fence, ordinal=i, settings=settings)
            for i in range(len(config.snapshots))
        )
    return fence, tuple(row.id for row in rows)


@guarded_worker
def finish_structured_acceptance_run(factory, fence, settings):
    """Finish only after every declared execution has a freshly verified stopped result."""
    from sqlalchemy import select

    from backend.app.research_process_models import ResearchProcessState
    from backend.app.structured_factor_results import read_verified_structured_execution
    from backend.app.structured_factor_services import _spec

    with (
        native_storage_guard(settings.storage_root),
        neutral_transaction(factory, fence, resource_scope="factor_numeric") as session,
    ):
        run, _, config, _ = _spec(session, fence)
        from backend.app.structured_factor_models import StructuredExecution

        executions = list(
            session.scalars(
                select(StructuredExecution)
                .filter_by(owner_id=fence.owner_id, run_id=fence.run_id)
                .order_by(StructuredExecution.ordinal)
                .limit(3)
            )
        )
        if tuple(x.snapshot_id for x in executions) != config.snapshots:
            raise ProcessError("structured_declared_pair_incomplete")
        results = tuple(
            read_verified_structured_execution(session, fence, x.id, settings)
            for x in executions
        )
        states = list(
            session.scalars(
                select(ResearchProcessState)
                .filter_by(
                    owner_id=fence.owner_id,
                    run_id=fence.run_id,
                    attempt_id=fence.attempt_id,
                    generation=fence.generation,
                )
                .limit(3)
            )
        )
        if {x.admission_id for x in states} != {x.admission_id for x in results} or any(
            x.status != "stopped" for x in states
        ):
            raise ProcessError("structured_whole_unit_stop_required")
        attempt = session.get(ExecutionAttempt, fence.attempt_id)
        run.status = attempt.status = "succeeded"
        attempt.sandbox_stopped = True
        attempt.finished_at = utcnow()
        # Multiple independently stopped units: no fabricated aggregate exit code.
        attempt.exit_code = None
        from backend.app.research_lifecycle import _NEUTRAL_KEY

        session.info[_NEUTRAL_KEY]["terminal"] = (
            "structured_succeeded",
            run.id,
            attempt.id,
            attempt.finished_at,
        )
        return results
