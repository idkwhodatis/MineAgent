"""Real ABI/file bytes through transaction doubles, not a PG/kernel proof."""

import hashlib
from contextlib import contextmanager
from types import SimpleNamespace as NS
from uuid import UUID, uuid4

import pytest

from backend.app import oe_structured_checkpoint_services as service
from backend.app import oe_structured_checkpoint_upload as upload
from packages.openevolve_structured.checkpoint import canonical
from packages.research_runtime.process_contracts import ProcessError
from runtimes.openevolve_structured.checkpoint_transfer import transfer_checkpoint
from tests.openevolve.test_structured_checkpoint import domain, native  # noqa: F401
from tests.openevolve.test_structured_checkpoint_publication import files  # noqa: F401


@pytest.fixture
def transfer(monkeypatch, files, domain):  # noqa: F811
    from backend.app import oe_structured_checkpoint_models as models

    scope = {
        "owner_id": uuid4(),
        "run_id": uuid4(),
        "attempt_id": uuid4(),
        "generation": 1,
    }
    fence, epoch, token = NS(**scope), uuid4(), uuid4()
    state = service.native_files_state(files, domain)
    control = NS(
        **scope,
        id=uuid4(),
        epoch_id=epoch,
        phase="seed",
        frontier=0,
        state_canonical=canonical(state).decode(),
        state_sha256=service.state_sha256(state),
    )
    intent = models.OEStructuredCheckpointIntent(
        id=token,
        **scope,
        epoch_id=epoch,
        control_state_id=control.id,
        save_ordinal=1,
        frontier=0,
        state_sha256=control.state_sha256,
    )
    rows = [intent]
    contents = {}
    calls = []

    class Session:
        def get(self, cls, identity):
            if identity == control.id:
                return control
            return next(
                (r for r in rows if isinstance(r, cls) and r.id == identity), None
            )

        def scalars(self, statement):
            cls = statement.column_descriptions[0]["entity"]
            result = [r for r in rows if isinstance(r, cls)]
            for condition in statement._where_criteria:
                result = [
                    r
                    for r in result
                    if getattr(r, condition.left.name) == condition.right.value
                ]
            if result and hasattr(result[0], "sequence"):
                result.sort(key=lambda r: r.sequence)
            return result

        def scalar(self, statement):
            return next(iter(self.scalars(statement)), None)

        def add(self, row):
            rows.append(row)

        def flush(self):
            pass

    session = Session()

    @contextmanager
    def transaction(*args, **kwargs):
        assert kwargs["resource_scope"] == "factor_numeric"
        calls.append("begin")
        yield session
        calls.append("commit")

    context = NS(config=NS(search=NS(domain=domain)))
    monkeypatch.setattr(upload, "neutral_transaction", transaction)
    monkeypatch.setattr(upload, "controller", lambda *a: context)
    monkeypatch.setattr(upload, "require_upload_hold", lambda *a: None)
    monkeypatch.setattr(upload, "latest_control", lambda *a: control)
    monkeypatch.setattr(service, "controller", lambda *a: context)
    monkeypatch.setattr(service, "current_state", lambda *a: (control, state))
    monkeypatch.setattr(
        upload,
        "reserve_budget",
        lambda *a, **kw: calls.append(("reserve", kw)) or NS(id=uuid4()),
    )
    import backend.app.openevolve_upload_quota as quota

    monkeypatch.setattr(
        quota, "require_upload_capacity", lambda *a: calls.append("quota")
    )

    def read(session_, fence_, identity, kind, sha, settings):
        k, raw = contents[identity]
        assert k == kind and hashlib.sha256(raw).hexdigest() == sha
        return raw

    def publish(factory, fence_, epoch_, token_, kind, raw, key, settings, peer):
        assert epoch_ == epoch and token_ == token
        oid = UUID(bytes=hashlib.sha256((kind + key).encode()).digest()[:16])
        assert oid not in contents or contents[oid] == (kind, raw)
        contents[oid] = (kind, raw)
        calls.append("publish")
        return oid

    monkeypatch.setattr(upload, "_publish", publish)
    monkeypatch.setattr(upload, "read_research_object", read)
    monkeypatch.setattr(service, "read_research_object", read)
    payloads = []

    def send(method, payload):
        payloads.append((method, payload))
        fn = {
            "checkpoint_upload_begin": upload.upload_begin,
            "checkpoint_upload_chunk": upload.upload_chunk,
            "checkpoint_upload_finish": upload.upload_finish,
        }[method]
        return fn(None, fence, epoch, payload, None, peer=None)

    return NS(**locals())


def test_real_transport_files_publish_distinct_actual_receipt_and_exact_replay(
    transfer,
):
    c = transfer
    receipt = transfer_checkpoint(
        c.send,
        token=c.token,
        files=tuple(sorted(c.files.items())),
        deadline=__import__("time").monotonic() + 10,
    )
    assert c.calls.index("quota") < c.calls.index("publish")
    assert c.calls[-1] == "commit"
    published_before = len(c.contents)
    for method, payload in c.payloads[:]:
        c.send(method, payload)
    assert len(c.contents) == published_before
    completion = c.session.get(c.models.OEStructuredCheckpointCompletion, UUID(receipt))
    assert (
        service.retained_files(c.session, c.fence, c.intent, completion, None)
        == c.files
    )
    raw = canonical(
        {
            "token": str(c.token),
            "frontier": 0,
            "state_sha256": c.control.state_sha256,
            "path": f"/work/checkpoints/{c.token}",
        }
    )
    publication = service.publish_checkpoint(
        c.session, c.fence, c.epoch, raw, None, peer=None
    )
    assert (
        service.publish_checkpoint(c.session, c.fence, c.epoch, raw, None, peer=None).id
        == publication.id
    )


