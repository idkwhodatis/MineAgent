"""Run one role per process: worker, dispatcher or reconciler."""

import argparse
import logging
import os
import socket
import threading
import time
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from backend.app.core.config import get_settings
from backend.app.core.db import create_db_engine, session_factory
from backend.app.models import ExecutionAttempt, ResearchRun, WorkerHeartbeat, utcnow
from backend.app.services import reconcile_attempt
from workers.research.dispatcher import dispatch_outbox, recover_stalled_jobs
from workers.research.queue import create_queue_app

logger = logging.getLogger(__name__)


def dispatch_workflow_tick(factory, settings):
    """Bounded successor tick after the outbox transaction, never an engine worker."""
    from sqlalchemy import text

    with factory() as session:
        revisions = session.scalars(
            text("SELECT version_num FROM alembic_version")
        ).all()
    if revisions not in (
        ["029_research_workflow"],
        ["030_declared_upload_basket"],
        ["031_workflow_structured_stage"],
        ["032_normal_oe_note_pins"],
        ["032a_rd_terminal_results"],
    ):
        return ()
    from workers.research.workflow_tick import tick_workflows

    # The tick independently checks the exact installed capability, shared
    # lifecycle, source bytes and root CAS. No caller status grants a claim.
    return tick_workflows(factory, settings, limit=4)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=["worker", "dispatcher", "reconciler"])
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    factory = session_factory(create_db_engine(settings))
    worker_id = f"{args.role}:{socket.gethostname()}:{uuid4()}"
    if args.role == "worker":
        done = threading.Event()

        def keep_alive():
            while not done.is_set():
                try:
                    with factory.begin() as session:
                        session.execute(
                            insert(WorkerHeartbeat)
                            .values(
                                worker_id=worker_id,
                                role="research",
                                heartbeat_at=utcnow(),
                            )
                            .on_conflict_do_update(
                                index_elements=[WorkerHeartbeat.worker_id],
                                set_={"heartbeat_at": utcnow()},
                            )
                        )
                except Exception:  # noqa: BLE001 - heartbeat reports stale rather than crashing worker
                    logger.error("Research worker heartbeat unavailable")
                done.wait(5)

        thread = threading.Thread(target=keep_alive, daemon=True)
        thread.start()
        import_thread = None
        if settings.factor_library_enabled and settings.factor_import_enabled:

            def process_factor_imports():
                from workers.research.factor_import import process_one

                while not done.is_set():
                    try:
                        process_one(settings)
                    except Exception:  # noqa: BLE001 - durable admission/journal retains uncertain state
                        logger.error(
                            "Factor import cycle failed; inspect durable admission state"
                        )
                    done.wait(2)

            import_thread = threading.Thread(target=process_factor_imports, daemon=True)
            import_thread.start()
        app = create_queue_app(settings)
        try:
            with app.open():
                app.run_worker(
                    queues=["research"],
                    concurrency=int(os.getenv("WORKER_CONCURRENCY", "1")),
                )
        finally:
            done.set()
            thread.join(timeout=20)
            if import_thread is not None:
                import_thread.join(timeout=25)
                if import_thread.is_alive():
                    logger.error(
                        "Factor import shutdown remains uncertain; journal recovery is required"
                    )
        return
    runner = portable = None

    def stop_attempt(attempt_id, sandbox_id):
        nonlocal runner, portable
        # Lazy: comparison recovery is independent of Docker availability.
        from runtimes.evaluator.portable_runner import PortableDockerEvaluator
        from runtimes.evaluator.runner import DockerEvaluator
        from workers.research.containment import AttemptSandboxStopper

        if runner is None:
            runner = DockerEvaluator()
        if portable is None:
            portable = PortableDockerEvaluator()
        return AttemptSandboxStopper(attempt_id, runner, portable).stop(sandbox_id)

    while True:
        try:
            rd_ids = []
            structured_ids = []
            structured_family = (
                (ResearchRun.purpose == "strategy_search")
                & (ResearchRun.engine == "openevolve")
                & (
                    ResearchRun.config["schema_version"].astext.in_(
                        (
                            "mineagent.oe-structured-provider-run.v1",
                            "mineagent.oe-structured-provider-run.v2",
                        )
                    )
                )
            ).is_(True)
            with factory.begin() as session:
                session.execute(
                    insert(WorkerHeartbeat)
                    .values(worker_id=worker_id, role=args.role, heartbeat_at=utcnow())
                    .on_conflict_do_update(
                        index_elements=[WorkerHeartbeat.worker_id],
                        set_={"heartbeat_at": utcnow()},
                    )
                )
                if args.role == "dispatcher":
                    dispatch_outbox(session, settings=settings)
                else:
                    ids = session.scalars(
                        select(ExecutionAttempt.id)
                        .join(ResearchRun)
                        .where(
                            ~(
                                (ResearchRun.purpose == "factor_research")
                                & (ResearchRun.engine == "rdagent")
                            ),
                            ~structured_family,
                            ExecutionAttempt.finished_at.is_(None),
                            ExecutionAttempt.lease_expires_at <= utcnow(),
                        )
                        .limit(100)
                    ).all()
                    # RD has a separate typed multi-unit terminal barrier.
                    # Never feed its child inventory into the legacy stopper.
                    rd_ids = session.scalars(
                        select(ExecutionAttempt.id)
                        .join(ResearchRun)
                        .where(
                            ExecutionAttempt.finished_at.is_(None),
                            ResearchRun.purpose == "factor_research",
                            ResearchRun.engine == "rdagent",
                            (ExecutionAttempt.lease_expires_at <= utcnow())
                            | (ResearchRun.status == "cancel_requested"),
                        )
                        .limit(100)
                    ).all()
                    structured_ids = session.scalars(
                        select(ExecutionAttempt.id)
                        .join(ResearchRun)
                        .where(
                            structured_family,
                            ExecutionAttempt.finished_at.is_(None),
                            (ExecutionAttempt.lease_expires_at <= utcnow())
                            | (ResearchRun.status == "cancel_requested"),
                        )
                        .limit(100)
                    ).all()
                    for attempt_id in ids:
                        if attempt_id in rd_ids:
                            continue
                        reconcile_attempt(
                            session,
                            attempt_id,
                            lambda sandbox_id, owned_id=attempt_id: stop_attempt(
                                owned_id, sandbox_id
                            ),
                            settings=settings,
                        )
                    recover_stalled_jobs(session)
                    from sqlalchemy import text

                    from backend.app.rdagent_submission_services import REVISION

                    if session.scalars(
                        text("SELECT version_num FROM alembic_version")
                    ).all() in (
                        [REVISION],
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
                    ):
                        recover_stalled_jobs(session, rd=True)
            if args.role == "dispatcher":
                dispatch_workflow_tick(factory, settings)
            for attempt_id in rd_ids:
                from workers.research.rdagent_recovery import reconcile_rd_attempt

                reconcile_rd_attempt(factory, attempt_id, settings)
            for attempt_id in structured_ids:
                from workers.research.oe_structured_recovery import (
                    reconcile_structured_attempt,
                )

                reconcile_structured_attempt(factory, attempt_id, settings)
            if (
                args.role == "reconciler"
                and settings.factor_library_enabled
                and settings.factor_import_enabled
            ):
                from workers.research.factor_import import reconcile_imports

                reconcile_imports(settings)
        except Exception:  # noqa: BLE001 - service loop must remain available after transient outages
            # Do not serialize SQLAlchemy exceptions/connection strings into logs.
            logger.error(
                "%s cycle failed; database or sandbox may be unavailable", args.role
            )
            if args.once:
                raise SystemExit(1) from None
        if args.once:
            return
        time.sleep(2)


if __name__ == "__main__":
    main()
