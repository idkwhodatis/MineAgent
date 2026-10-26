"""Revision018 real PG/filesystem source boundary; no controller-execution claim.

Update negatives exercise append-only guards, NOT every internal deferred source
predicate branch. Complete direct-SQL inserted-invalid graph coverage remains a
separate requirement; these tests do not silently count immutability as that proof.
"""

import hashlib
import stat
from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest
from rd_source_fixture import PROVENANCE, persist_maintained_source
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from backend.app.models import DatasetVersion, ResearchRun, User
from backend.app.rdagent_source_publication import publish_reserved_source
from backend.app.rdagent_source_witness_models import FactorSourceReadWitness
from backend.app.rdagent_source_witness_services import derive_source_read_witness
from backend.app.research_broker_models import (
    ResearchBudgetControl,
    ResearchBudgetReservation,
    ResearchModelOperation,
)
from backend.app.research_lifecycle import neutral_transaction
from backend.app.research_object_models import ResearchObject
from backend.app.services import DomainError
from backend.app.storage import read_verified
from packages.research_runtime.budget_contracts import validate_ledger

pytestmark = pytest.mark.postgres
SOURCE = b"# Maintained source retention fixture; never executed.\ndef factor():\n    return 1\n"


@pytest.fixture
def source_setup(migrated_engine, settings):
    factory = sessionmaker(migrated_engine, expire_on_commit=False)
    with factory.begin() as session:
        owner = User(
            id=uuid4(),
            email=f"{uuid4().hex}@example.invalid",
            hashed_password="!no-login-maintained-fixture",
            full_name=PROVENANCE,
        )
        session.add(owner)
        session.flush()
        dataset = DatasetVersion(
            id=uuid4(),
            owner_id=owner.id,
            name=PROVENANCE,
            market="CN",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 4, 1),
            source="synthetic_fixture",
            is_synthetic=True,
            content_sha256=hashlib.sha256(PROVENANCE.encode()).hexdigest(),
            manifest={"synthetic": True, "source_persistence_only": True},
            storage_key="datasets/" + uuid4().hex,
        )
        session.add(dataset)
    return factory, owner.id, dataset, settings


def persist(setup, source=SOURCE):
    factory, owner, dataset, settings = setup
    return persist_maintained_source(
        factory, owner=owner, dataset=dataset, settings=settings, source_utf8=source
    )


def test_actual_files_fenced_publisher_and_host_parser_witness(source_setup):
    factory, _, _, settings = source_setup
    saved = persist(source_setup)
    assert saved.provenance == PROVENANCE
    assert saved.version.code.encode() == SOURCE
    assert (
        read_verified(
            settings.storage_root,
            saved.codeobject.storage_key,
            saved.codeobject.sha256,
            saved.codeobject.size_bytes,
        )
        == SOURCE
    )
    with neutral_transaction(
        factory, saved.fence, resource_scope="factor_objects"
    ) as session:
        header, parts = derive_source_read_witness(
            session, saved.fence, source_id=saved.source.id, settings=settings
        )
        assert header["source_sha256"] == hashlib.sha256(SOURCE).hexdigest()
        assert header["part_count"] == len(parts) == 1
        witness = session.scalar(
            select(FactorSourceReadWitness).where(
                FactorSourceReadWitness.source_id == saved.source.id
            )
        )
        assert witness.witness_sha256 == header["witness_sha256"]
        assert (
            session.scalar(
                text("SELECT mineagent_rd_source_violation(:id)"),
                {"id": saved.source.id},
            )
            is None
        )
    with factory() as session:
        run = session.get(ResearchRun, saved.fence.run_id)
        assert (run.purpose, run.engine, run.strategy_version_id) == (
            "factor_research",
            "rdagent",
            None,
        )
        budget = session.scalar(
            select(ResearchBudgetControl).where(
                ResearchBudgetControl.run_id == saved.fence.run_id
            )
        )
        validate_ledger(budget.limits, budget.used, budget.reserved)
        assert budget.used["calls"] == 1 and budget.used["tokens"] == 1
        assert budget.reserved["process_slots"] == 1
        assert budget.reserved["mutation_slots"] == 1
        reservations = session.scalars(
            select(ResearchBudgetReservation).where(
                ResearchBudgetReservation.run_id == saved.fence.run_id
            )
        ).all()
        assert all(
            r.state == "settled"
            for r in reservations
            if r.purpose in {"model_transport", "object_storage"}
        )
        assert run.status == "running"  # No fabricated completion/containment proof.


