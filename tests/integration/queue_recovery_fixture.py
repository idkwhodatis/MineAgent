"""Real Procrastinate crash-before-claim probe on an actually submitted run.

Called by the full provider flows after normal transactional outbox dispatch.
No receipt, run, attempt, or authority graph is fabricated by this probe.
"""

from uuid import UUID

from sqlalchemy import select, text

from backend.app.models import ExecutionAttempt, OutboxEvent, ResearchRun
from workers.research.queue_recovery import recover_stalled_research_jobs


def assert_stalled_delivery_recovers(factory, run_id):
    run_id = UUID(str(run_id))
    with factory.begin() as session:
        run = session.get(ResearchRun, run_id)
        assert run.status == "queued" and run.active_attempt_id is None
        assert (
            session.scalar(select(ExecutionAttempt.id).filter_by(run_id=run_id)) is None
        )
        event = session.scalars(select(OutboxEvent).filter_by(run_id=run_id)).one()
        assert event.dispatched_at is not None and event.job_id is not None
        job_id = event.job_id
        worker_id = session.scalar(
            text("SELECT worker_id FROM procrastinate_register_worker_v1()")
        )
        fetched = session.scalar(
            text(
                "SELECT (procrastinate_fetch_job_v2(ARRAY['research']::varchar[], :worker)).id"
            ),
            {"worker": worker_id},
        )
        assert fetched == job_id
        session.execute(
            text(
                "UPDATE procrastinate_workers SET last_heartbeat = now() - interval '5 minutes' WHERE id = :worker"
            ),
            {"worker": worker_id},
        )
    with factory() as session:
        assert recover_stalled_research_jobs(session) == 1
        session.rollback()
    with factory.begin() as session:
        assert (
            session.scalar(
                text("SELECT status FROM procrastinate_jobs WHERE id = :job"),
                {"job": job_id},
            )
            == "doing"
        )
        assert recover_stalled_research_jobs(session) == 1
    with factory.begin() as session:
        assert recover_stalled_research_jobs(session) == 0
        assert session.execute(
            text("SELECT status, attempts FROM procrastinate_jobs WHERE id = :job"),
            {"job": job_id},
        ).one() == ("todo", 1)
        run = session.get(ResearchRun, run_id)
        assert run.status == "queued" and run.active_attempt_id is None
        assert (
            session.scalar(select(ExecutionAttempt.id).filter_by(run_id=run_id)) is None
        )
        assert (
            session.scalars(select(OutboxEvent).filter_by(run_id=run_id)).one().job_id
            == job_id
        )
        session.execute(
            text("SELECT procrastinate_unregister_worker_v1(:worker)"),
            {"worker": worker_id},
        )
