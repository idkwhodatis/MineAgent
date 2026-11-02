"""Actual publication/binding orchestration; explicit DB/filesystem doubles, not PG."""

import copy
import hashlib
from contextlib import contextmanager
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import oe_structured_provider_publication as p
from backend.app import openevolve_lock_admission as admission
from backend.app import research_broker_services as budgets
from backend.app import research_object_services as o
from backend.app.oe_structured_operational_models import OEStructuredProviderTransport
from backend.app.research_broker_models import (
    ResearchModelOperation,
    ResearchModelTransport,
)
from backend.app.research_object_models import ResearchObject, ResearchObjectOutcome
from packages.research_runtime.process_contracts import ProcessError
from tests.openevolve.test_structured_provider_receipts import chain


@pytest.fixture
def case(monkeypatch, tmp_path):
    document = chain()[0]
    raw = p.receipt_bytes(document)
    scope = {
        k: getattr(document, k)
        for k in ("owner_id", "run_id", "attempt_id", "generation")
    }
    fence = NS(**scope)
    oid, pid, mid, opid = uuid4(), uuid4(), uuid4(), uuid4()
    sha = hashlib.sha256(raw).hexdigest()
    publication = NS(
        id=pid,
        **scope,
        object_kind=p.OBJECT_KIND,
        sha256=sha,
        size_bytes=len(raw),
        storage_key="fixed",
        budget_reservation_id=uuid4(),
        peak_bytes=2 * len(raw) + 8192,
    )
    obj = NS(id=oid, object_kind=p.OBJECT_KIND, sha256=sha, size_bytes=len(raw))
    member = NS(
        id=mid,
        **scope,
        transport_id=document.transport_id,
        operation_id=opid,
        request_sha256=document.request_sha256,
        model_binding_sha256=document.model_binding_sha256,
    )
    transport = NS(
        id=document.transport_id, **scope, operation_id=opid, state="submitted"
    )
    operation = NS(
        id=opid,
        **scope,
        request_sha256=document.request_sha256,
        protocol="mineagent.oe-structured-provider-chat.v1",
    )
    state = NS(
        db={"reserved": False, "outcome": False, "charge": 0, "receipts": []},
        events=[],
        active=False,
        fail=None,
        race_replay=False,
    )

    class Session:
        def scalars(self, statement):
            result = (
                ["032c_oe_provider_table_checks"]
                if "alembic_version" in str(statement)
                else state.db["receipts"]
            )
            return NS(all=lambda: result)

        def scalar(self, statement):
            return publication if state.db["reserved"] else None

        def get(self, model, key):
            return {
                ResearchObject: obj,
                ResearchObjectOutcome: NS(object_id=oid)
                if state.db["outcome"]
                else None,
                OEStructuredProviderTransport: member,
                ResearchModelTransport: transport,
                ResearchModelOperation: operation,
            }.get(model)

        def add(self, row):
            state.db["receipts"].append(row)

        def flush(self):
            if state.fail == "bind":
                raise RuntimeError("bind failure")

    session = Session()

    @contextmanager
    def transaction(*args, **kwargs):
        assert not state.active and kwargs["resource_scope"] == "objects"
        state.active = True
        before = copy.deepcopy(state.db)
        state.events.append("begin")
        try:
            yield session
            if state.fail == "commit" and state.db["receipts"]:
                raise RuntimeError("commit failure")
        except BaseException:
            state.db = before
            state.events.append("rollback")
            raise
        else:
            state.events.append("commit")
            if state.fail == "unknown_commit" and state.db["receipts"]:
                raise RuntimeError("unknown commit failure")
        finally:
            state.active = False

    def require(*args, **kwargs):
        assert state.active
        return NS(), NS()

    @contextmanager
    def guard(*args, **kwargs):
        yield

    def reserve(*args, **kwargs):
        assert state.active
        state.db["reserved"] = True
        return publication

    def finalize(*args):
        assert state.active
        state.events.append("finalize")
        if state.fail == "finalize":
            raise RuntimeError("finalize failure")
        state.db["outcome"] = True
        state.db["charge"] = publication.peak_bytes
        return oid

    def write(*args, **kwargs):
        assert not state.active and state.db["reserved"]
        state.events.append("write")
        if state.fail == "write":
            raise RuntimeError("write failure")

    def get_publication(*args):
        if state.race_replay:
            state.db.update(outcome=True, charge=publication.peak_bytes)
        return publication

    for module in (p, o):
        monkeypatch.setattr(module, "neutral_transaction", transaction)
        monkeypatch.setattr(module, "require_neutral_transaction", require)
    monkeypatch.setattr(
        p, "reserve_budget", lambda *a, **k: NS(id=publication.budget_reservation_id)
    )
    monkeypatch.setattr(p, "reserve_object", reserve)
    monkeypatch.setattr(p, "read_research_object", lambda *a: raw)
    monkeypatch.setattr(
        p,
        "decode_spec",
        lambda *a: (None, NS(model_binding=NS(sha256=document.model_binding_sha256))),
    )
    monkeypatch.setattr(o, "_publication", get_publication)
    monkeypatch.setattr(
        o, "_versioned_identity", lambda *a: NS(storage_key="fixed", sha256=sha)
    )
    monkeypatch.setattr(o, "_check_write_capacity", lambda *a: None)
    monkeypatch.setattr(o, "publication_journal", lambda *a: b"{}")
    monkeypatch.setattr(o, "publish_bytes", write)
    monkeypatch.setattr(o, "finalize_object", finalize)
    monkeypatch.setattr(o, "shared_lifecycle", guard)
    monkeypatch.setattr(admission, "publication_storage_admission", guard)
    monkeypatch.setattr(budgets, "require_reservation", lambda *a, **k: None)
    state.invoke = lambda: p.retain_physical_receipt(
        NS(kw={"bind": object()}),
        fence,
        NS(storage_root=tmp_path),
        mid,
        document,
        "key",
    )
    state.publication = publication
    state.member = member
    return state


