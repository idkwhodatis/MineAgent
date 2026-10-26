"""Inactive publication protocol checks; no physical/PG acceptance claim."""

import json
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from backend.app.rdagent_models import (
    FactorSourcePublication,
    FactorSourcePublicationOutcome,
)
from backend.app.rdagent_source_publication import (
    finalize_source,
    reserve_source,
    source_publication_journal,
)
from backend.app.services import DomainError
from packages.research_runtime.fences import AttemptFence


def test_source_publication_reservation_and_outcome_are_one_to_one():
    for model, key in (
        (FactorSourcePublication, "budget_reservation_id"),
        (FactorSourcePublicationOutcome, "publication_id"),
    ):
        ddl = str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))
        assert f"UNIQUE ({key})" in ddl
    ddl = str(
        CreateTable(FactorSourcePublication.__table__).compile(
            dialect=postgresql.dialect()
        )
    )
    assert "peak_bytes = size_bytes * 2 + 8192" in ddl
    assert "UNIQUE (start_id)" in ddl


def test_source_journal_exact_attempt_and_destination_identity():
    fields = {
        key: UUID(int=i)
        for i, key in enumerate(
            (
                "id",
                "owner_id",
                "run_id",
                "attempt_id",
                "start_id",
                "call_id",
                "assembly_object_id",
                "budget_reservation_id",
                "factor_id",
                "factor_version_id",
            ),
            1,
        )
    }
    row = SimpleNamespace(
        **fields,
        generation=1,
        assembly_sha256="a" * 64,
        source_sha256="b" * 64,
        size_bytes=10,
        peak_bytes=8212,
    )
    raw = source_publication_journal(row)
    assert len(raw) < 4096
    assert json.loads(raw)["attempt_id"] == str(fields["attempt_id"])
    assert raw == source_publication_journal(row)
    row.generation = 2
    assert raw != source_publication_journal(row)


@pytest.mark.parametrize("action", ["reserve", "finalize"])
def test_source_publication_rejects_before_io_without_exact_transaction(action):
    class NoAuthority:
        def __init__(self):
            self.info = {}

        def scalar(self, *args):
            raise AssertionError("unauthorized publication reached SQL")

    fence = AttemptFence(
        owner_id=UUID(int=1),
        run_id=UUID(int=2),
        attempt_id=UUID(int=3),
        generation=1,
        lease_token=UUID(int=4),
    )
    with pytest.raises(DomainError):
        if action == "reserve":
            reserve_source(
                NoAuthority(),
                fence,
                call_id=UUID(int=5),
                assembly_object_id=UUID(int=6),
                assembly_sha256="a" * 64,
                settings=None,
            )
        else:
            finalize_source(
                NoAuthority(), fence, publication_id=UUID(int=7), settings=None
            )


