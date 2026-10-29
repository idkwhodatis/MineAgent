"""Persisted-accounting doubles, not physical receipt or real PG proof."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import oe_structured_quota as q
from packages.research_runtime.budget_contracts import BudgetVector


class Session:
    def __init__(self, rows=(), receipts=(), revision=q.REVISION):
        self.rows = list(rows)
        self.receipts = list(receipts)
        self.revision = revision
        self.queries = []

    def scalars(self, sql):
        self.queries.append(str(sql))
        return NS(
            all=lambda: (
                [self.revision] if "alembic_version" in str(sql) else self.receipts
            )
        )

    def get(self, model, identity):
        return None

    def execute(self, sql):
        self.queries.append(str(sql))
        return NS(all=lambda: self.rows)


def sdk(protocol=q.OE_PROTOCOL):
    scope = {
        "owner_id": uuid4(),
        "run_id": uuid4(),
        "attempt_id": uuid4(),
        "generation": 1,
    }
    row = lambda **kw: NS(**scope, **kw)
    iteration = uuid4()
    reservation = row(
        id=uuid4(),
        amounts=BudgetVector(
            storage_bytes=123, process_slots=1, wall_milliseconds=5000
        ).model_dump(),
        state="settled",
        logical_key="oe-structured-provider:" + str(iteration),
    )
    operation = row(id=uuid4(), protocol=protocol, request_sha256="a" * 64)
    transport = row(
        id=uuid4(),
        operation_id=operation.id,
        reservation_id=reservation.id,
        state="completed",
        ordinal=1,
    )
    settlement = row(
        reservation_id=reservation.id,
        outcome="conservative_unknown",
        charge=dict(reservation.amounts),
    )
    member = (
        row(
            id=uuid4(),
            transport_id=transport.id,
            operation_id=operation.id,
            iteration_intent_id=iteration,
            request_sha256=operation.request_sha256,
        )
        if protocol == q.OE_PROTOCOL
        else None
    )
    kind = "oe_structured_provider_receipt" if member else "rd_provider_process_receipt"
    receipts = [
        row(kind=k, receipt_object_kind=kind, size_bytes=100)
        for k in ("intent", "started", "stopped")
    ]
    return (reservation, transport, settlement, operation, member), receipts


@pytest.mark.parametrize("protocol", [q.OE_PROTOCOL, *sorted(q.RD_PROTOCOLS)])
def test_both_engines_exact_membership_releases_one_peak(protocol):
    row, receipts = sdk(protocol)
    session = Session([row], receipts)
    assert q.sdk_process_holds(session) == ()
    expected = "membership_id" if protocol == q.OE_PROTOCOL else "transport_id"
    assert expected in session.queries[-1]


@pytest.mark.parametrize(
    "fault",
    [
        "orphan",
        "unknown",
        "scope",
        "partial",
        "refund",
        "missing_dimension",
        "missing_stop",
        "duplicate_kind",
        "wrong_kind",
        "wrong_member",
        "wrong_request",
        "wrong_key",
        "wrong_protocol",
        "unfinished",
    ],
)
def test_sdk_unknown_or_incomplete_peak_never_disappears(fault):
    row, receipts = sdk()
    reservation, transport, settlement, operation, member = row
    if fault == "orphan":
        row = (reservation, None, None, None, None)
    elif fault == "unknown":
        reservation.state = "uncertain"
    elif fault == "scope":
        receipts[-1].owner_id = uuid4()
    elif fault == "partial":
        settlement.charge["storage_bytes"] = 0
    elif fault == "refund":
        settlement.outcome = "not_submitted"
    elif fault == "missing_dimension":
        settlement.charge.pop("cost_microunits")
    elif fault == "missing_stop":
        receipts.pop()
    elif fault == "duplicate_kind":
        receipts[-1].kind = "intent"
    elif fault == "wrong_kind":
        receipts[-1].receipt_object_kind = "rd_provider_process_receipt"
    elif fault == "wrong_member":
        member.transport_id = uuid4()
    elif fault == "wrong_request":
        member.request_sha256 = "b" * 64
    elif fault == "wrong_key":
        reservation.logical_key = "caller-assertion"
    elif fault == "wrong_protocol":
        operation.protocol = "unknown"
    elif fault == "unfinished":
        transport.state = "submitted"
    assert q.sdk_process_holds(Session([row], receipts)) == (
        (reservation.owner_id, 123, 8),
    )


def test_duplicate_consumers_fail_not_double_or_under_count():
    row, receipts = sdk()
    with pytest.raises(ValueError, match="multiple consumers"):
        q.sdk_process_holds(Session([row, row], receipts))


@pytest.mark.parametrize(
    "revision", ["026_rd_seeded_submissions", "028_future", "unknown"]
)
def test_exact_revision_only_no_shared_row_queries(revision):
    session = Session(revision=revision)
    assert q.sdk_process_holds(session) == ()
    assert q.checkpoint_upload_holds(session) == ()
    assert len(session.queries) == 2


def upload():
    (reservation, *_), _ = sdk()
    scope = {k: getattr(reservation, k) for k in q.SCOPE}
    item = NS(
        **scope,
        id=uuid4(),
        epoch_id=uuid4(),
        intent_id=uuid4(),
        reservation_id=reservation.id,
        total_bytes=100,
        chunk_count=88,
        file_count=11,
    )
    reservation.logical_key = "oe-structured-upload:" + str(item.intent_id)
    reservation.amounts = BudgetVector(storage_bytes=2916552).model_dump()
    settled = NS(
        **scope,
        reservation_id=reservation.id,
        outcome="conservative_unknown",
        charge=dict(reservation.amounts),
    )
    return reservation, item, settled


def test_upload_maximum_and_orphan_are_conservatively_400_files():
    reservation, item, settled = upload()
    reservation.state = "uncertain"
    assert q.checkpoint_upload_holds(Session([(reservation, item, settled)])) == (
        (reservation.owner_id, 2916552, 400),
    )
    assert q.checkpoint_upload_holds(Session([(reservation, None, settled)])) == (
        (reservation.owner_id, 2916552, 400),
    )


def test_upload_full_settlement_alone_not_controller_stop(monkeypatch):
    row = upload()
    monkeypatch.setattr(q, "_controller_settled", lambda *a: False)
    assert q.checkpoint_upload_holds(Session([row])) == (
        (row[0].owner_id, 2916552, 400),
    )
    monkeypatch.setattr(q, "_controller_settled", lambda *a: True)
    assert q.checkpoint_upload_holds(Session([row])) == ()
    row[2].charge["cost_microunits"] = 1
    assert q.checkpoint_upload_holds(Session([row])) == (
        (row[0].owner_id, 2916552, 400),
    )


@pytest.mark.parametrize(
    "fault",
    ["scope", "key", "chunks", "files", "duplicate", "dimensions", "undercharge"],
)
def test_upload_malformed_inventory_fail_closed(fault):
    reservation, item, settled = upload()
    rows = [(reservation, item, settled)]
    if fault == "scope":
        item.owner_id = uuid4()
    elif fault == "key":
        reservation.logical_key = "oe-structured-upload:wrong"
    elif fault == "chunks":
        item.chunk_count = 89
    elif fault == "files":
        item.file_count = 12
    elif fault == "duplicate":
        rows *= 2
    elif fault == "dimensions":
        reservation.amounts.pop("cost_microunits")
    elif fault == "undercharge":
        reservation.amounts["storage_bytes"] -= 1
    with pytest.raises(ValueError):
        q.checkpoint_upload_holds(Session(rows))


def test_inventory_bound_before_processing():
    with pytest.raises(ValueError, match="bound"):
        q.sdk_process_holds(Session([None] * 10001))
    with pytest.raises(ValueError, match="bound"):
        q.checkpoint_upload_holds(Session([None] * 10001))


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "epoch",
        "scope",
        "state",
        "profile",
        "purpose",
        "key",
        "refund",
        "partial",
        "stop_missing",
        "stop_boolean",
        "stop_hash",
    ],
)
def test_upload_release_requires_actual_bound_controller_membership(fault):
    reservation, upload, _ = globals()["upload"]()
    scope = {k: getattr(upload, k) for k in q.SCOPE}
    row = lambda **kw: NS(**scope, **kw)
    epoch = row(id=upload.epoch_id, controller_admission_id=uuid4())
    admission = row(
        id=epoch.controller_admission_id,
        reservation_id=reservation.id,
        role="openevolve_orchestrator",
        profile="mineagent.oe-structured-orchestrator.v1",
    )
    state = row(status="stopped")
    reservation.purpose = "process_unit"
    reservation.logical_key = "oe-structured-controller:" + str(epoch.id)
    settlement = row(
        reservation_id=reservation.id,
        outcome="conservative_unknown",
        charge=dict(reservation.amounts),
    )
    payload = {
        "schema_version": "mineagent.oe-structured-controller-stop.v1",
        "launch_sha256": "a" * 64,
        "identity_sha256": "b" * 64,
        "child_sha256": None,
        "container_id": "c" * 64,
        "confirmed_absent": True,
        "kernel_identity_absent": True,
        "child_kernel_identity_absent": None,
    }
    stop = row(payload=payload, payload_sha256=q.digest(payload))
    records = {
        q.OEStructuredEpoch: epoch,
        q.ResearchProcessAdmission: admission,
        q.ResearchProcessState: state,
        q.ResearchBudgetReservation: reservation,
    }
    if fault == "epoch":
        records[q.OEStructuredEpoch] = None
    elif fault == "scope":
        epoch.owner_id = uuid4()
    elif fault == "state":
        state.status = "uncertain"
    elif fault == "profile":
        admission.profile = "other"
    elif fault == "purpose":
        reservation.purpose = "object_storage"
    elif fault == "key":
        reservation.logical_key = "unrelated"
    elif fault == "refund":
        settlement.outcome = "measured"
    elif fault == "partial":
        settlement.charge["storage_bytes"] = 0
    elif fault == "stop_boolean":
        payload["confirmed_absent"] = False
    elif fault == "stop_hash":
        stop.payload_sha256 = "d" * 64

    class BoundSession:
        def get(self, model, identity):
            return records.get(model)

        def scalars(self, sql):
            return NS(
                all=lambda: (
                    [settlement]
                    if "research_budget_settlements" in str(sql)
                    else ([] if fault == "stop_missing" else [stop])
                )
            )

    if fault in ("stop_boolean", "stop_hash"):
        with pytest.raises(ValueError):
            q._controller_settled(BoundSession(), upload)
    else:
        assert q._controller_settled(BoundSession(), upload) is (fault is None)


def test_cross_engine_consumer_cannot_release_sdk_peak():
    row, receipts = sdk()
    row[3].protocol = min(q.RD_PROTOCOLS)
    with pytest.raises(ValueError, match="cross-engine"):
        q.sdk_process_holds(Session([row], receipts))


@pytest.mark.parametrize("field", ["process_slots", "cost_microunits"])
def test_json_boolean_is_not_an_exact_integer_settlement(field):
    row, receipts = sdk()
    row[2].charge[field] = bool(row[2].charge[field])
    assert q.sdk_process_holds(Session([row], receipts)) == ((row[0].owner_id, 123, 8),)
