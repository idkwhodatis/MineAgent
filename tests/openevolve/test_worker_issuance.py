"""Flush-only placement issuance wiring, not real socket or PostgreSQL evidence."""

import hashlib
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from backend.app.openevolve_worker_issuance import record_expected_worker_identity
from backend.app.services import DomainError
from packages.openevolve_adapter.host_contracts import EvidenceRef
from packages.openevolve_adapter.observer_transport import canonical
from packages.openevolve_adapter.placement_contracts import parse_placement_wire


def fixture():
    fence = SimpleNamespace(
        owner_id=uuid4(), run_id=uuid4(), attempt_id=uuid4(), generation=1
    )
    epoch = SimpleNamespace(id=uuid4(), admission_id=uuid4(), bootstrap_sha256="b" * 64)
    intent = SimpleNamespace(id=uuid4(), epoch_id=epoch.id)
    ticket = SimpleNamespace(
        id=uuid4(),
        epoch_id=epoch.id,
        intent_id=intent.id,
        admission_id=epoch.admission_id,
    )
    admission = SimpleNamespace(
        id=epoch.admission_id,
        logical_operation_id=uuid4(),
        image_id="sha256:" + "c" * 64,
        ticket_sha256=hashlib.sha256(("a" * 64).encode()).hexdigest(),
    )
    placement = {
        "schema_version": "mineagent.openevolve-worker-placement.v1",
        "epoch_id": str(epoch.id),
        "intent_id": str(intent.id),
        "controller_namespace_pid": 1,
        "worker_namespace_pid": 2,
        "worker_start_ticks": 100,
    }
    event = {
        "schema_version": "mineagent.openevolve-placement-event.v1",
        "admission_id": str(admission.id),
        "placement": placement,
    }
    event_sha = hashlib.sha256(canonical(event)).hexdigest()
    wire = canonical(
        {"event": event, "event_sha256": event_sha, "process_ticket": "a" * 64}
    )
    controller = {
        "host_pid": 10,
        "start_ticks": 90,
        "pid_namespace": "pid:[1]",
        "cgroup": "0::/unit",
    }
    worker = {
        "host_pid": 11,
        "start_ticks": 100,
        "pid_namespace": "pid:[1]",
        "cgroup": "0::/unit",
        "namespace_pid": 2,
        "parent_host_pid": 10,
    }
    evidence = {
        "schema_version": "mineagent.openevolve-worker-identity.v1",
        **{k: str(v) if k != "generation" else v for k, v in vars(fence).items()},
        "admission_id": str(admission.id),
        "epoch_id": str(epoch.id),
        "intent_id": str(intent.id),
        "ticket_id": str(ticket.id),
        "placement_event_sha256": event_sha,
        "placement": placement,
        "controller": controller,
        "worker": worker,
    }
    ref = EvidenceRef(
        id=uuid4(), sha256=hashlib.sha256(canonical(evidence)).hexdigest()
    )
    return fence, epoch, intent, ticket, admission, wire, evidence, ref


def call_fixture(data, *, existing=None, observed=None):
    from contextlib import ExitStack

    fence, epoch, intent, ticket, admission, wire, evidence, ref = data
    session = Mock()
    session.scalar.return_value = existing
    rows = {admission.id: admission, intent.id: intent, ticket.id: ticket}
    stack = ExitStack()
    for name, value in [
        ("_read_evidence", evidence),
        ("_locked_epoch", epoch),
        ("require_authenticated_peer", SimpleNamespace(leader=evidence["controller"])),
    ]:
        stack.enter_context(
            patch("backend.app.openevolve_worker_issuance." + name, return_value=value)
        )
    stack.enter_context(
        patch("backend.app.openevolve_worker_issuance.require_neutral_transaction")
    )
    auth = stack.enter_context(
        patch("backend.app.openevolve_worker_issuance.require_controller_peer")
    )
    stack.enter_context(
        patch(
            "backend.app.openevolve_worker_issuance._owned_row",
            side_effect=lambda _, model, identity, f: rows[identity],
        )
    )
    kernel = stack.enter_context(
        patch(
            "backend.app.openevolve_worker_issuance.observe_pool_worker",
            return_value=evidence["worker"] if observed is None else observed,
        )
    )
    return (
        stack,
        session,
        auth,
        kernel,
        lambda: record_expected_worker_identity(
            session,
            fence,
            object(),
            wire,
            evidence_reference=ref,
            expected_process_profile="fixed",
            settings=object(),
        ),
    )


def test_expected_identity_revalidates_live_controller_and_kernel_before_flush():
    data = fixture()
    stack, session, auth, kernel, call = call_fixture(data)
    with stack:
        row = call()
    assert row.host_pid == 11 and row.start_ticks == 100
    assert row.intent_id == data[2].id and row.ticket_id == data[3].id
    assert auth.call_count == 2 and kernel.call_count == 1
    session.add.assert_called_once_with(row)
    session.flush.assert_called_once()
    session.commit.assert_not_called()


def test_historical_exact_placement_replay_does_not_reissue_or_require_live_old_worker():
    data = fixture()
    _, _, _, ticket, _, _, evidence, ref = data
    old = SimpleNamespace(
        ticket_id=ticket.id,
        evidence_object_id=ref.id,
        evidence_sha256=ref.sha256,
        **{
            k: evidence["worker"][k]
            for k in ["host_pid", "start_ticks", "pid_namespace", "cgroup"]
        },
    )
    stack, session, auth, kernel, call = call_fixture(data, existing=old)
    with stack:
        assert call() is old
    assert auth.call_count == 1
    kernel.assert_not_called()
    session.add.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.parametrize("fault", ["wire_mismatch", "ticket", "scope", "kernel_reuse"])
def test_expected_identity_rejects_forged_or_stale_evidence(fault):
    data = list(fixture())
    observed = None
    if fault == "wire_mismatch":
        data[6]["placement_event_sha256"] = "0" * 64
    elif fault == "ticket":
        data[4].ticket_sha256 = "0" * 64
    elif fault == "scope":
        data[6]["generation"] = 2
    else:
        observed = {**data[6]["worker"], "start_ticks": 101}
    stack, session, _, _, call = call_fixture(data, observed=observed)
    with stack, pytest.raises(DomainError):
        call()
    session.add.assert_not_called()
    session.commit.assert_not_called()


def test_placement_wire_rejects_duplicate_keys_and_payload_digest_drift():
    data = fixture()
    raw = data[5]
    with pytest.raises(ValueError):
        parse_placement_wire(raw[:-1] + b',"process_ticket":"' + b"a" * 64 + b'"}')
    altered = raw.replace(b'"worker_start_ticks":100', b'"worker_start_ticks":101')
    with pytest.raises(ValueError):
        parse_placement_wire(altered)