def test_actual_continuation_sources_are_reparsed_in_order(source_setup):
    source = b"#" + b"x" * 40000 + b"\n"
    saved = persist(source_setup, source)
    assert len(saved.member_ids) == len(saved.outcome_ids) == 2
    factory, _, _, settings = source_setup
    with neutral_transaction(
        factory, saved.fence, resource_scope="factor_objects"
    ) as session:
        header, parts = derive_source_read_witness(
            session, saved.fence, source_id=saved.source.id, settings=settings
        )
        assert header["part_count"] == 2
        assert header["source_sha256"] == hashlib.sha256(source).hexdigest()
        assert [p["ordinal"] for p in parts] == [0, 1]


def test_nonempty_invalid_python_is_retained_without_execution(source_setup):
    source = b"def intentionally_invalid(:\n"
    saved = persist(source_setup, source)
    assert saved.version.code.encode() == source
    assert saved.version.source_kind == "rdagent"


def test_exact_source_publication_replay_and_wrong_bytes(source_setup):
    saved = persist(source_setup)
    factory, _, _, settings = source_setup
    again = publish_reserved_source(
        factory,
        saved.fence,
        publication_id=saved.publication_id,
        content=SOURCE,
        settings=settings,
    )
    assert again.source_id == saved.source.id
    with pytest.raises(DomainError):
        publish_reserved_source(
            factory,
            saved.fence,
            publication_id=saved.publication_id,
            content=SOURCE + b"# drift\n",
            settings=settings,
        )


@pytest.mark.parametrize(
    "field", ["owner_id", "run_id", "attempt_id", "lease_token", "generation"]
)
def test_substituted_fence_cannot_publish_retained_source(source_setup, field):
    saved = persist(source_setup)
    factory, _, _, settings = source_setup
    bad = replace(saved.fence, **{field: 2 if field == "generation" else uuid4()})
    with pytest.raises(DomainError):
        publish_reserved_source(
            factory,
            bad,
            publication_id=saved.publication_id,
            content=SOURCE,
            settings=settings,
        )


@pytest.mark.parametrize(
    ("table", "column", "value"),
    [
        ("factor_generation_sources", "source_sha256", "0" * 64),
        ("factor_source_read_witnesses", "raw_text_sha256", "0" * 64),
        ("factor_source_read_witnesses", "selected_first_ordinal", 1),
        ("factor_generation_model_operations", "relation", "continuation"),
        ("research_model_operations", "protocol", "mineagent.neutral-model.v1"),
        ("research_budget_reservations", "logical_key", "forged-reservation"),
    ],
)
def test_direct_sql_updates_cannot_rewrite_evidence(source_setup, table, column, value):
    saved = persist(source_setup)
    factory, _, _, _ = source_setup
    # Tables/columns are fixed test literals, never user-directed SQL identifiers.
    with pytest.raises(DBAPIError), factory.begin() as session:
        session.execute(
            text(f"UPDATE {table} SET {column}=:value WHERE run_id=:run"),
            {"value": value, "run": saved.fence.run_id},
        )


