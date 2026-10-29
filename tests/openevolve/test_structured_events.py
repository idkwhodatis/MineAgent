"""Actual normal wire→fixed handler sequencing with scoped DB/peer doubles."""

import hashlib
import json
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import oe_structured_events as service
from backend.app.oe_structured_event_wire import read_event
from packages.openevolve_structured.checkpoint import canonical
from packages.research_runtime.process_contracts import ProcessError
from runtimes.openevolve_structured_host.peer import ControllerBinding
from runtimes.rdagent_operational.peer import KernelIdentity


def fixture(monkeypatch):
    leader = KernelIdentity(100, 1, "pid:[4]", "0::/x", 1, (0,) * 4, (0,) * 4)
    child = KernelIdentity(101, 2, "pid:[4]", "0::/x", 100, (65533,) * 4, (65533,) * 4)
    peer = NS(binding=ControllerBinding("a" * 64, "b" * 64, leader, child))
    epoch, admission = uuid4(), uuid4()
    calls = []
    state = NS(active=False, locked=False, fail_commit=False, session=NS())

    @contextmanager
    def txn(*a, **kw):
        assert state.locked and not state.active
        assert kw["resource_scope"] == "factor_numeric"
        state.active = True
        calls.append("begin")
        try:
            yield state.session
            if state.fail_commit:
                raise ProcessError("commit_failed")
            calls.append("commit")
        finally:
            state.active = False

    @contextmanager
    def guard(*a):
        assert not state.locked
        state.locked = True
        try:
            yield
        finally:
            state.locked = False

    monkeypatch.setattr(service, "neutral_transaction", txn)
    monkeypatch.setattr(service, "shared_lifecycle", lambda *a: nullcontext())
    monkeypatch.setattr(service, "native_storage_guard", guard)
    monkeypatch.setattr(service, "require_peer", lambda *a, **kw: calls.append("peer"))
    monkeypatch.setattr(
        service,
        "require_controller_context",
        lambda *a, **kw: NS(
            epoch=NS(id=epoch), config=NS(search=NS(seed_candidate_sha256="a" * 64))
        ),
    )
    factory, settings = NS(kw={"bind": object()}), NS(storage_root="/unused")

    def event(kind, payload):
        tag = (
            "transfer-event.v1"
            if kind == "worker_placement" or kind.startswith("checkpoint_upload_")
            else "event.v1"
        )
        value = {
            "schema_version": "mineagent.oe-structured-provider-" + tag,
            "admission_id": str(admission),
            "epoch_id": str(epoch),
            "kind": kind,
            "payload": payload,
        }
        return read_event(
            canonical(
                {
                    "event": value,
                    "event_sha256": hashlib.sha256(canonical(value)).hexdigest(),
                    "process_ticket": "c" * 64,
                }
            )
        )

    def call(e):
        return json.loads(
            service.apply_event(
                factory, NS(), e, settings, peer=peer, ticket_key=b"x" * 32
            )
        )

    return NS(**locals())


def test_seed_ack_uses_real_epoch_and_follows_commit(monkeypatch):
    c = fixture(monkeypatch)
    e = c.event("seed_source", {"candidate_sha256": "a" * 64})
    answer = c.call(e)
    assert (
        answer["receipt_id"] == str(c.epoch)
        and answer["event_sha256"] == e.event_sha256
    )
    assert c.calls == ["peer", "begin", "peer", "commit", "peer"]


def test_commit_failure_cannot_return_event_success(monkeypatch):
    c = fixture(monkeypatch)
    c.state.fail_commit = True
    with pytest.raises(ProcessError, match="commit_failed"):
        c.call(c.event("seed_source", {"candidate_sha256": "a" * 64}))
    assert "commit" not in c.calls


