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


def dispatch_outbox(session: Session, limit: int = 100) -> int:
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
    for event in events:
        require_legacy_run(session.get(ResearchRun, event.run_id))
    connection = session.connection().connection.driver_connection
    url = session.bind.url.set(drivername="postgresql").render_as_string(
        hide_password=False
    )
    queue = create_queue_app(database_url=url)
    task = queue.tasks[TASK_NAME]
    for event in events:
        event.job_id = task.configure(connection=connection).defer(
            run_id=str(event.run_id), generation=event.generation
        )
        event.dispatched_at = utcnow()
    session.flush()
    return len(events)


def recover_stalled_jobs(session: Session, stale_seconds: int = 120) -> int:
    """Requeue only our jobs with missing/stale queue-worker heartbeats.

    Uses the pinned Procrastinate 3.10 heartbeat predicate and public retry SQL
    function under the caller's transaction. A redelivery still must claim the
    business Run: it cannot retry a live/stale attempt or skip confirmed stop.
    This also closes the crash window between queue delivery and business claim.
    """
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
            {"task": TASK_NAME, "seconds": stale_seconds},
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
        require_legacy_run(run)
    for job in jobs:
        job_id = job["id"]
        session.execute(
            text("SELECT procrastinate_retry_job_v2(:job_id, now(), NULL, NULL, NULL)"),
            {"job_id": job_id},
        )
    return len(jobs)