def test_direct_sql_new_wrong_protocol_request_rejected(source_setup):
    saved = persist(source_setup)
    factory, _, _, _ = source_setup
    with factory() as session:
        old = session.scalar(
            select(ResearchModelOperation).where(
                ResearchModelOperation.run_id == saved.fence.run_id
            )
        )
        values = {
            c.name: getattr(old, c.name)
            for c in ResearchModelOperation.__table__.columns
        }
    values.update(
        id=uuid4(), ordinal=1, state="open", protocol="mineagent.neutral-model.v1"
    )
    with pytest.raises(DBAPIError), factory.begin() as session:
        session.add(ResearchModelOperation(**values))
        session.flush()


def test_tampered_real_response_bytes_cannot_revalidate_witness(source_setup):
    saved = persist(source_setup)
    factory, _, _, settings = source_setup
    with factory() as session:
        obj = session.get(ResearchObject, saved.response_object_ids[0])
        path = settings.storage_root / obj.storage_key
    original = path.read_bytes()
    original_mode = stat.S_IMODE(path.stat().st_mode)
    assert original_mode & 0o222 == 0
    try:
        # Simulate privileged disk corruption without changing production modes.
        path.chmod(original_mode | stat.S_IWUSR)
        path.write_bytes(b"{}")
        with (
            pytest.raises(DomainError) as corrupt,
            neutral_transaction(
                factory, saved.fence, resource_scope="factor_objects"
            ) as session,
        ):
            derive_source_read_witness(
                session, saved.fence, source_id=saved.source.id, settings=settings
            )
        assert corrupt.value.code == "neutral_object_uncertain"
    finally:
        try:
            path.write_bytes(original)
        finally:
            path.chmod(original_mode)


def test_direct_sql_new_member_serializes_against_source_finalization(source_setup):
    from backend.app.core.maintenance import shared_lifecycle
    from backend.app.rdagent_models import FactorGenerationModelOperation
    from backend.app.rdagent_source_publication import (
        finalize_source,
        source_storage_guard,
    )

    saved = persist(source_setup)
    factory, _, _, settings = source_setup
    with factory() as session:
        member = session.get(FactorGenerationModelOperation, saved.member_ids[-1])
        operation = session.get(ResearchModelOperation, member.operation_id)
        operation_fields = {
            c.name: getattr(operation, c.name)
            for c in ResearchModelOperation.__table__.columns
        }
        member_fields = {
            c.name: getattr(member, c.name)
            for c in FactorGenerationModelOperation.__table__.columns
        }
    operation_fields.update(id=uuid4(), ordinal=operation.ordinal + 1, state="open")
    member_fields.update(
        id=uuid4(),
        operation_id=operation_fields["id"],
        idempotency_key=uuid4(),
        request_ordinal=member.request_ordinal + 1,
        predecessor_id=member.id,
        relation="continuation",
    )

    def insert_late_member(session):
        session.add(ResearchModelOperation(**operation_fields))
        session.flush()
        session.add(FactorGenerationModelOperation(**member_fields))
        session.flush()

    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        source_storage_guard(settings.storage_root),
        neutral_transaction(
            factory, saved.fence, resource_scope="factor_objects"
        ) as first,
    ):
        # Real source finalizer re-reads retained files and immutable witnesses;
        # its neutral transaction owns the actual same-run row lock.
        result = finalize_source(
            first, saved.fence, publication_id=saved.publication_id, settings=settings
        )
        assert result.source_id == saved.source.id
        with pytest.raises(DBAPIError) as conflict, factory.begin() as second:
            insert_late_member(second)
        assert conflict.value.orig.sqlstate == "55P03"
    # After release, this is no longer a lock conflict. The completed source/call
    # cannot acquire an uncompleted continuation following an observed stop.
    with pytest.raises(DBAPIError) as invalid, factory.begin() as second:
        insert_late_member(second)
    assert invalid.value.orig.sqlstate != "55P03"


def test_unicode_source_retains_exact_utf8_bytes(source_setup):
    source = "# 中文😀 maintained retention fixture\nvalue = '你好'\n".encode()
    saved = persist(source_setup, source)
    assert saved.version.code.encode() == source
    assert saved.codeobject.sha256 == hashlib.sha256(source).hexdigest()
