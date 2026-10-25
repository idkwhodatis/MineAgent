"""Containment-only neutral recovery. Never launch, retry, settle or publish."""

from sqlalchemy import select

from backend.app.core.maintenance import shared_lifecycle
from backend.app.research_process_models import ResearchProcessAdmission
from backend.app.research_process_services import as_admission, record_stopped
from runtimes.research_supervisor.supervisor import DockerSupervisor


def recover_unit(factory, storage_root, admission_id):
    with shared_lifecycle(factory.kw["bind"], storage_root):
        with factory() as session:
            row = session.scalar(
                select(ResearchProcessAdmission).where(
                    ResearchProcessAdmission.id == admission_id
                )
            )
            if row is None:
                return False
            admission = as_admission(row)
        # No database transaction remains open across Docker stop/inspect.
        supervisor = DockerSupervisor(admission.image_id)
        supervisor.stop_and_verify(storage_root, admission)
        with factory() as session:
            record_stopped(session, admission, storage_root=storage_root)
            session.commit()
        return True


from contextlib import contextmanager
from dataclasses import dataclass

from backend.app.research_lifecycle import neutral_transaction
from backend.app.research_process_services import (
    check_launch_intent,
    commit_launch_intent,
    record_identity,
)
from runtimes.research_supervisor.supervisor import UnitWatchdog, _mint_launch_authority


@dataclass(frozen=True)
class RunningUnit:
    admission: object
    identity: dict
    supervisor: DockerSupervisor
    watchdog: UnitWatchdog


@contextmanager
def run_unit(factory, fence, storage_root, admission_id, ticket):
    """Sole production launch boundary: committed one-use intent, then external I/O."""
    with shared_lifecycle(factory.kw["bind"], storage_root):
        with neutral_transaction(factory, fence, resource_scope="processes") as session:
            admission, intent = commit_launch_intent(session, fence, admission_id)
        # Mint only after the transaction committed. Stale/rolled-back and freely
        # constructed admissions cannot pass this authoritative lookup.
        authority = _mint_launch_authority(admission, intent)
        with neutral_transaction(factory, fence, resource_scope="processes") as session:
            check_launch_intent(session, fence, admission, intent)
        supervisor = DockerSupervisor(admission.image_id)
        watchdog = UnitWatchdog(supervisor, storage_root, authority)
        watchdog.start()
        try:
            identity = supervisor.launch(storage_root, authority, ticket)
            with neutral_transaction(
                factory, fence, resource_scope="processes"
            ) as session:
                record_identity(
                    session, fence, admission, identity, storage_root=storage_root
                )
            yield RunningUnit(admission, identity, supervisor, watchdog)
        finally:
            # Caller errors/fence loss never cancel the wall watchdog. Only a
            # positively stopped unit permits its trusted watchdog to retire.
            supervisor.stop_and_verify(storage_root, admission)
            watchdog.stopped()
            with factory() as session:
                record_stopped(session, admission, storage_root=storage_root)
                session.commit()
