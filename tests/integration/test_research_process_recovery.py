"""Real PostgreSQL registry fences plus real Docker containment recovery."""

import hashlib
import os
import secrets
import shutil
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.models import ExecutionAttempt, ResearchRun, utcnow
from backend.app.research_broker_models import ResearchBudgetControl
from backend.app.research_broker_services import initialize_budget, reserve_budget
from backend.app.research_lifecycle import neutral_transaction
from backend.app.research_process_models import (
    ResearchProcessAdmission,
    ResearchProcessReceipt,
    ResearchProcessState,
)
from backend.app.research_process_services import (
    admit_unit,
    assert_attempt_units_stopped,
    mark_uncertain,
    reconcile_attempt_units,
)
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.fences import AttemptFence
from packages.research_runtime.process_contracts import ProcessError
from runtimes.research_supervisor.supervisor import DockerSupervisor

pytestmark = pytest.mark.postgres
from packages.research_runtime.process_profiles import profile_for

PROCESS_AMOUNTS = profile_for("neutral_fixture.v1", "neutral_fixture").reservation


@pytest.fixture
def process_fence_fixture(migrated_engine, claimed_attempt, owner):
    # Extend only this fixture's real lease to cover container startup; no stale
    # lease exception or mocked database authority is accepted.
    with Session(migrated_engine) as session:
        attempt = session.get(ExecutionAttempt, claimed_attempt.id)
        attempt.lease_expires_at = utcnow() + timedelta(minutes=5)
        session.commit()
    fence = AttemptFence(
        owner.user.id,
        claimed_attempt.run_id,
        claimed_attempt.id,
        claimed_attempt.generation,
        claimed_attempt.lease_token,
    )
    return sessionmaker(migrated_engine, expire_on_commit=False), fence


def _admit_fixture_process(factory, fence, image_id, *, logical_id=None):
    ticket = secrets.token_hex(32)
    with neutral_transaction(factory, fence, resource_scope="processes") as session:
        if (
            session.scalar(
                select(ResearchBudgetControl.id).where(
                    ResearchBudgetControl.run_id == fence.run_id
                )
            )
            is None
        ):
            initialize_budget(
                session,
                fence,
                BudgetVector(
                    process_slots=8,
                    storage_bytes=8 * PROCESS_AMOUNTS.storage_bytes,
                    wall_milliseconds=8 * 60000,
                ),
            )
        reservation = reserve_budget(
            session,
            fence,
            purpose="process_unit",
            logical_key="process:" + uuid4().hex,
            amounts=PROCESS_AMOUNTS,
        )
        admission = admit_unit(
            session,
            fence,
            role="neutral_fixture",
            logical_operation_id=logical_id or uuid4(),
            launch_ordinal=1,
            profile="neutral_fixture.v1",
            input_sha256="a" * 64,
            reservation_id=reservation.id,
            image_id=image_id,
            ticket_sha256=hashlib.sha256(ticket.encode()).hexdigest(),
        )
    return admission, ticket


def _seed_stopped_process(factory, fence, settings, image_id):
    """Shared cold-restore fixture: actual commit→Docker→immutable stop receipts."""
    from workers.research.process_recovery import run_unit

    admission, ticket = _admit_fixture_process(factory, fence, image_id)
    with run_unit(factory, fence, settings.storage_root, admission.id, ticket):
        pass
    return admission


def _required_image():
    image = os.environ.get("MINEAGENT_RESEARCH_SUPERVISOR_IMAGE")
    if image and shutil.which("docker") and os.getuid() != 0:
        return image
    if os.environ.get("MINEAGENT_REQUIRE_NEUTRAL_ACCEPTANCE") == "1":
        pytest.fail("Real neutral process image/runtime required")
    pytest.skip("Real neutral process image/runtime not configured")


@pytest.mark.parametrize("field", ["owner_id", "run_id", "attempt_id", "generation"])
def test_process_admission_rejects_wrong_scope(process_fence_fixture, field):
    factory, fence = process_fence_fixture
    admission, _ = _admit_fixture_process(factory, fence, _required_image())
    with factory() as session:
        original = session.get(ResearchProcessAdmission, admission.id)
        assert original is not None
        with pytest.raises(DBAPIError):
            session.execute(
                text(
                    f"UPDATE research_process_admissions SET {field}=:value WHERE id=:id"
                ),
                {"value": 2 if field == "generation" else uuid4(), "id": admission.id},
            )
            session.commit()
        session.rollback()
        assert getattr(
            session.get(ResearchProcessAdmission, admission.id), field
        ) == getattr(admission, field)


