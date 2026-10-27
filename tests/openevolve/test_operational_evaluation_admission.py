"""Real maintained staging bytes with explicit SQL/peer/publication doubles."""

import hashlib
import shutil
from contextlib import contextmanager
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from test_operational_native_process import case as native_case
from test_search_staging import fixture as staging_fixture

from backend.app import openevolve_evaluation_admission as s
from backend.app.openevolve_controller_services import typed_bootstrap
from packages.openevolve_adapter.host_contracts import SourceEnvelope
from packages.research_runtime.broker_contracts import digest
from packages.research_runtime.process_contracts import canonical


def setup(monkeypatch, tmp_path):
    base = native_case.__wrapped__(monkeypatch, tmp_path / "base")
    bootstrap, _ = typed_bootstrap(base.launch)
    args = staging_fixture(tmp_path / "data")
    owner, dataset_id = base.fence.owner_id, bootstrap.spec.execution.dataset_version_id
    root = tmp_path / "data" / "store"
    key = f"datasets/{owner}/{dataset_id}"
    target = root / key
    target.parent.mkdir(parents=True)
    shutil.move(str(root / "dataset"), target)
    dataset = NS(
        id=dataset_id,
        owner_id=owner,
        content_sha256=bootstrap.spec.execution.dataset_sha256,
        manifest=args["manifest"],
        storage_key=key,
    )
    candidate = NS(
        id=uuid4(),
        epoch_id=base.evaluation.epoch_id,
        kind="seed",
        source_sha256=args["source_sha256"],
        source_object_id=uuid4(),
        source_object_sha256="a" * 64,
    )
    prep = NS(
        id=base.evaluation.id,
        **vars(base.fence),
        epoch_id=candidate.epoch_id,
        candidate_id=candidate.id,
        source_sha256=candidate.source_sha256,
        execution_sha256=digest(bootstrap.spec.execution),
        window_id=args["window"].id,
        reservation_id=uuid4(),
        admission_id=uuid4(),
        logical_ticket_id=uuid4(),
        physical_ordinal=1,
    )
    hold = NS(
        id=prep.reservation_id,
        logical_key=f"oe-native:{prep.id}:1",
        amounts=s.FLOOR.model_dump(),
        state="reserved",
    )
    calls, records = [], []

    class Session:
        def get(self, cls, key):
            if cls is s.DatasetVersion:
                return dataset
            return next((r for r in records if type(r) is cls and r.id == key), None)

        def scalar(self, query):
            return None

        def add_all(self, values):
            records.extend(values)

        def add(self, row):
            records.append(row)

        def flush(self):
            calls.append("flush")

    session = Session()

    @contextmanager
    def transaction(*a, **k):
        calls.append("begin")
        yield session
        calls.append("commit")

    def resolve(*a, **k):
        calls.append("peer")
        return candidate, NS(id=uuid4()), None, bootstrap, args["window"]

    def read(*a):
        calls.append("read-source")
        return canonical(
            SourceEnvelope(
                source=args["source"].decode(), source_sha256=args["source_sha256"]
            ).model_dump(mode="json")
        )

    monkeypatch.setattr(s, "neutral_transaction", transaction)
    monkeypatch.setattr(s, "_owned_row", lambda *a: prep)
    monkeypatch.setattr(s, "_resolve", resolve)
    monkeypatch.setattr(s, "require_reservation", lambda *a, **k: hold)
    monkeypatch.setattr(s, "read_research_object", read)
    return NS(
        base=base,
        bootstrap=bootstrap,
        args=args,
        prep=prep,
        hold=hold,
        dataset=dataset,
        candidate=candidate,
        calls=calls,
        records=records,
        session=session,
        settings=NS(storage_root=root),
    )


def render(c):
    return s.render_prepared_input(
        None,
        c.base.fence,
        None,
        event=None,
        ticket_key=b"k" * 32,
        preparation_id=c.prep.id,
        settings=c.settings,
    )


def test_actual_render_has_no_unaccounted_staging_files(monkeypatch, tmp_path):
    c = setup(monkeypatch, tmp_path)
    before = set(c.settings.storage_root.rglob("*"))
    manifest, inputs = render(c)
    assert len(inputs) == len(manifest.files) == 5
    assert set(inputs) == {f.destination for f in manifest.files}
    assert manifest.evaluation_id == c.prep.id
    assert manifest.execution_plan_sha256 == c.prep.execution_sha256
    assert manifest.native_artifacts_sha256 != manifest.sha256
    assert set(c.settings.storage_root.rglob("*")) == before
    assert c.calls == ["begin", "peer", "read-source", "commit"]
    for item in manifest.files:
        assert hashlib.sha256(inputs[item.destination]).hexdigest() == item.sha256


@pytest.mark.parametrize(
    "fault",
    [
        "foreign_owner",
        "wrong_storage",
        "dataset_hash",
        "reservation_key",
        "reservation_amount",
        "source",
    ],
)
def test_render_rejects_scope_or_budget_before_staging(monkeypatch, tmp_path, fault):
    c = setup(monkeypatch, tmp_path)
    if fault == "foreign_owner":
        c.dataset.owner_id = uuid4()
    if fault == "wrong_storage":
        c.dataset.storage_key = "dataset"
    if fault == "dataset_hash":
        c.dataset.content_sha256 = "0" * 64
    if fault == "reservation_key":
        c.hold.logical_key = "oe-native:other:1"
    if fault == "reservation_amount":
        c.hold.amounts = {**c.hold.amounts, "storage_bytes": 0}
    if fault == "source":
        c.candidate.source_sha256 = "0" * 64
    with pytest.raises(ValueError):
        render(c)
    assert not c.records


def test_previously_admitted_evaluation_does_not_restage(monkeypatch, tmp_path):
    c = setup(monkeypatch, tmp_path)
    c.records.append(s.SearchEvaluation(id=c.prep.id))
    with pytest.raises(ValueError, match="reconciliation"):
        render(c)
    assert "read-source" not in c.calls


def test_admit_exact_retained_manifest_and_deadline_after_real_render(
    monkeypatch, tmp_path
):
    c = setup(monkeypatch, tmp_path)
    manifest, _ = render(c)

    def publish(*a, **kw):
        assert kw["content"] == canonical(manifest.model_dump(mode="json"))
        c.calls.append("publish")
        return NS(id=uuid4(), sha256=manifest.sha256)

    def persist(session, fence, intent, settings):
        assert session.get(s.SearchEvaluation, c.prep.id) is not None
        c.calls.append("admission")

    monkeypatch.setattr(s, "publish_search_object", publish)
    monkeypatch.setattr(s, "persist_native_intent", persist)
    intent = s.admit_prepared_input(
        None,
        c.base.fence,
        None,
        event=None,
        ticket_key=b"k" * 32,
        preparation_id=c.prep.id,
        manifest=manifest,
        settings=c.settings,
    )
    assert intent.admission.id == c.prep.admission_id
    assert intent.admission.ipc.ticket_sha256 is None
    assert (intent.not_after - intent.issued_at).total_seconds() == 60
    assert c.calls[-2:] == ["admission", "commit"]
    evaluation = next(r for r in c.records if type(r) is s.SearchEvaluation)
    assert evaluation.input_manifest_sha256 == manifest.native_artifacts_sha256
