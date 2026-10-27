"""Actual publisher boundary shape with explicit ledger/transaction doubles."""

import hashlib
from contextlib import contextmanager
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import openevolve_object_publication as m
from backend.app.research_object_models import ResearchObject, ResearchObjectOutcome


@pytest.fixture
def case(monkeypatch):
    fence = NS(owner_id=uuid4(), run_id=uuid4(), attempt_id=uuid4(), generation=1)
    raw = b'{"value":1}'
    sha = hashlib.sha256(raw).hexdigest()
    oid, pid, rid = uuid4(), uuid4(), uuid4()
    obj = NS(
        id=oid,
        owner_id=fence.owner_id,
        object_kind="model_response",
        sha256=sha,
        size_bytes=len(raw),
    )
    previous = NS(
        id=pid,
        **fence.__dict__,
        object_kind="model_response",
        sha256=sha,
        size_bytes=len(raw),
    )
    data = NS(previous=None, outcome=None, events=[], obj=obj, raw=raw)

    class Session:
        def scalar(self, q):
            return data.previous

        def get(self, cls, key):
            return (
                obj
                if cls is ResearchObject
                else data.outcome
                if cls is ResearchObjectOutcome
                else None
            )

    session = Session()

    @contextmanager
    def txn(*a, **k):
        data.events.append("begin")
        yield session
        data.events.append("commit")

    monkeypatch.setattr(m, "neutral_transaction", txn)
    monkeypatch.setattr(m, "_current_search", lambda *_: object())
    monkeypatch.setattr(m, "require_search_identity", lambda *_: None)
    monkeypatch.setattr(m, "reserve_budget", lambda *a, **k: NS(id=rid))
    monkeypatch.setattr(
        m, "reserve_object", lambda *a, **k: data.events.append("reserve") or previous
    )

    def publish(*args):
        data.events.append("publish_uuid")
        data.previous = previous
        data.outcome = NS(object_id=oid)
        return oid  # exact shared publisher API; never ORM object

    monkeypatch.setattr(m, "publish_reserved_object", publish)
    monkeypatch.setattr(m, "read_research_object", lambda *a: data.raw)
    data.invoke = lambda: m.publish_search_object(
        None,
        fence,
        kind="model_response",
        content=raw,
        logical_key="oe:test",
        settings=None,
    )
    data.previous_row = previous
    return data


def test_shared_publisher_uuid_resolved_fresh_then_settled_replay_readonly(case):
    result = case.invoke()
    assert type(result) is m.PublishedEvidence and result.id == case.obj.id
    assert case.events.index("publish_uuid") > case.events.index("commit")
    count = len(case.events)
    assert case.invoke() == result
    assert (
        "reserve" not in case.events[count:]
        and "publish_uuid" not in case.events[count:]
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("object_kind", "model_request"),
        ("sha256", "0" * 64),
        ("size_bytes", 999),
        ("run_id", uuid4()),
    ],
)
def test_existing_publication_drift_does_not_write(case, field, value):
    case.previous = case.previous_row
    setattr(case.previous, field, value)
    with pytest.raises(ValueError, match="replay drift"):
        case.invoke()
    assert "publish_uuid" not in case.events and "reserve" not in case.events


def test_fresh_read_hash_mismatch_rejected_after_truthful_publication(case):
    case.raw = b'{"value":2}'
    with pytest.raises(ValueError, match="retained"):
        case.invoke()
    assert "publish_uuid" in case.events


def test_unknown_kind_rejected_before_transaction(case):
    with pytest.raises(ValueError):
        m.publish_search_object(
            None,
            None,
            kind="arbitrary_pickle",
            content=b"{}",
            logical_key="oe:test",
            settings=None,
        )
    assert not case.events
