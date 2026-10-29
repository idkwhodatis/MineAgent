"""Distinct receipt grammar and explicit metadata graph; no PG/physical claim."""

from uuid import uuid4

import pytest

from packages.factors.contracts import canonical_bytes
from packages.openevolve_structured import provider_receipts as p
from packages.rdagent_provider.process_receipts import SDKIdentity


def chain():
    base = {
        "owner_id": uuid4(),
        "run_id": uuid4(),
        "attempt_id": uuid4(),
        "generation": 1,
        "transport_id": uuid4(),
        "request_sha256": "a" * 64,
        "model_binding_sha256": "b" * 64,
    }
    boot = uuid4()
    identity = SDKIdentity(
        pid=101,
        start_ticks=991,
        process_group=101,
        session_id=101,
        parent_pid=55,
        boot_id=boot,
        deadline_monotonic_ns=8000001,
    )
    return (
        p.SDKIntent(
            **base,
            admitted_monotonic_ns=1,
            deadline_monotonic_ns=8000001,
            maximum_duration_ms=8,
            boot_id=boot,
        ),
        p.SDKStarted(**base, identity=identity),
        p.SDKStopped(
            **base,
            identity=identity,
            observed_monotonic_ns=7000001,
            leader_reaped=True,
            process_group_absent=True,
        ),
    )


def test_exact_oe_chain_roundtrip():
    values = chain()
    for value in values:
        assert p.parse_receipt(p.receipt_bytes(value)) == value
    assert p.validate_chain(*values, completed=True) == values[-1]


@pytest.mark.parametrize(
    "field,value",
    [
        ("owner_id", str(uuid4())),
        ("transport_id", str(uuid4())),
        ("request_sha256", "c" * 64),
        ("model_binding_sha256", "c" * 64),
    ],
)
def test_cross_scope_request_rejected(field, value):
    intent, started, stopped = chain()
    data = stopped.model_dump(mode="json")
    data[field] = value
    stopped = p.SDKStopped.model_validate_json(canonical_bytes(data))
    with pytest.raises(ValueError):
        p.validate_chain(intent, started, stopped, completed=True)


def test_late_stop_is_containment_only():
    intent, started, stopped = chain()
    stopped = p.SDKStopped.model_validate_json(
        canonical_bytes(
            {**stopped.model_dump(mode="json"), "observed_monotonic_ns": 9000001}
        )
    )
    p.validate_chain(intent, started, stopped, completed=False)
    with pytest.raises(ValueError):
        p.validate_chain(intent, started, stopped, completed=True)


@pytest.mark.parametrize(
    "change",
    ["old_rd", "duplicate", "whitespace", "unknown", "false_stop", "boolean_pid"],
)
def test_reject_incompatible_or_forged_receipt(change):
    raw = p.receipt_bytes(chain()[-1])
    if change == "old_rd":
        raw = raw.replace(
            p.SCHEMA.encode(), b"mineagent.rd-provider-process-receipt.v1"
        )
    elif change == "duplicate":
        raw = b'{"kind":"stopped",' + raw[1:]
    elif change == "whitespace":
        raw = b" " + raw
    elif change == "unknown":
        raw = b'{"extra":1,' + raw[1:]
    elif change == "false_stop":
        raw = raw.replace(b'"leader_reaped":true', b'"leader_reaped":false')
    else:
        raw = raw.replace(b'"pid":101', b'"pid":true')
    with pytest.raises(ValueError):
        p.parse_receipt(raw)


def test_forged_model_copy_cannot_bypass_physical_field_validator():
    stopped = chain()[-1].model_copy(update={"leader_reaped": False})
    with pytest.raises(ValueError):
        p.receipt_bytes(stopped)


def test_receipt_membership_and_shared_transport_ids_are_distinct():
    from backend.app.oe_structured_operational_models import (
        OEStructuredProviderReceipt,
        OEStructuredProviderTransport,
    )

    table = OEStructuredProviderReceipt.__table__
    assert "membership_id" in table.c and "transport_id" not in table.c
    refs = {
        e.target_fullname for fk in table.foreign_key_constraints for e in fk.elements
    }
    assert "oe_structured_provider_transports.id" in refs
    assert "transport_id" in OEStructuredProviderTransport.__table__.c


def test_candidate_and_exchange_have_exact_epoch_and_sdk_membership_fks():
    from backend.app.oe_structured_models import (
        OEStructuredCandidate,
        OEStructuredProviderExchange,
    )

    for cls in (OEStructuredCandidate, OEStructuredProviderExchange):
        refs = {
            e.target_fullname
            for fk in cls.__table__.foreign_key_constraints
            for e in fk.elements
        }
        assert "oe_structured_epochs.id" in refs
    refs = {
        e.target_fullname
        for fk in OEStructuredProviderExchange.__table__.foreign_key_constraints
        for e in fk.elements
    }
    assert "oe_structured_provider_transports.id" in refs
    assert "oe_structured_iteration_intents.id" in refs
