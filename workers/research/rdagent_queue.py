"""RD queue delivery creates a real first attempt, never a legacy substitute.

Fresh-only initial profile: duplicate deliveries cannot restart an existing
attempt, and source/plan drift is rejected before any image inspection. Public
runtime selection is still closed pending the actual queue/controller gate.
"""

from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, text

from backend.app.engine_run_preflight import preflight
from backend.app.engine_run_submission_schemas import RDSubmit
from backend.app.factor_analysis_models import FactorAnalysisPlan
from backend.app.models import ExecutionAttempt, OutboxEvent, ResearchRun, utcnow
from backend.app.rdagent_services import bind_research_attempt
from backend.app.rdagent_submission_models import FactorResearchSubmission
from backend.app.rdagent_submission_receipts import existing_receipt, read_request
from backend.app.rdagent_submission_services import REVISION, resolve_submission_runtime
from backend.app.research_broker_services import initialize_budget
from backend.app.research_lifecycle import _try_neutral_lock, neutral_transaction
from backend.app.services import DomainError
from packages.rdagent_adapter.persisted_identity import require_source_identity
from packages.research_runtime.fences import AttemptFence

TASK_NAME = "mineagent.execute_rd_run"


def claim_configured_run(factory, run_id, generation, worker_id, settings):
    with factory.begin() as session:
        fence = _claim(session, run_id, generation, worker_id, settings, None, None)
    if fence is None:
        return None
    with neutral_transaction(
        factory, fence, resource_scope="factor_numeric"
    ) as session:
        config = require_source_identity(session.get(ResearchRun, fence.run_id))
        initialize_budget(session, fence, config.budget)
        bind_research_attempt(session, fence)
    return fence


def _claim(session, run_id, generation, worker_id, settings, runtime, installed_parent):
    if (
        type(run_id) is not UUID
        or type(generation) is not int
        or generation != 1
        or type(worker_id) is not str
        or not 1 <= len(worker_id) <= 255
    ):
        raise DomainError("rd_claim_invalid", "Exact fresh RD delivery required", 409)
    revisions = session.scalars(text("SELECT version_num FROM alembic_version")).all()
    if revisions not in (
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
            "engine_submission_not_enabled", "RD queue schema unavailable", 409
        )
    owner = session.scalar(select(ResearchRun.owner_id).where(ResearchRun.id == run_id))
    if owner is None:
        return None
    _try_neutral_lock(session, f"submit:{owner}")
    run = session.scalar(
        select(ResearchRun)
        .filter_by(owner_id=owner, id=run_id)
        .with_for_update(nowait=True)
    )
    if run is None or run.status != "queued" or run.generation != generation:
        return None
    config = require_source_identity(run)
    if (
        run.active_attempt_id is not None
        or session.scalar(select(ExecutionAttempt.id).filter_by(run_id=run_id).limit(1))
        is not None
    ):
        return None
    row = session.scalar(
        select(FactorResearchSubmission).filter_by(owner_id=owner, run_id=run_id)
    )
    event = session.scalar(
        select(OutboxEvent).filter_by(run_id=run_id, generation=generation)
    )
    from workers.research.rdagent_seed_queue import (
        require_seeded_queue_binding,
        seeded_row,
    )

    seeded = seeded_row(session, run, revisions)
    if (
        event is None
        or (row is None and seeded is None)
        or (row is not None and seeded is not None)
    ):
        raise DomainError(
            "rd_claim_unbound", "Committed submission and outbox required", 409
        )
    if seeded is not None:
        require_seeded_queue_binding(
            session, run, seeded, config, settings, read_inputs=True
        )
    elif config.schema_version == "mineagent.factor-research.v2":
        from workers.research.rdagent_provider_queue import (
            require_provider_queue_binding,
        )

        require_provider_queue_binding(
            session, run, row, config, settings, read_inputs=True
        )
    else:
        if runtime is None:
            runtime, installed_parent = resolve_submission_runtime(settings)
        request = read_request(row)
        submitted = RDSubmit(
            **request.model_dump(), preflight_sha256=row.preflight_sha256
        )
        existing_receipt(session, owner, run.idempotency_key, submitted)
        plan = session.get(FactorAnalysisPlan, row.plan_id)
        if (
            runtime != config.runtime
            or plan.qlib_installed_parent_image_id != installed_parent
        ):
            raise DomainError(
                "runtime_profile_changed",
                "Queued immutable runtime differs from accepted server profile",
                409,
            )
        checked = preflight(session, owner, request, settings)
        if not checked.can_submit or checked.preflight_sha256 != row.preflight_sha256:
            raise DomainError(
                "preflight_changed",
                "Queued retained source no longer matches declaration",
                409,
            )
    now = utcnow()
    attempt = ExecutionAttempt(
        id=uuid4(),
        run_id=run.id,
        generation=generation,
        worker_id=worker_id,
        lease_token=uuid4(),
        lease_expires_at=now + timedelta(seconds=settings.lease_seconds),
        heartbeat_at=now,
        status="running",
        sandbox_id="not-launched:rd-queued",
        sandbox_stopped=False,
    )
    session.add(attempt)
    session.flush()
    run.active_attempt_id = attempt.id
    run.status = "running"
    session.flush()
    return AttemptFence(owner, run.id, attempt.id, generation, attempt.lease_token)


