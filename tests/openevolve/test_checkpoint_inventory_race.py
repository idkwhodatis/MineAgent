"""Actual collect/strict inventory with a real SQLite ORM append between them.

Other graph/authority/physical prerequisites are explicit doubles. SQLite table
has the real ORM columns, not PostgreSQL foreign keys or deferred triggers. The
success sentinel proves only the unchanged inventory boundary was reached.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine
from sqlalchemy.orm import Session

from backend.app import openevolve_checkpoint_publication as publication
from backend.app import openevolve_services as service
from backend.app.openevolve_checkpoint_boundary import CheckpointBoundary
from backend.app.services import DomainError
from packages.openevolve_adapter.host_contracts import CheckpointPublicationCommand
from tests.openevolve.test_checkpoint_evidence import fixture, wrap
from tests.openevolve.test_operational_checkpoint_publication import graph


class InventoryAccepted(Exception):
    pass


def setup_graph(monkeypatch, tmp_path):
    fence, epoch, intent, data, _, _ = graph(monkeypatch)
    model = publication.m.SearchWorkerOutcome
    engine = create_engine("sqlite:///" + str(tmp_path / "epoch.db"))
    metadata = MetaData()
    Table(
        model.__tablename__,
        metadata,
        *(
            Column(c.name, c.type, primary_key=c.primary_key, nullable=c.nullable)
            for c in model.__table__.columns
        ),
    )
    metadata.create_all(engine)
    terminal = data[publication.m.SearchTerminalApplication][0]
    intent.epoch_id = epoch.id
    intent.terminal_id = terminal.id
    inventory, _ = wrap(fixture()[0])

    class View:
        def __init__(self, session):
            self.session = session

        def scalars(self, query):
            selected = query.column_descriptions[0]["entity"]
            if selected is model:
                return self.session.scalars(query)
            return NS(all=lambda: data[selected])

        def scalar(self, query):
            selected = query.column_descriptions[0]["entity"]
            if selected is publication.m.SearchCheckpointPublication:
                return None
            assert selected is publication.m.SearchTerminalApplication
            return terminal

    monkeypatch.setattr(service, "_locked_epoch", lambda *a: epoch)
    monkeypatch.setattr(service, "_owned_row", lambda *a: intent)
    monkeypatch.setattr(service, "_latest_control", publication._latest_control)
    monkeypatch.setattr(
        service,
        "_validate_control_chain",
        lambda *a: (_ for _ in ()).throw(InventoryAccepted()),
    )

    def append():
        with Session(engine) as session:
            row = model(
                id=uuid4(),
                **vars(fence),
                epoch_id=epoch.id,
                intent_id=uuid4(),
                native_iteration=1,
                status="worker_error",
                evidence_object_id=uuid4(),
                evidence_sha256="c" * 64,
            )
            session.add(row)
            session.commit()

    def collect():
        with Session(engine) as session:
            return publication.collect_closure(View(session), fence, epoch, intent)

    def verify(closure):
        monkeypatch.setattr(
            service,
            "_read_evidence",
            lambda _s, _f, _r, kind, _settings: (
                inventory if kind == "search_checkpoint_inventory" else closure
            ).model_dump(mode="json"),
        )
        command = CheckpointPublicationCommand(
            epoch_id=epoch.id,
            intent_id=intent.id,
            inventory={"id": uuid4(), "sha256": "a" * 64},
            closure={"id": uuid4(), "sha256": "b" * 64},
        )
        with Session(engine) as session:
            service.publish_checkpoint_snapshot(View(session), fence, command, object())

    return collect, append, verify, engine


def test_actual_append_between_collection_and_validation_reproduces_failure(
    monkeypatch, tmp_path
):
    collect, append, verify, engine = setup_graph(monkeypatch, tmp_path)
    try:
        closure = collect()
        assert closure.worker_outcome_ids == ()
        append()
        with pytest.raises(
            DomainError, match="durable epoch evidence: worker_outcome_ids"
        ):
            verify(closure)
        assert len(collect().worker_outcome_ids) == 1
    finally:
        engine.dispose()


def test_boundary_keeps_complete_projection_stable_then_retains_later_append(
    monkeypatch, tmp_path
):
    collect, append, verify, engine = setup_graph(monkeypatch, tmp_path)
    gate, requested, done = CheckpointBoundary(), threading.Event(), threading.Event()
    deadline = time.monotonic() + 3

    def worker():
        requested.set()
        with gate.enter(checkpoint=False, deadline=deadline):
            append()
            done.set()

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with gate.enter(checkpoint=True, deadline=deadline):
                closure = collect()
                future = pool.submit(worker)
                assert requested.wait(1)
                assert not done.is_set()
                with pytest.raises(InventoryAccepted):
                    verify(closure)
            future.result(timeout=2)
        assert done.is_set() and len(collect().worker_outcome_ids) == 1
        # An out-of-boundary writer is never hidden or filtered out.
        with pytest.raises(
            DomainError, match="durable epoch evidence: worker_outcome_ids"
        ):
            verify(closure)
    finally:
        engine.dispose()
