"""Transactional handoff: outbox and Procrastinate share a verified psycopg transaction.

Duplicate queue deliveries remain safe because claim_attempt locks the business
Run and checks its generation. We do not claim end-to-end exactly-once execution.
"""

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.app.core import maintenance  # noqa: F401 -- before queue/row locks
from backend.app.models import OutboxEvent, ResearchRun, utcnow
from backend.app.research_lifecycle import require_legacy_run
from backend.app.services import DomainError
from workers.research.queue import TASK_NAME, create_queue_app


def dispatch_outbox(session: Session, limit: int = 100, *, settings=None) -> int:
    if session.bind.dialect.name != "postgresql":
        raise ValueError("Outbox dispatcher requires PostgreSQL")
    events = session.scalars(
        select(OutboxEvent)
        .where(OutboxEvent.dispatched_at.is_(None))
        .order_by(OutboxEvent.created_at, OutboxEvent.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    if not events:
        return 0
    # Validate every identity before creating any queue job. Missing rows fail closed.
    tasks = {}
    selected = []
    for event in events:
        run = session.get(ResearchRun, event.run_id)
        if run is not None and (run.purpose, run.engine) == (
            "factor_research",
            "rdagent",
        ):
            from workers.research.rdagent_queue import (
                TASK_NAME as RD_TASK_NAME,
            )
            from workers.research.rdagent_queue import (
                require_dispatch_binding,
            )

            try:
                require_dispatch_binding(session, run, event, settings)
            except DomainError as exc:
                if exc.code in {
                    "engine_submission_not_enabled",
                    "engine_runtime_unconfigured",
                    "engine_runtime_unsupported",
                    "runtime_profile_changed",
                }:
                    # Leave the durable event pending, without blocking unrelated
                    # legacy work or manufacturing a delivered/accepted receipt.
                    continue
                raise
            tasks[event.id] = RD_TASK_NAME
        else:
            require_legacy_run(run)
            tasks[event.id] = TASK_NAME
        selected.append(event)
    events = selected
    if not events:
        return 0
    connection = session.connection().connection.driver_connection
    url = session.bind.url.set(drivername="postgresql").render_as_string(
        hide_password=False
    )
    queue = create_queue_app(database_url=url)
    for event in events:
        task = queue.tasks[tasks[event.id]]
        event.job_id = task.configure(connection=connection).defer(
            run_id=str(event.run_id), generation=event.generation
        )
        event.dispatched_at = utcnow()
    session.flush()
    return len(events)


def recover_stalled_jobs(
    session: Session, stale_seconds: int = 120, *, rd=False
) -> int:
    """Requeue only our jobs with missing/stale queue-worker heartbeats.

    Uses the pinned Procrastinate 3.10 heartbeat predicate and public retry SQL
    function under the caller's transaction. A redelivery still must claim the
    business Run: it cannot retry a live/stale attempt or skip confirmed stop.
    This also closes the crash window between queue delivery and business claim.
    """
    if type(rd) is not bool:
        raise ValueError("Explicit queue family required")
    selected_task = TASK_NAME
    if rd:
        from workers.research.rdagent_queue import TASK_NAME as RD_TASK_NAME

        selected_task = RD_TASK_NAME
    if stale_seconds < 30:
        raise ValueError("Queue stall threshold must be at least 30 seconds")
    jobs = (
        session.execute(
            text("""
        SELECT j.id, j.args FROM procrastinate_jobs j
        LEFT JOIN procrastinate_workers w ON w.id = j.worker_id
        WHERE j.task_name = :task AND j.queue_name = 'research'
          AND j.status = 'doing'
          AND (j.worker_id IS NULL OR w.last_heartbeat < now() - make_interval(secs => :seconds))
        ORDER BY j.id LIMIT 100 FOR UPDATE OF j SKIP LOCKED
    """),
            {"task": selected_task, "seconds": stale_seconds},
        )
        .mappings()
        .all()
    )
    # Queue receipts are transport state, not authority to select an engine.
    # Resolve each exact committed outbox binding before retrying any job.
    for job in jobs:
        args = job["args"]
        try:
            if (
                type(args) is not dict
                or set(args) != {"run_id", "generation"}
                or type(args["run_id"]) is not str
                or type(args["generation"]) is not int
                or args["generation"] < 1
            ):
                raise ValueError
            run_id = UUID(args["run_id"])
            if str(run_id) != args["run_id"]:
                raise ValueError
        except (ValueError, TypeError, KeyError, AttributeError):
            raise DomainError(
                "unsupported_queue_identity", "Queue job has no exact run binding"
            ) from None
        run = session.scalars(
            select(ResearchRun)
            .join(OutboxEvent, OutboxEvent.run_id == ResearchRun.id)
            .where(
                OutboxEvent.job_id == job["id"],
                OutboxEvent.run_id == run_id,
                OutboxEvent.generation == args["generation"],
            )
        ).one_or_none()
        if rd:
            from backend.app.engine_run_submission_schemas import RDSubmit
            from backend.app.rdagent_submission_models import FactorResearchSubmission
            from backend.app.rdagent_submission_receipts import (
                existing_receipt,
                read_request,
            )
            from packages.rdagent_adapter.persisted_identity import (
                require_source_identity,
            )

            try:
                require_source_identity(run)
            except (ValueError, TypeError, AttributeError):
                raise DomainError(
                    "unsupported_queue_identity", "RD queue binding is invalid"
                ) from None
            row = session.scalar(
                select(FactorResearchSubmission).filter_by(
                    owner_id=run.owner_id, run_id=run.id
                )
            )
            if row is None or args["generation"] != 1:
                raise DomainError(
                    "unsupported_queue_identity", "RD queue receipt is missing"
                )
            request = read_request(row)
            existing_receipt(
                session,
                run.owner_id,
                row.idempotency_key,
                RDSubmit(**request.model_dump(), preflight_sha256=row.preflight_sha256),
            )
        else:
            require_legacy_run(run)
    for job in jobs:
        job_id = job["id"]
        session.execute(
            text("SELECT procrastinate_retry_job_v2(:job_id, now(), NULL, NULL, NULL)"),
            {"job_id": job_id},
        )
    return len(jobs)