def execute(run_id, generation, *, settings):
    """Queue callback; a duplicate cannot rerun a claimed or terminal generation."""
    import socket

    from backend.app.core.db import create_db_engine, session_factory
    from workers.research.lease import AttemptLease
    from workers.research.rdagent_configured import execute_configured_controller

    engine = create_db_engine(settings)
    factory = session_factory(engine)
    worker_id = f"rdagent:{socket.gethostname()}:{uuid4()}"
    from backend.app.core.atomic_storage import provision_directory
    from backend.app.core.maintenance import shared_lifecycle

    try:
        with shared_lifecycle(factory.kw["bind"], settings.storage_root):
            for directory in ("factors", "research-objects", "research-processes"):
                provision_directory(
                    settings.storage_root / directory,
                    durable_parent=settings.storage_root,
                )
            fence = claim_configured_run(
                factory, UUID(run_id), generation, worker_id, settings
            )
            if fence is None:
                return
            lease = AttemptLease(
                factory,
                settings,
                fence.attempt_id,
                fence.generation,
                fence.lease_token,
                worker_id,
            )
            lease.start()
            try:
                return execute_configured_controller(factory, fence, settings)
            finally:
                lease.close()

    finally:
        engine.dispose()


def require_dispatch_binding(session, run, event, settings):
    """Validate an existing durable outbox; no attempt or file access is created."""
    try:
        config = require_source_identity(run)
    except (ValueError, TypeError, AttributeError):
        raise DomainError(
            "unsupported_run_identity", "Run identity is unsupported", 409
        ) from None
    revisions = session.scalars(text("SELECT version_num FROM alembic_version")).all()
    if revisions not in (
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
            "engine_submission_not_enabled", "RD queue schema unavailable", 409
        )
    if event.run_id != run.id or event.generation != 1 or run.generation != 1:
        raise DomainError(
            "rd_dispatch_binding", "Exact fresh generation outbox required", 409
        )
    row = session.scalar(
        select(FactorResearchSubmission).filter_by(owner_id=run.owner_id, run_id=run.id)
    )
    from workers.research.rdagent_seed_queue import (
        require_seeded_queue_binding,
        seeded_row,
    )

    seeded = seeded_row(session, run, revisions)
    if seeded is not None:
        if row is not None:
            raise DomainError(
                "rd_dispatch_binding", "Exactly one submission family required", 409
            )
        if settings is None:
            from backend.app.core.config import get_settings

            settings = get_settings()
        require_seeded_queue_binding(
            session, run, seeded, config, settings, read_inputs=False
        )
        return
    if row is None:
        raise DomainError("rd_dispatch_binding", "RD submission receipt required", 409)
    if config.schema_version == "mineagent.factor-research.v2":
        from workers.research.rdagent_provider_queue import (
            require_provider_queue_binding,
        )

        if settings is None:
            from backend.app.core.config import get_settings

            settings = get_settings()
        require_provider_queue_binding(
            session, run, row, config, settings, read_inputs=False
        )
        return
    request = read_request(row)
    existing_receipt(
        session,
        run.owner_id,
        run.idempotency_key,
        RDSubmit(**request.model_dump(), preflight_sha256=row.preflight_sha256),
    )
    if settings is None:
        from backend.app.core.config import get_settings

        settings = get_settings()
    runtime, parent = resolve_submission_runtime(settings)
    plan = session.get(FactorAnalysisPlan, row.plan_id)
    if runtime != config.runtime or parent != plan.qlib_installed_parent_image_id:
        raise DomainError(
            "runtime_profile_changed",
            "Queued immutable runtime differs from server profile",
            409,
        )
