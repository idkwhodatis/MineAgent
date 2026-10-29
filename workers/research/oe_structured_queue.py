"""Exact normal027 outbox claim. No legacy/024 fallback or automatic rerun.

The initial lease is a declared bounded 720 seconds for the 600-second controller
plus stop/publication margin. No worker heartbeat silently replaces this profile
with a shorter legacy lease. Shared migration/capability registration is required.
"""

from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, text

from backend.app.core.maintenance import shared_lifecycle
from backend.app.models import ExecutionAttempt, OutboxEvent, ResearchRun, utcnow
from backend.app.oe_structured_identity import decode_spec
from backend.app.oe_structured_models import OEStructuredSubmission
from backend.app.oe_structured_preflight import REVISION, evaluate
from backend.app.oe_structured_provider_input import retained_submission_data
from backend.app.oe_structured_provider_submission import (
    ProviderStructuredSearchRequest,
    ProviderStructuredSearchSubmit,
)
from backend.app.oe_structured_server_profile import (
    resolve_operator_profile,
    resolve_runtime,
)
from backend.app.oe_structured_submission_receipts import existing_receipt
from backend.app.research_broker_services import initialize_budget
from backend.app.research_lifecycle import _try_neutral_lock, neutral_transaction
from backend.app.services import DomainError
from packages.research_runtime.fences import AttemptFence

TASK_NAME = "mineagent.execute_oe_structured_run"
LEASE_SECONDS = 720


def _fail(code="oe_structured_queue_binding"):
    raise DomainError(code, "Exact normal structured submission/outbox required", 409)


def require_dispatch_binding(session, run, event, settings):
    if session.scalars(text("SELECT version_num FROM alembic_version")).all() not in (
        [REVISION],
        ["029_research_workflow"],
        ["030_declared_upload_basket"],
        ["031_workflow_structured_stage"],
        ["032_normal_oe_note_pins"],
    ):
        _fail("runtime_schema_unavailable")
    if (
        run is None
        or event is None
        or event.run_id != run.id
        or event.generation != 1
        or run.generation != 1
    ):
        _fail()
    spec, config = decode_spec(session, run)
    row = session.get(OEStructuredSubmission, spec.submission_id)
    if row is None or (row.owner_id, row.run_id, row.idempotency_key) != (
        run.owner_id,
        run.id,
        run.idempotency_key,
    ):
        _fail()
    from packages.openevolve_structured.workflow_run import WorkflowStructuredRunConfig

    if type(config) is WorkflowStructuredRunConfig:
        from backend.app.oe_workflow_queue import dispatch_binding

        return dispatch_binding(session, run, row, config, settings)
    request = ProviderStructuredSearchRequest.model_validate_json(row.request_canonical)
    receipt = existing_receipt(
        session,
        run.owner_id,
        run.idempotency_key,
        ProviderStructuredSearchSubmit(
            **request.model_dump(), preflight_sha256=row.preflight_sha256
        ),
    )
    if receipt is None or receipt.id != row.id or receipt.run_id != run.id:
        _fail()
    runtime = resolve_runtime(settings)
    _, binding = resolve_operator_profile(
        settings, run.owner_id, request.model_profile_id
    )
    if runtime.runtime != config.runtime or binding != config.model_binding:
        _fail("oe_structured_queued_profile_changed")
    return request, config, row.preflight_sha256


def claim_structured_run(factory, run_id, generation, worker_id, settings):
    if (
        type(run_id) is not UUID
        or type(generation) is not int
        or generation != 1
        or type(worker_id) is not str
        or not 1 <= len(worker_id) <= 255
    ):
        _fail("oe_structured_claim_invalid")
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        factory() as session,
    ):
        owner = session.scalar(
            select(ResearchRun.owner_id).where(ResearchRun.id == run_id)
        )
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
        if (
            run.active_attempt_id is not None
            or session.scalar(
                select(ExecutionAttempt.id).filter_by(run_id=run_id).limit(1)
            )
            is not None
        ):
            return None
        events = session.scalars(
            select(OutboxEvent).filter_by(run_id=run_id, generation=generation)
        ).all()
        if len(events) != 1:
            _fail()
        request, config, preflight_sha = require_dispatch_binding(
            session, run, events[0], settings
        )
        from packages.openevolve_structured.workflow_run import (
            WorkflowStructuredRunConfig,
        )

        if type(config) is WorkflowStructuredRunConfig:
            from backend.app.oe_structured_models import OEStructuredSpec
            from backend.app.oe_workflow_queue import claim_data, evaluate_claim

            spec = session.get(OEStructuredSpec, run.id)
            data_context = claim_data(
                session, owner, request, settings, config, spec.submission_id
            )
            evaluator = evaluate_claim
        else:
            data_context = retained_submission_data(session, owner, request, settings)
            evaluator = evaluate
        with data_context as data:
            checked, fixed, runtime, binding = evaluator(
                session, owner, request, settings, data
            )
            if (
                not checked.can_submit
                or checked.preflight_sha256 != preflight_sha
                or fixed != config.fixed_inputs
                or runtime.runtime != config.runtime
                or binding != config.model_binding
            ):
                _fail("oe_structured_queued_preflight_changed")
            now = utcnow()
            attempt = ExecutionAttempt(
                id=uuid4(),
                run_id=run.id,
                generation=1,
                worker_id=worker_id,
                lease_token=uuid4(),
                lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
                heartbeat_at=now,
                status="running",
                sandbox_id="not-launched:oe-structured",
                sandbox_stopped=False,
            )
            session.add(attempt)
            session.flush()
            run.active_attempt_id, run.status = attempt.id, "running"
            session.flush()
            fence = AttemptFence(owner, run.id, attempt.id, 1, attempt.lease_token)
            # Retained file guard and owner lock survive through the actual claim commit.
            session.commit()
        with neutral_transaction(
            factory, fence, resource_scope="factor_numeric"
        ) as current:
            _, actual = decode_spec(current, current.get(ResearchRun, run_id))
            if actual != config:
                _fail()
            initialize_budget(current, fence, config.budget)
        return fence


def execute(run_id, generation, *, settings):
    """Normal task consumer; failed/uncertain attempt is never silently reissued."""
    import socket

    from backend.app.core.atomic_storage import provision_directory
    from backend.app.core.db import create_db_engine, session_factory
    from workers.research.oe_structured_controller import execute_controller_epoch
    from workers.research.oe_structured_finish import finish_structured_run

    if type(run_id) is not str or str(UUID(run_id)) != run_id:
        _fail("oe_structured_claim_invalid")
    engine = create_db_engine(settings)
    factory = session_factory(engine)
    try:
        with shared_lifecycle(engine, settings.storage_root):
            for directory in ("factors", "research-objects", "research-processes"):
                provision_directory(
                    settings.storage_root / directory,
                    durable_parent=settings.storage_root,
                )
            fence = claim_structured_run(
                factory,
                UUID(run_id),
                generation,
                "oe-structured:" + socket.gethostname() + ":" + str(uuid4()),
                settings,
            )
            if fence is None:
                return None
            epoch_id = execute_controller_epoch(factory, fence, settings)
            return finish_structured_run(factory, fence, epoch_id, settings)
    finally:
        engine.dispose()
