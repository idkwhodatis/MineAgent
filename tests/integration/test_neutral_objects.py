"""Actual PG identity, admission and filesystem failure boundaries for N1 objects."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from backend.app.research_broker_models import ResearchBudgetReservation
from backend.app.research_broker_services import initialize_budget, reserve_budget
from backend.app.research_lifecycle import neutral_transaction
from backend.app.research_object_models import (
    ResearchObject,
    ResearchObjectOutcome,
    ResearchObjectPublication,
)
from backend.app.research_object_services import (
    physical_inventory,
    publish_reserved_object,
    reserve_object,
)
from backend.app.services import DomainError
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.fences import AttemptFence

pytestmark = pytest.mark.postgres


@pytest.fixture
def object_setup(migrated_engine, owner, queued_run, claimed_attempt, settings):
    factory = sessionmaker(migrated_engine, expire_on_commit=False)
    fence = AttemptFence(
        owner.user.id,
        UUID(queued_run["id"]),
        claimed_attempt.id,
        claimed_attempt.generation,
        claimed_attempt.lease_token,
    )
    (settings.storage_root / "research-objects").mkdir(exist_ok=True)
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        initialize_budget(session, fence, BudgetVector(storage_bytes=1024 * 1024))
    return factory, fence, settings


def prepare(setup, *, kind="model_request", content=b"{}", key=None):
    factory, fence, settings = setup
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        budget = reserve_budget(
            session,
            fence,
            purpose="object_storage",
            logical_key=key or uuid4().hex,
            amounts=BudgetVector(storage_bytes=2 * len(content) + 8192),
        )
        publication = reserve_object(session, fence, kind, content, budget.id, settings)
        publication_id, budget_id = publication.id, budget.id
    return publication_id, budget_id


def test_neutral_real_equal_bytes_different_kinds_are_separate_and_charged(
    object_setup,
):
    factory, fence, settings = object_setup
    ids = []
    for kind in ("model_request", "model_response"):
        publication, _ = prepare(object_setup, kind=kind)
        ids.append(
            publish_reserved_object(factory, fence, publication, b"{}", settings)
        )
    assert ids[0] != ids[1]
    with factory() as session:
        rows = session.scalars(select(ResearchObject)).all()
        assert len(rows) == 2 and rows[0].sha256 == rows[1].sha256
        assert len({row.storage_key for row in rows}) == 2
        reservations = session.scalars(select(ResearchBudgetReservation)).all()
        assert sum(row.amounts["storage_bytes"] for row in reservations) == 16392
    assert len(physical_inventory(settings.storage_root)) == 4


def test_neutral_real_exact_republication_reuses_same_kind_object(object_setup):
    factory, fence, settings = object_setup
    first, _ = prepare(object_setup)
    a = publish_reserved_object(factory, fence, first, b"{}", settings)
    assert publish_reserved_object(factory, fence, first, b"{}", settings) == a
    second, _ = prepare(object_setup)
    assert publish_reserved_object(factory, fence, second, b"{}", settings) == a
    with factory() as session:
        assert len(session.scalars(select(ResearchObject)).all()) == 1
        assert len(session.scalars(select(ResearchObjectOutcome)).all()) == 2
    assert len(physical_inventory(settings.storage_root)) == 3


@pytest.mark.parametrize("field", ["owner_id", "object_kind", "sha256", "storage_key"])
def test_neutral_real_object_identity_is_immutable(object_setup, field):
    factory, fence, settings = object_setup
    publication, _ = prepare(object_setup)
    object_id = publish_reserved_object(factory, fence, publication, b"{}", settings)
    replacements = {
        "owner_id": str(uuid4()),
        "object_kind": "model_response",
        "sha256": "a" * 64,
        "storage_key": "research-objects/invalid",
    }
    with pytest.raises(DBAPIError), factory.begin() as session:
        session.execute(
            text(f"UPDATE research_objects SET {field}=:value WHERE id=:id"),
            {"value": replacements[field], "id": object_id},
        )


def test_neutral_real_publication_budget_cannot_substitute_scope(
    object_setup, submit_run
):
    factory, fence, _settings = object_setup
    publication, _ = prepare(object_setup)
    other_run = submit_run()
    assert other_run.status_code in (201, 202)
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        other_budget = reserve_budget(
            session,
            fence,
            purpose="object_storage",
            logical_key=uuid4().hex,
            amounts=BudgetVector(storage_bytes=8196),
        )
        other_budget_id = other_budget.id
    with factory() as session:
        row = session.get(ResearchObjectPublication, publication)
        values = {
            c.name: getattr(row, c.name)
            for c in ResearchObjectPublication.__table__.columns
            if c.name != "created_at"
        }
    values.update(
        id=uuid4(),
        run_id=UUID(other_run.json()["id"]),
        budget_reservation_id=other_budget_id,
    )
    with pytest.raises(DBAPIError), factory.begin() as session:
        session.add(ResearchObjectPublication(**values))
        session.flush()


def test_neutral_real_pending_peak_reservations_count_toward_physical_quota(
    object_setup,
):
    factory, fence, settings = object_setup
    first, _ = prepare(object_setup)
    # No physical file yet, but the full first reservation is already charged.
    assert physical_inventory(settings.storage_root) == ()
    bounded = settings.model_copy(update={"neutral_object_owner_max_bytes": 10000})
    with pytest.raises(DomainError, match="could not be verified"):
        prepare((factory, fence, bounded))
    with factory() as session:
        assert len(session.scalars(select(ResearchObjectPublication)).all()) == 1
        assert session.get(ResearchObjectPublication, first) is not None


def test_neutral_real_database_loss_after_fsync_preserves_charged_orphan(
    object_setup, monkeypatch
):
    from backend.app import research_object_services as services

    factory, fence, settings = object_setup
    publication, budget_id = prepare(object_setup)
    actual_finalize = services.finalize_object

    def terminate_actual_connection(session, *args):
        session.execute(text("SELECT pg_terminate_backend(pg_backend_pid())"))
        raise AssertionError("PostgreSQL connection should be terminated")

    monkeypatch.setattr(services, "finalize_object", terminate_actual_connection)
    with pytest.raises(DBAPIError):
        publish_reserved_object(factory, fence, publication, b"{}", settings)
    with factory() as session:
        assert session.get(ResearchObjectOutcome, publication) is None
        assert session.get(ResearchBudgetReservation, budget_id).state == "reserved"
    assert len(physical_inventory(settings.storage_root)) == 2
    monkeypatch.setattr(services, "finalize_object", actual_finalize)
    result = publish_reserved_object(factory, fence, publication, b"{}", settings)
    with factory() as session:
        assert session.get(ResearchObjectOutcome, publication).object_id == result
    assert len(physical_inventory(settings.storage_root)) == 2


def test_neutral_real_lease_loss_after_file_write_leaves_charged_evidence(
    object_setup, monkeypatch
):
    from backend.app import research_object_services as services

    factory, fence, settings = object_setup
    publication, budget_id = prepare(object_setup)
    actual_write = services.publish_bytes

    def lose_lease_after_real_write(target, content, **kwargs):
        actual_write(target, content, **kwargs)
        if "/by-kind/" in str(target):
            with factory.begin() as session:
                session.execute(
                    text(
                        "UPDATE execution_attempts SET lease_expires_at=now()-interval '1 second' WHERE id=:id"
                    ),
                    {"id": fence.attempt_id},
                )
            # The real writer still owns its independent filesystem fence here.
            # Losing the DB lease must not let a reconciler declare it stopped.
            for statement, identifier in (
                (
                    "UPDATE research_runs SET status='interrupted' WHERE id=:id",
                    fence.run_id,
                ),
                (
                    "UPDATE execution_attempts SET finished_at=now(),sandbox_stopped=true WHERE id=:id",
                    fence.attempt_id,
                ),
            ):
                with pytest.raises(DBAPIError), factory.begin() as session:
                    session.execute(text(statement), {"id": identifier})

    monkeypatch.setattr(services, "publish_bytes", lose_lease_after_real_write)
    with pytest.raises(DomainError):
        publish_reserved_object(factory, fence, publication, b"{}", settings)
    with factory() as session:
        assert session.get(ResearchObjectOutcome, publication) is None
        assert session.get(ResearchBudgetReservation, budget_id).state == "reserved"
    assert len(physical_inventory(settings.storage_root)) == 2


@pytest.mark.parametrize(
    "transition", ["run_terminal", "attempt_terminal", "generation"]
)
def test_neutral_real_pending_object_writer_blocks_terminal_transition(
    object_setup, transition
):
    factory, fence, _settings = object_setup
    publication, budget_id = prepare(object_setup)
    with factory.begin() as session:
        session.execute(
            text(
                "UPDATE execution_attempts SET lease_expires_at=now()-interval '1 second' WHERE id=:id"
            ),
            {"id": fence.attempt_id},
        )
    statements = {
        "run_terminal": (
            "UPDATE research_runs SET status='interrupted' WHERE id=:id",
            fence.run_id,
        ),
        "attempt_terminal": (
            "UPDATE execution_attempts SET finished_at=now(),sandbox_stopped=true WHERE id=:id",
            fence.attempt_id,
        ),
        "generation": (
            "UPDATE research_runs SET generation=generation+1 WHERE id=:id",
            fence.run_id,
        ),
    }
    sql, key = statements[transition]
    with pytest.raises(DBAPIError), factory.begin() as session:
        session.execute(text(sql), {"id": key})
    with factory() as session:
        assert session.get(ResearchObjectOutcome, publication) is None
        assert session.get(ResearchBudgetReservation, budget_id).state == "reserved"


def test_neutral_real_crash_temporary_blocks_every_retry_without_growth(object_setup):
    factory, fence, settings = object_setup
    publication, budget_id = prepare(object_setup)
    with factory() as session:
        row = session.get(ResearchObjectPublication, publication)
        target = settings.storage_root / row.storage_key
    target.parent.mkdir(parents=True, exist_ok=True)
    # A real durable temp represents a process dying before atomic rename; this
    # is not ordinary exception cleanup and must never be silently overwritten.
    leftover = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    leftover.write_bytes(b"{}")
    before = physical_inventory(settings.storage_root)
    for _ in range(3):
        with pytest.raises(DomainError):
            publish_reserved_object(factory, fence, publication, b"{}", settings)
        assert physical_inventory(settings.storage_root) == before
    with factory() as session:
        assert session.get(ResearchObjectOutcome, publication) is None
        assert session.get(ResearchBudgetReservation, budget_id).state == "reserved"


def test_neutral_real_success_retains_peak_charge_and_allows_contained_terminal(
    object_setup,
):
    from backend.app.research_broker_models import ResearchBudgetControl

    factory, fence, settings = object_setup
    publication, budget_id = prepare(object_setup)
    publish_reserved_object(factory, fence, publication, b"{}", settings)
    with factory.begin() as session:
        reservation = session.get(ResearchBudgetReservation, budget_id)
        control = session.scalar(select(ResearchBudgetControl))
        assert reservation.state == "settled"
        assert control.used["storage_bytes"] == reservation.amounts["storage_bytes"]
        assert control.reserved["storage_bytes"] == 0
        session.execute(
            text("UPDATE research_runs SET status='interrupted' WHERE id=:id"),
            {"id": fence.run_id},
        )
        session.execute(
            text(
                "UPDATE execution_attempts SET finished_at=now(),sandbox_stopped=true WHERE id=:id"
            ),
            {"id": fence.attempt_id},
        )