@pytest.mark.parametrize(
    "failure",
    [
        "none",
        "second_write",
        "fresh_fence",
        "capacity",
        "file_capacity",
        "completed_replay",
        "observed",
        "terminal",
    ],
)
def test_source_writer_crash_boundaries_keep_files_and_do_not_refund(
    tmp_path, monkeypatch, failure
):
    import hashlib
    import os
    from contextlib import contextmanager

    import backend.app.core.atomic_storage as atomic
    import backend.app.rdagent_source_publication as service
    import backend.app.research_lifecycle as lifecycle
    import runtimes.factor_parser.supervisor as parser
    from backend.app.core import maintenance

    content = b"invalid but bounded Python\n"
    fields = {
        key: UUID(int=i)
        for i, key in enumerate(
            (
                "id",
                "owner_id",
                "run_id",
                "attempt_id",
                "start_id",
                "call_id",
                "assembly_object_id",
                "budget_reservation_id",
                "factor_id",
                "factor_version_id",
            ),
            1,
        )
    }
    row = SimpleNamespace(
        **fields,
        generation=1,
        assembly_sha256="a" * 64,
        source_sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        peak_bytes=2 * len(content) + 8192,
    )
    fence = AttemptFence(
        owner_id=row.owner_id,
        run_id=row.run_id,
        attempt_id=row.attempt_id,
        generation=1,
        lease_token=UUID(int=90),
    )
    events, writes = [], []
    settings = SimpleNamespace(
        storage_root=tmp_path,
        factor_max_files=4 if failure == "file_capacity" else 5,
        factor_installation_max_bytes=1 if failure == "capacity" else 100000,
        factor_owner_max_bytes=100000,
    )
    (tmp_path / "factors").mkdir()

    class Session:
        def scalar(self, statement):
            model = statement.column_descriptions[0]["entity"]
            if (
                failure == "observed"
                and model.__name__ == "FactorGenerationObservation"
            ):
                return object()
            if failure == "terminal" and model.__name__ == "FactorGenerationOutcome":
                return object()
            return object() if failure == "completed_replay" else None

        def scalars(self, statement):
            # Explicit historical source-only schema in this filesystem double.
            if str(statement) == "SELECT version_num FROM alembic_version":
                return SimpleNamespace(all=lambda: ["018_rd_source_persistence"])
            return SimpleNamespace(all=lambda: [row])

    @contextmanager
    def transaction(factory, current, *, resource_scope):
        assert current == fence and resource_scope == "factor_objects"
        events.append("transaction_enter")
        yield Session()
        events.append("transaction_commit")

    @contextmanager
    def lifecycle_guard(*args):
        events.append("maintenance_enter")
        yield
        events.append("maintenance_exit")

    @contextmanager
    def storage_guard(root):
        fd = os.open(root / "factors", os.O_RDONLY | os.O_DIRECTORY)
        try:
            events.append("storage_enter")
            yield fd
        finally:
            events.append("storage_exit")
            os.close(fd)

    def write(path, data, **kwargs):
        assert events[-1] == "transaction_commit" or writes
        writes.append(path)
        if failure == "second_write" and len(writes) == 2:
            raise OSError("injected source write failure")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def finalize(*args, **kwargs):
        assert events[-1] == "transaction_enter"
        if failure == "fresh_fence":
            raise DomainError("attempt_fenced", "injected lost fence", 409)
        return "immutable_source_outcome"

    monkeypatch.setattr(maintenance, "shared_lifecycle", lifecycle_guard)
    monkeypatch.setattr(lifecycle, "neutral_transaction", transaction)
    monkeypatch.setattr(service, "source_storage_guard", storage_guard)
    monkeypatch.setattr(service, "require_research_spec", lambda *args: None)
    monkeypatch.setattr(service, "_scope_row", lambda *args, **kwargs: row)
    monkeypatch.setattr(
        service,
        "_require_source_reservation",
        lambda *args, **kwargs: events.append("reservation_checked"),
    )
    monkeypatch.setattr(
        service, "physical_inventory", lambda *args, **kwargs: (0, 0, 0)
    )
    monkeypatch.setattr(parser, "assert_quiescent", lambda *args: None)
    monkeypatch.setattr(atomic, "publish_bytes", write)
    monkeypatch.setattr(service, "finalize_source", finalize)
    factory = SimpleNamespace(kw={"bind": object()})
    if failure in {
        "second_write",
        "fresh_fence",
        "capacity",
        "file_capacity",
        "observed",
        "terminal",
    }:
        with pytest.raises((DomainError, OSError)):
            service.publish_reserved_source(
                factory,
                fence,
                publication_id=row.id,
                content=content,
                settings=settings,
            )
    else:
        assert (
            service.publish_reserved_source(
                factory,
                fence,
                publication_id=row.id,
                content=content,
                settings=settings,
            )
            == "immutable_source_outcome"
        )
    if failure in {
        "capacity",
        "file_capacity",
        "completed_replay",
        "observed",
        "terminal",
    }:
        assert not writes
    else:
        assert len(writes) == 2 and writes[
            0
        ].read_bytes() == service.source_publication_journal(row)
        assert writes[1].exists() == (failure != "second_write")
    if failure == "second_write":
        assert events.count("transaction_enter") == 1
    if failure in {"none", "fresh_fence"}:
        assert events.count("transaction_enter") == 2
    assert events[0] == "maintenance_enter" and "storage_exit" in events


@pytest.mark.parametrize(
    "artifact", ["temporary", "symlink", "hardlink", "oversized_inventory"]
)
def test_source_retry_rejects_ambiguous_physical_inventory(tmp_path, artifact):
    import os

    from backend.app.rdagent_source_publication import _reject_temporary_files

    source = tmp_path / "source.py"
    source.write_bytes(b"x")
    maximum = 100
    if artifact == "temporary":
        (tmp_path / ".source.py.crash.tmp").write_bytes(b"partial")
    elif artifact == "symlink":
        (tmp_path / "alias").symlink_to(source)
    elif artifact == "hardlink":
        os.link(source, tmp_path / "alias")
    else:
        maximum = 0
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(DomainError):
            _reject_temporary_files(fd, maximum=maximum)
        assert source.read_bytes() == b"x"  # recovery never deletes evidence
    finally:
        os.close(fd)


@pytest.mark.parametrize("drift", ["consumer", "key", "amount"])
def test_source_reservation_cannot_back_another_domain_or_drift(monkeypatch, drift):
    import backend.app.rdagent_source_publication as service
    from packages.research_runtime.budget_contracts import BudgetVector

    publication = SimpleNamespace(
        start_id=UUID(int=1), budget_reservation_id=UUID(int=2), peak_bytes=9000
    )
    reservation = SimpleNamespace(
        id=publication.budget_reservation_id,
        logical_key=f"rdsource:{publication.start_id}",
        amounts=BudgetVector(storage_bytes=9000).model_dump(),
    )
    if drift == "key":
        reservation.logical_key = "some_other_storage"
    if drift == "amount":
        reservation.amounts = BudgetVector(storage_bytes=9001).model_dump()
    monkeypatch.setattr(
        service, "require_reservation", lambda *args, **kwargs: reservation
    )
    session = SimpleNamespace(
        scalar=lambda query: object() if drift == "consumer" else None
    )
    with pytest.raises(DomainError):
        service._require_source_reservation(session, object(), publication)
