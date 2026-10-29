"""Actual retained journal reads with DB authority doubles; not PostgreSQL proof."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app import numeric_rejection_services as services
from packages.research_runtime.process_contracts import ProcessError, digest
from tests.research_runtime.test_numeric_rejection import retained


def persisted(monkeypatch, root):
    admission, launch, identity, stop, value, path = retained(root)
    receipts = {
        kind: SimpleNamespace(
            id=uuid4(),
            payload=obj.model_dump(mode="json"),
            payload_sha256=digest(obj.model_dump(mode="json")),
        )
        for kind, obj in {
            "launch_intent": launch.intent,
            "launch_identity": identity,
            "stop": stop,
            "rejection": value,
        }.items()
    }
    state = SimpleNamespace(status="stopped")
    calls = []
    monkeypatch.setattr(
        services, "require_numeric_rejection_schema", lambda *a: calls.append("fence")
    )
    monkeypatch.setattr(services, "_persisted", lambda *_: (admission, state, receipts))
    return admission, receipts, state, path, calls


def test_fresh_reader_returns_exact_receipt_ids_and_original_payload(
    monkeypatch, tmp_path
):
    admission, receipts, _, path, calls = persisted(monkeypatch, tmp_path)
    proof = services.read_numeric_rejection(
        None, admission, admission.id, storage_root=tmp_path
    )
    assert proof.admission_id == admission.id
    assert proof.rejection_receipt_id == receipts["rejection"].id
    assert proof.stop_receipt_hash == receipts["stop"].payload_sha256
    assert proof.outcome == "child_nonzero" and proof.child_exit_code == 1
    assert calls == ["fence", "fence"]
    (path / "control" / "rejection.json").unlink()
    with pytest.raises(ProcessError):
        services.read_numeric_rejection(
            None, admission, admission.id, storage_root=tmp_path
        )
    assert calls == ["fence", "fence", "fence"]


@pytest.mark.parametrize("mutation", ["scope", "state", "missing", "extra", "drift"])
def test_missing_or_changed_db_authority_cannot_reuse_physical_receipt(
    monkeypatch, tmp_path, mutation
):
    admission, receipts, state, _, _ = persisted(monkeypatch, tmp_path)
    fence = admission
    if mutation == "scope":
        fence = admission.model_copy(update={"owner_id": uuid4()})
    elif mutation == "state":
        state.status = "uncertain"
    elif mutation == "missing":
        del receipts["rejection"]
    elif mutation == "extra":
        receipts["extraction"] = receipts["rejection"]
    else:
        receipts["rejection"].payload = {
            **receipts["rejection"].payload,
            "child_exit_code": 0,
        }
    with pytest.raises(ProcessError):
        services.read_numeric_rejection(
            None, fence, admission.id, storage_root=tmp_path
        )


def test_fresh_fence_denial_precedes_physical_read(monkeypatch, tmp_path):
    admission, _, _, _, _ = persisted(monkeypatch, tmp_path)

    def denied(*_):
        raise ProcessError("stale_fence")

    monkeypatch.setattr(services, "require_numeric_rejection_schema", denied)
    with pytest.raises(ProcessError, match="stale_fence"):
        services.read_numeric_rejection(
            None, admission, admission.id, storage_root=tmp_path
        )


@pytest.mark.parametrize("status", ["reserved", "uncertain", "settled"])
def test_settlement_charges_whole_original_hold_once(monkeypatch, tmp_path, status):
    from backend.app import research_broker_services as broker
    from packages.research_runtime.budget_contracts import BudgetVector

    admission, _, _, _, _ = persisted(monkeypatch, tmp_path)
    scope = {
        key: getattr(admission, key)
        for key in ("owner_id", "run_id", "attempt_id", "generation")
    }
    amount = BudgetVector(
        process_slots=1, wall_milliseconds=60000, storage_bytes=67_174_400
    ).model_dump()
    reservation = SimpleNamespace(
        id=admission.reservation_id,
        purpose="process_unit",
        state=status,
        amounts=amount,
        **scope,
    )
    previous = (
        SimpleNamespace(charge=amount, outcome="conservative_unknown", **scope)
        if status == "settled"
        else None
    )
    session = SimpleNamespace(
        get=lambda *a, **k: reservation, scalar=lambda *_: previous
    )
    calls = []
    monkeypatch.setattr(broker, "settle_budget", lambda *a, **k: calls.append(k))
    proof = services.settle_numeric_rejection(
        session, admission, admission.id, storage_root=tmp_path
    )
    assert proof.admission_id == admission.id
    assert calls == [
        {
            "charge": BudgetVector.model_validate(amount),
            "outcome": "conservative_unknown",
        }
    ]


@pytest.mark.parametrize(
    "mutation",
    [
        "scope",
        "purpose",
        "floor",
        "refunded",
        "failed",
        "missing_settlement",
        "unsettled_with_receipt",
    ],
)
def test_settlement_never_refunds_or_relabels_unknown_accounting(
    monkeypatch, tmp_path, mutation
):
    from backend.app import research_broker_services as broker
    from packages.research_runtime.budget_contracts import BudgetVector

    admission, _, _, _, _ = persisted(monkeypatch, tmp_path)
    scope = {
        key: getattr(admission, key)
        for key in ("owner_id", "run_id", "attempt_id", "generation")
    }
    amount = BudgetVector(
        process_slots=1, wall_milliseconds=60000, storage_bytes=67_174_400
    ).model_dump()
    reservation = SimpleNamespace(
        id=admission.reservation_id,
        purpose="process_unit",
        state="settled",
        amounts=amount,
        **scope,
    )
    previous = SimpleNamespace(charge=amount, outcome="conservative_unknown", **scope)
    if mutation == "scope":
        previous.owner_id = uuid4()
    elif mutation == "purpose":
        reservation.purpose = "model_transport"
    elif mutation == "floor":
        reservation.amounts = BudgetVector().model_dump()
    elif mutation == "refunded":
        previous.charge = BudgetVector().model_dump()
    elif mutation == "failed":
        previous.outcome = "measured"
    elif mutation == "missing_settlement":
        previous = None
    else:
        reservation.state = "uncertain"
    session = SimpleNamespace(
        get=lambda *a, **k: reservation, scalar=lambda *_: previous
    )
    monkeypatch.setattr(
        broker,
        "settle_budget",
        lambda *a, **k: pytest.fail("Invalid accounting reached settlement"),
    )
    with pytest.raises(ProcessError):
        services.settle_numeric_rejection(
            session, admission, admission.id, storage_root=tmp_path
        )