def test_no_chunk_before_committed_inventory(transfer):
    c = transfer
    with pytest.raises(ProcessError):
        upload.upload_finish(
            None,
            c.fence,
            c.epoch,
            {"token": str(c.token), "chunks": 4},
            None,
            peer=None,
        )
    assert not c.contents


def test_changed_declared_inventory_not_replayed(transfer):
    c = transfer
    transfer_checkpoint(
        c.send,
        token=c.token,
        files=tuple(sorted(c.files.items())),
        deadline=__import__("time").monotonic() + 10,
    )
    payload = __import__("copy").deepcopy(c.payloads[0][1])
    payload["files"][0]["sha256"] = "f" * 64
    with pytest.raises(ProcessError):
        upload.upload_begin(None, c.fence, c.epoch, payload, None, peer=None)


def test_partial_upload_not_completed_or_refunded(transfer):
    c = transfer

    def interrupt(method, payload):
        if method == "checkpoint_upload_chunk" and payload["sequence"] == 1:
            raise OSError("lost original channel")
        return c.send(method, payload)

    with pytest.raises(OSError):
        transfer_checkpoint(
            interrupt,
            token=c.token,
            files=tuple(sorted(c.files.items())),
            deadline=__import__("time").monotonic() + 10,
        )
    assert any(isinstance(r, c.models.OEStructuredCheckpointUpload) for r in c.rows)
    assert (
        len([r for r in c.rows if isinstance(r, c.models.OEStructuredCheckpointChunk)])
        == 1
    )
    assert not any(
        isinstance(r, c.models.OEStructuredCheckpointCompletion) for r in c.rows
    )
    assert c.contents  # retained uncertainty, not deleted on a lost socket


def test_old_publication_replay_uses_original_bytes_but_new_publish_cannot_use_stale_cursor(
    transfer, monkeypatch
):
    c = transfer
    transfer_checkpoint(
        c.send,
        token=c.token,
        files=tuple(sorted(c.files.items())),
        deadline=__import__("time").monotonic() + 10,
    )
    raw = canonical(
        {
            "token": str(c.token),
            "frontier": 0,
            "state_sha256": c.control.state_sha256,
            "path": f"/work/checkpoints/{c.token}",
        }
    )
    original = service.publish_checkpoint(
        c.session, c.fence, c.epoch, raw, None, peer=None
    )
    monkeypatch.setattr(
        service,
        "current_state",
        lambda *a: (_ for _ in ()).throw(
            AssertionError("Must not reinterpret old save as newer frontier")
        ),
    )
    assert (
        service.publish_checkpoint(c.session, c.fence, c.epoch, raw, None, peer=None).id
        == original.id
    )
    c.rows.remove(original)
    monkeypatch.setattr(service, "current_state", lambda *a: (NS(id=uuid4()), c.state))
    with pytest.raises(ProcessError):
        service.publish_checkpoint(c.session, c.fence, c.epoch, raw, None, peer=None)


@pytest.mark.parametrize(
    "change", ["settled", "receipt", "cost", "scope", "logical_key"]
)
def test_live_upload_requires_original_full_unsettled_hold(monkeypatch, change):
    from backend.app.research_broker_models import ResearchBudgetReservation
    from packages.openevolve_structured.upload_contracts import Begin
    from packages.research_runtime.budget_contracts import BudgetVector

    scope = {
        "owner_id": uuid4(),
        "run_id": uuid4(),
        "attempt_id": uuid4(),
        "generation": 1,
    }
    token = uuid4()
    names = [
        "best_program.json",
        "best_program_info.json",
        "metadata.json",
        f"programs/{uuid4()}.json",
    ]
    begin = Begin.model_validate_json(
        canonical(
            {
                "token": str(token),
                "files": [
                    {"name": n, "size_bytes": 1, "sha256": "a" * 64} for n in names
                ],
            }
        )
    )
    row = NS(
        intent_id=token,
        inventory_canonical=canonical(begin.model_dump(mode="json")).decode(),
        inventory_sha256=hashlib.sha256(
            canonical(begin.model_dump(mode="json"))
        ).hexdigest(),
        total_bytes=4,
        file_count=4,
        chunk_count=4,
        reservation_id=uuid4(),
    )
    held = NS(
        **scope,
        id=row.reservation_id,
        purpose="object_storage",
        logical_key=f"oe-structured-upload:{token}",
        state="reserved",
        amounts=BudgetVector(storage_bytes=begin.transfer_peak_bytes).model_dump(),
    )
    settlement = None
    if change == "settled":
        held.state = "settled"
    elif change == "receipt":
        settlement = NS()
    elif change == "cost":
        held.amounts["cost_microunits"] = 1
    elif change == "scope":
        held.run_id = uuid4()
    else:
        held.logical_key += "-other"
    session = NS(
        get=lambda cls, key: held if cls is ResearchBudgetReservation else settlement
    )
    with pytest.raises(ProcessError):
        upload.require_upload_hold(session, NS(**scope), row)