def test_process_unknown_stop_blocks_terminal_and_keeps_reservation(
    process_fence_fixture,
):
    factory, fence = process_fence_fixture
    admission, _ = _admit_fixture_process(factory, fence, _required_image())
    with neutral_transaction(factory, fence, resource_scope="processes") as session:
        mark_uncertain(session, fence, admission)
        assert not reconcile_attempt_units(
            session, fence.run_id, fence.attempt_id, fence.generation
        ).confirmed
        with pytest.raises(ProcessError):
            assert_attempt_units_stopped(
                session, fence.run_id, fence.attempt_id, fence.generation
            )
    with factory() as session:
        assert session.get(ResearchProcessState, admission.id).status == "uncertain"
        assert (
            session.scalar(
                text("SELECT state FROM research_budget_reservations WHERE id=:id"),
                {"id": admission.reservation_id},
            )
            == "reserved"
        )


def test_process_cannot_reuse_launch_or_wrong_reservation(process_fence_fixture):
    factory, fence = process_fence_fixture
    admission, _ = _admit_fixture_process(factory, fence, _required_image())
    arguments = {
        k: v
        for k, v in admission.__dict__.items()
        if k not in {"id", "owner_id", "run_id", "attempt_id", "generation"}
    }
    with (
        neutral_transaction(factory, fence, resource_scope="processes") as session,
        pytest.raises(ProcessError, match="already_admitted"),
    ):
        admit_unit(session, fence, **arguments)
    from packages.research_runtime.control import BrokerAbort

    with (
        neutral_transaction(factory, fence, resource_scope="processes") as session,
        pytest.raises(BrokerAbort),
    ):
        admit_unit(session, fence, **{**arguments, "reservation_id": uuid4()})


def test_real_process_stop_after_lease_loss_is_containment_only(
    process_fence_fixture, settings
):
    factory, fence = process_fence_fixture
    image = _required_image()
    from workers.research.process_recovery import run_unit

    admission, ticket = _admit_fixture_process(factory, fence, image)
    with run_unit(factory, fence, settings.storage_root, admission.id, ticket):
        with factory() as session:
            attempt = session.get(ExecutionAttempt, fence.attempt_id)
            attempt.lease_expires_at = utcnow() - timedelta(seconds=1)
            session.commit()
        from backend.app.services import DomainError

        with (
            pytest.raises(DomainError),
            neutral_transaction(factory, fence, resource_scope="processes"),
        ):
            pytest.fail("Expired authority admitted")
    with factory() as session:
        assert_attempt_units_stopped(
            session, fence.run_id, fence.attempt_id, fence.generation
        )
        assert session.get(ResearchRun, fence.run_id).status == "preparing"
        assert (
            session.scalar(
                text("SELECT state FROM research_budget_reservations WHERE id=:id"),
                {"id": admission.reservation_id},
            )
            == "reserved"
        )
        # Positive stop proof permits terminal projection, but retained unknown
        # accounting must still prohibit a new generation.
        session.execute(
            text("UPDATE research_runs SET status='cancelled' WHERE id=:id"),
            {"id": fence.run_id},
        )
        session.execute(
            text(
                "UPDATE execution_attempts SET finished_at=now(),sandbox_stopped=true WHERE id=:id"
            ),
            {"id": fence.attempt_id},
        )
        session.commit()
        with pytest.raises(DBAPIError):
            session.execute(
                text("UPDATE research_runs SET generation=generation+1 WHERE id=:id"),
                {"id": fence.run_id},
            )
            session.commit()
        session.rollback()
        receipt = session.scalar(
            select(ResearchProcessReceipt).where(
                ResearchProcessReceipt.admission_id == admission.id,
                ResearchProcessReceipt.kind == "stop",
            )
        )
        with pytest.raises(DBAPIError):
            session.execute(
                text(
                    "UPDATE research_process_receipts SET payload='{}'::jsonb WHERE id=:id"
                ),
                {"id": receipt.id},
            )
            session.commit()


@pytest.mark.parametrize("field", ["owner_id", "run_id", "attempt_id", "generation"])
def test_process_insert_enforces_composite_scope(
    process_fence_fixture, submit_run, other, settings, field
):
    factory, fence = process_fence_fixture
    admission, _ = _admit_fixture_process(factory, fence, _required_image())
    response = submit_run()
    assert response.status_code in (201, 202)
    from backend.app.services import claim_attempt

    with factory() as session:
        second = claim_attempt(
            session,
            UUID(response.json()["id"]),
            response.json()["generation"],
            "second-process-fixture",
            settings,
        )
        assert second is not None
        second_id = second.id
        session.commit()
    with neutral_transaction(factory, fence, resource_scope="processes") as session:
        reservation = reserve_budget(
            session,
            fence,
            purpose="process_unit",
            logical_key="scope:" + uuid4().hex,
            amounts=PROCESS_AMOUNTS,
        )
        reservation_id = reservation.id
    values = {
        **admission.__dict__,
        "schema_version": "mineagent.process-admission.v1",
        "ipc_policy": "host_ticket",
        "id": uuid4(),
        "reservation_id": reservation_id,
        "logical_operation_id": uuid4(),
    }
    values[field] = {
        "owner_id": other.user.id,
        "run_id": UUID(response.json()["id"]),
        "attempt_id": second_id,
        "generation": 2,
    }[field]
    with factory() as session:
        session.add(ResearchProcessAdmission(**values))
        with pytest.raises(DBAPIError) as caught:
            session.flush()
        # Prove the composite FK, not a duplicate id/reservation or immutable UPDATE.
        assert caught.value.orig.sqlstate == "23503"
        session.rollback()