def test_first_receipt_three_transactions_and_exact_replay_one(case):
    assert case.invoke() is None
    assert case.events == [
        "begin",
        "commit",
        "begin",
        "commit",
        "write",
        "write",
        "begin",
        "finalize",
        "commit",
    ]
    assert (
        len(case.db["receipts"]) == 1
        and case.db["charge"] == case.publication.peak_bytes
    )
    case.events.clear()
    case.invoke()
    assert case.events == ["begin", "commit"]
    assert (
        len(case.db["receipts"]) == 1
        and case.db["charge"] == case.publication.peak_bytes
    )


def test_shared_publisher_completed_replay_binds_without_writes(case):
    case.race_replay = True
    case.invoke()
    assert case.events == ["begin", "commit", "begin", "finalize", "commit"]
    assert (
        len(case.db["receipts"]) == 1
        and case.db["charge"] == case.publication.peak_bytes
    )


@pytest.mark.parametrize("failure", ["write", "finalize", "bind", "commit"])
def test_failure_retains_committed_hold_and_retry_closes_once(case, failure):
    case.fail = failure
    with pytest.raises(RuntimeError, match="failure"):
        case.invoke()
    assert case.db == {"reserved": True, "outcome": False, "charge": 0, "receipts": []}
    assert (
        case.events[-1] == "rollback"
        if failure != "write"
        else case.events[-1] == "write"
    )
    case.fail = None
    case.invoke()
    assert case.db["outcome"] and len(case.db["receipts"]) == 1
    assert case.db["charge"] == case.publication.peak_bytes


def test_conflicting_retained_receipt_replay_rejected_without_refund(case):
    case.invoke()
    case.db["receipts"][0].receipt_sha256 = "f" * 64
    previous = copy.deepcopy(case.db)
    with pytest.raises(ProcessError):
        case.invoke()
    assert case.db["charge"] == previous["charge"] and len(case.db["receipts"]) == 1
    assert case.events[-1] == "rollback"


def test_unknown_commit_retains_fullcharge_and_duplicate_retry_is_readonly(case):
    case.fail = "unknown_commit"
    with pytest.raises(RuntimeError, match="unknown commit"):
        case.invoke()
    assert case.db["outcome"] and len(case.db["receipts"]) == 1
    assert case.db["charge"] == case.publication.peak_bytes
    case.fail = None
    case.events.clear()
    case.invoke()
    assert case.events == ["begin", "commit"]
    assert len(case.db["receipts"]) == 1
    assert case.db["charge"] == case.publication.peak_bytes


def test_replay_commit_failure_preserves_existing_fullcharge(case):
    case.invoke()
    case.fail = "commit"
    with pytest.raises(RuntimeError, match="commit failure"):
        case.invoke()
    assert case.db["outcome"] and len(case.db["receipts"]) == 1
    assert case.db["charge"] == case.publication.peak_bytes


def test_cross_owner_binding_rolls_back_finalization(case):
    case.member.owner_id = uuid4()
    with pytest.raises(ProcessError):
        case.invoke()
    assert case.db == {"reserved": True, "outcome": False, "charge": 0, "receipts": []}


def test_wrong_object_bytes_roll_back_finalization(case, monkeypatch):
    monkeypatch.setattr(p, "read_research_object", lambda *args: b"{}")
    with pytest.raises(ValueError):
        case.invoke()
    assert case.db == {"reserved": True, "outcome": False, "charge": 0, "receipts": []}


@pytest.mark.parametrize("failure", ["commit", "rollback", "expired", "missing"])
def test_actual_neutral_authority_rejects_callback_transaction_loss(
    monkeypatch, tmp_path, failure
):
    from contextlib import nullcontext
    from datetime import timedelta

    from backend.app import research_lifecycle as lifecycle
    from backend.app.models import utcnow
    from backend.app.services import DomainError
    from tests.research_runtime.test_neutral_terminal_commit import arranged

    factory, fence, _run, attempt, *_, commits = arranged(monkeypatch)
    pid, oid = uuid4(), uuid4()
    publication = NS(
        id=pid, object_kind=p.OBJECT_KIND, storage_key="fixed", size_bytes=2
    )
    monkeypatch.setattr(o, "shared_lifecycle", lambda *a: nullcontext())
    monkeypatch.setattr(
        admission, "publication_storage_admission", lambda *a: nullcontext()
    )
    monkeypatch.setattr(o, "_publication", lambda *a: publication)
    monkeypatch.setattr(o, "_versioned_identity", lambda *a: NS(storage_key="fixed"))
    monkeypatch.setattr(o, "finalize_object", lambda *a: oid)
    rows = factory.class_
    original_get = rows.get
    monkeypatch.setattr(
        rows,
        "get",
        lambda self, model, key, **kw: (
            NS(object_id=oid)
            if model is ResearchObjectOutcome
            else original_get(self, model, key, **kw)
        ),
    )

    def bind(session, object_id):
        assert object_id == oid
        lifecycle.require_neutral_transaction(session, fence, resource_scope="objects")
        if failure == "commit":
            session.commit()
        elif failure == "rollback":
            session.rollback()
        elif failure == "expired":
            attempt.lease_expires_at = utcnow() - timedelta(seconds=1)
        else:
            session.info.pop(lifecycle._NEUTRAL_KEY)

    with pytest.raises(DomainError):
        o.publish_reserved_object(
            factory, fence, pid, b"{}", NS(storage_root=tmp_path), bind_finalized=bind
        )
    assert commits == []