def test_placement_reply_matches_actual_installed_scheduler_abi(monkeypatch):
    c = fixture(monkeypatch)
    rid, sid = uuid4(), uuid4()

    def record(*a, **kw):
        assert c.state.active and c.state.locked and kw == {"peer": c.peer}
        return NS(id=rid, sampling_intent_id=sid)

    monkeypatch.setattr(service, "record_placement", record)
    payload = {
        "schema_version": "mineagent.oe-structured-worker-placement.v1",
        "epoch_id": str(c.epoch),
        "intent_id": str(sid),
        "controller_namespace_pid": 1,
        "worker_namespace_pid": 2,
        "worker_start_ticks": 123,
    }
    answer = c.call(c.event("worker_placement", payload))["result"]
    assert answer == {
        "placement_receipt_id": str(rid),
        "epoch_id": str(c.epoch),
        "intent_id": str(sid),
        "worker_namespace_pid": 2,
        "worker_start_ticks": 123,
    }


@pytest.mark.parametrize(
    "payload",
    [{}, {"candidate_sha256": "0" * 64}, {"candidate_sha256": "a" * 64, "extra": 1}],
)
def test_seed_payload_drift_never_acknowledged(monkeypatch, payload):
    c = fixture(monkeypatch)
    with pytest.raises(ProcessError):
        c.call(c.event("seed_source", payload))
    assert "commit" not in c.calls


@pytest.mark.parametrize("kind", ["checkpoint_prepare", "checkpoint_publish"])
def test_checkpoint_ack_uses_persisted_row_after_commit(monkeypatch, kind):
    c = fixture(monkeypatch)
    row = NS(id=uuid4(), state_sha256="a" * 64)
    payload = {"frontier": 0, "state_sha256": "a" * 64, "save_ordinal": 1}

    def callback(session, fence, epoch, raw, settings, *, peer):
        assert c.state.active and c.state.locked
        assert epoch == c.epoch and raw == canonical(payload) and peer is c.peer
        return row

    monkeypatch.setattr(
        service,
        "prepare_checkpoint" if kind == "checkpoint_prepare" else "publish_checkpoint",
        callback,
    )
    answer = c.call(c.event(kind, payload))
    assert answer["receipt_id"] == str(row.id)
    assert "commit" in c.calls
    if kind == "checkpoint_prepare":
        assert answer["result"] == {
            "token": str(row.id),
            "path": "/work/checkpoints/" + str(row.id),
        }
    else:
        assert answer["result"] == {
            "published": True,
            "frontier": 0,
            "state_sha256": "a" * 64,
        }


def test_checkpoint_commit_failure_has_no_ack(monkeypatch):
    c = fixture(monkeypatch)
    c.state.fail_commit = True
    monkeypatch.setattr(service, "prepare_checkpoint", lambda *a, **kw: NS(id=uuid4()))
    with pytest.raises(ProcessError, match="commit_failed"):
        c.call(c.event("checkpoint_prepare", {}))
    assert "commit" not in c.calls


@pytest.mark.parametrize("phase", ["begin", "chunk", "finish"])
def test_upload_owns_transactions_and_ack_uses_actual_retained_row(monkeypatch, phase):
    c = fixture(monkeypatch)
    token, upload_id, row_id = uuid4(), uuid4(), uuid4()
    upload = NS(id=upload_id, epoch_id=c.epoch)
    row = NS(id=row_id, upload_id=upload_id, epoch_id=c.epoch, chunk_sha256="b" * 64)
    values = iter([upload, row])
    c.state.session = NS(scalar=lambda q: next(values), get=lambda *a: row)
    monkeypatch.setattr(service, "scoped", lambda row, fence: row)
    result = {"token": str(token)}
    if phase == "begin":
        result["reserved"] = True
    elif phase == "chunk":
        result.update(sequence=0, sha256="b" * 64)
    else:
        result["receipt_id"] = str(row_id)

    def callback(factory, fence, epoch, payload, settings, *, peer):
        assert c.state.locked and not c.state.active
        assert epoch == c.epoch and peer is c.peer
        c.calls.append("upload_committed")
        return result

    monkeypatch.setattr(service, "upload_" + phase, callback)
    answer = c.call(c.event("checkpoint_upload_" + phase, {}))
    assert answer["result"] == result
    assert answer["receipt_id"] == str(upload_id if phase == "begin" else row_id)
    assert c.calls.index("upload_committed") < c.calls.index("commit")