def test_process_database_terminal_and_generation_fail_closed(process_fence_fixture):
    factory, fence = process_fence_fixture
    _admit_fixture_process(factory, fence, _required_image())
    for statement in (
        "UPDATE research_runs SET status='cancelled' WHERE id=:run",
        "UPDATE research_runs SET generation=generation+1 WHERE id=:run",
        "UPDATE execution_attempts SET sandbox_stopped=true WHERE id=:attempt",
        "UPDATE execution_attempts SET finished_at=now() WHERE id=:attempt",
    ):
        with factory() as session:
            with pytest.raises(DBAPIError):
                session.execute(
                    text(statement), {"run": fence.run_id, "attempt": fence.attempt_id}
                )
                session.commit()
            session.rollback()


from contextlib import contextmanager


@contextmanager
def actual_peer(factory, fence, settings, image_id, logical_operation_id):
    """Real scoped peer for broker/restore acceptance, always stops owned unit."""
    from runtimes.research_supervisor.ipc import accept_fixture_peer
    from workers.research.process_recovery import run_unit

    admission, ticket = _admit_fixture_process(
        factory, fence, image_id, logical_id=logical_operation_id
    )
    with run_unit(factory, fence, settings.storage_root, admission.id, ticket) as unit:
        yield accept_fixture_peer(settings.storage_root, admission, unit.identity)


@pytest.mark.parametrize(
    "dimension", ["process_slots", "wall_milliseconds", "storage_bytes"]
)
def test_process_profile_requires_every_reserved_peak(process_fence_fixture, dimension):
    from packages.research_runtime.control import BrokerAbort

    factory, fence = process_fence_fixture
    amounts = PROCESS_AMOUNTS.model_copy(update={dimension: 0})
    with neutral_transaction(factory, fence, resource_scope="processes") as session:
        initialize_budget(session, fence, PROCESS_AMOUNTS)
        reservation = reserve_budget(
            session,
            fence,
            purpose="process_unit",
            logical_key="insufficient",
            amounts=amounts,
        )
        with pytest.raises(BrokerAbort):
            admit_unit(
                session,
                fence,
                role="neutral_fixture",
                logical_operation_id=uuid4(),
                launch_ordinal=1,
                profile="neutral_fixture.v1",
                input_sha256="a" * 64,
                reservation_id=reservation.id,
                image_id=_required_image(),
                ticket_sha256="c" * 64,
            )
        assert session.scalar(select(ResearchProcessAdmission.id)) is None


@pytest.mark.parametrize("invalid", ["absent", "rolled_back", "stale", "consumed"])
def test_process_launch_requires_committed_current_one_use_authority(
    process_fence_fixture, settings, monkeypatch, invalid
):
    from backend.app.research_process_services import commit_launch_intent
    from backend.app.services import DomainError
    from workers.research.process_recovery import run_unit

    factory, fence = process_fence_fixture
    admission, ticket = _admit_fixture_process(factory, fence, _required_image())
    target = admission.id
    if invalid == "absent":
        target = uuid4()
    elif invalid == "rolled_back":
        # Transaction rollback removes both the reservation and its process row.
        class Rollback(Exception):
            pass

        try:
            with neutral_transaction(
                factory, fence, resource_scope="processes"
            ) as session:
                reservation = reserve_budget(
                    session,
                    fence,
                    purpose="process_unit",
                    logical_key="rollback",
                    amounts=PROCESS_AMOUNTS,
                )
                pending = admit_unit(
                    session,
                    fence,
                    role="neutral_fixture",
                    logical_operation_id=uuid4(),
                    launch_ordinal=1,
                    profile="neutral_fixture.v1",
                    input_sha256="a" * 64,
                    reservation_id=reservation.id,
                    image_id=_required_image(),
                    ticket_sha256="c" * 64,
                )
                target = pending.id
                raise Rollback()
        except Rollback:
            pass
    elif invalid == "stale":
        with factory() as session:
            session.get(ExecutionAttempt, fence.attempt_id).lease_expires_at = (
                utcnow() - timedelta(seconds=1)
            )
            session.commit()
    else:
        with neutral_transaction(factory, fence, resource_scope="processes") as session:
            commit_launch_intent(session, fence, admission.id)

    def no_create(*_args, **_kwargs):
        pytest.fail("Rejected authority reached physical creation")

    monkeypatch.setattr(DockerSupervisor, "launch", no_create)
    with (
        pytest.raises((ProcessError, DomainError)),
        run_unit(factory, fence, settings.storage_root, target, ticket),
    ):
        pytest.fail("Invalid authority acknowledged")
