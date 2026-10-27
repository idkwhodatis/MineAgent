"""Real ORM projection/codec/journal validation; DB and peer authority are doubles.

These do not authenticate a kernel peer or claim PostgreSQL/Docker acceptance.
"""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from test_rdagent_contracts import research_dict
from test_rdagent_controller_process import records

from backend.app import rdagent_protected_controller as service
from backend.app.research_process_models import (
    ResearchProcessAdmission,
    ResearchProcessReceipt,
    ResearchProcessState,
)
from packages.factors.contracts import canonical_bytes
from packages.rdagent_adapter.contracts import FactorResearchConfig
from packages.rdagent_adapter.operational_bootstrap import RDOperationalBootstrap
from packages.research_runtime.process_contracts import ProcessError, digest
from packages.research_runtime.process_persistence_codec import encode_row
from runtimes.rdagent_operational.peer import TrustedControllerBinding


@pytest.fixture
def resolver(monkeypatch, tmp_path):
    physical, identity, child, _ = records(tmp_path)
    admission = physical.intent.admission
    scope = {
        k: getattr(admission, k)
        for k in ("owner_id", "run_id", "attempt_id", "generation")
    }
    fence = SimpleNamespace(**scope)
    config = FactorResearchConfig.model_validate(research_dict())
    bootstrap = RDOperationalBootstrap(
        **scope,
        spec_id=uuid4(),
        attempt_binding_id=admission.logical_operation_id,
        config=config,
        config_sha256=config.sha256,
        input_sha256=config.input.sha256,
    )
    admission = admission.model_copy(update={"input_sha256": bootstrap.sha256})
    intent = physical.intent.model_copy(
        update={
            "admission": admission,
            "bootstrap_sha256": bootstrap.sha256,
            "bootstrap_size_bytes": len(canonical_bytes(bootstrap)),
        }
    )
    physical = physical.model_copy(update={"intent": intent})
    identity = identity.model_copy(
        update={
            "admission_sha256": digest(admission.model_dump(mode="json")),
            "launch_sha256": digest(physical.model_dump(mode="json")),
        }
    )
    child = child.model_copy(
        update={
            "identity_sha256": digest(identity.model_dump(mode="json")),
            "admission_sha256": identity.admission_sha256,
        }
    )
    binding = SimpleNamespace(id=admission.logical_operation_id)
    launch = SimpleNamespace(
        attempt_binding_id=binding.id,
        admission_id=admission.id,
        bootstrap_sha256=bootstrap.sha256,
        bootstrap=bootstrap.model_dump(mode="json"),
    )
    # The real ORM object is deliberately NOT a Mapping: catches decode_row(row).
    row = ResearchProcessAdmission(**encode_row(admission))
    state = ResearchProcessState(admission_id=admission.id, status="running")
    receipts = [
        ResearchProcessReceipt(
            id=uuid4(),
            **scope,
            admission_id=admission.id,
            kind=kind,
            payload=value.model_dump(mode="json"),
            payload_sha256=digest(value.model_dump(mode="json")),
        )
        for kind, value in (
            ("launch_intent", intent),
            ("launch_identity", identity),
            ("controller_child", child),
        )
    ]
    peer = SimpleNamespace(
        admission=admission,
        binding=TrustedControllerBinding(
            identity.admission_sha256,
            identity.leader,
            child.controller,
        ),
    )
    session = Mock()
    session.scalar.return_value = launch
    session.scalars.side_effect = lambda _: receipts

    def get(model, key, *, with_for_update):
        assert with_for_update is True and key == admission.id
        return row if model is ResearchProcessAdmission else state

    session.get.side_effect = get
    transaction = Mock()
    authenticate = Mock(return_value=peer)
    monkeypatch.setattr(service, "require_neutral_transaction", transaction)
    monkeypatch.setattr(
        service,
        "expected_operational_bootstrap",
        Mock(return_value=(binding, bootstrap)),
    )
    monkeypatch.setattr(service, "require_controller_peer", authenticate)
    return SimpleNamespace(
        session=session,
        fence=fence,
        row=row,
        state=state,
        receipts=receipts,
        peer=peer,
        launch=launch,
        transaction=transaction,
        authenticate=authenticate,
        authorization=object(),
        binding=binding,
    )


def invoke(value):
    return service.require_protected_controller(
        value.session, value.fence, value.authorization
    )


def test_exact_orm_codec_receipts_and_current_authority(resolver):
    assert invoke(resolver) is resolver.launch
    resolver.transaction.assert_called_once_with(resolver.session, resolver.fence)
    resolver.authenticate.assert_called_once_with(
        resolver.authorization,
        resolver.fence,
        logical_operation_id=resolver.binding.id,
    )
    assert invoke(resolver) is resolver.launch
    assert resolver.authenticate.call_count == 2  # No cached permission across events.


@pytest.mark.parametrize(
    "key",
    [
        "owner_id",
        "run_id",
        "attempt_id",
        "generation",
        "logical_operation_id",
        "input_sha256",
        "image_id",
    ],
)
def test_admission_scope_or_commitment_drift(resolver, key):
    value = (
        2
        if key == "generation"
        else (
            "e" * 64
            if key == "input_sha256"
            else ("sha256:" + "e" * 64 if key == "image_id" else uuid4())
        )
    )
    setattr(resolver.row, key, value)
    with pytest.raises(ProcessError, match="rd_controller_launch"):
        invoke(resolver)
    resolver.authenticate.assert_not_called()


@pytest.mark.parametrize("key", ["owner_id", "run_id", "attempt_id", "generation"])
def test_receipt_scope_drift(resolver, key):
    setattr(resolver.receipts[2], key, 2 if key == "generation" else uuid4())
    with pytest.raises(ProcessError, match="rd_controller_receipts"):
        invoke(resolver)


def test_changed_receipt_payload_hash(resolver):
    resolver.receipts[2].payload["peer_pid"] += 1
    with pytest.raises(ProcessError, match="rd_controller_receipts"):
        invoke(resolver)


@pytest.mark.parametrize("change", ["missing_child", "duplicate_kind", "extra_stop"])
def test_exact_three_receipts_required(resolver, change):
    if change == "missing_child":
        resolver.receipts.pop()
    elif change == "duplicate_kind":
        resolver.receipts[2].kind = "launch_identity"
    else:
        resolver.receipts.append(SimpleNamespace(kind="stop"))
    with pytest.raises(ProcessError, match="rd_controller_receipts"):
        invoke(resolver)
    resolver.authenticate.assert_not_called()


@pytest.mark.parametrize("status", ["stopped", "uncertain", "launch_intended"])
def test_not_running_rejected(resolver, status):
    resolver.state.status = status
    with pytest.raises(ProcessError, match="rd_controller_not_running"):
        invoke(resolver)
    resolver.authenticate.assert_not_called()


def test_rehashed_identity_chain_drift(resolver):
    receipt = resolver.receipts[2]
    receipt.payload["identity_sha256"] = "f" * 64
    receipt.payload_sha256 = digest(receipt.payload)
    with pytest.raises(ProcessError):
        invoke(resolver)
    resolver.authenticate.assert_not_called()


@pytest.mark.parametrize("change", ["child", "leader", "admission"])
def test_current_peer_binding_must_equal_persisted_receipts(resolver, change):
    if change == "admission":
        resolver.peer.admission = resolver.peer.admission.model_copy(
            update={"id": uuid4()}
        )
    else:
        binding = resolver.peer.binding
        field = "controller" if change == "child" else "leader"
        kernel = getattr(binding, field)
        resolver.peer.binding = replace(
            binding, **{field: replace(kernel, start_ticks=kernel.start_ticks + 1)}
        )
    with pytest.raises(ProcessError, match="rd_controller_peer_drift"):
        invoke(resolver)


def test_current_peer_revalidation_failure_propagates(resolver):
    resolver.authenticate.side_effect = ProcessError("peer_no_longer_live")
    with pytest.raises(ProcessError, match="peer_no_longer_live"):
        invoke(resolver)


def test_transaction_failure_precedes_reads(resolver):
    resolver.transaction.side_effect = ProcessError("fence_stale")
    with pytest.raises(ProcessError, match="fence_stale"):
        invoke(resolver)
    resolver.session.scalar.assert_not_called()
    resolver.authenticate.assert_not_called()


def test_broker_fixed_guard_rechecks_original_binding_each_boundary(
    resolver, monkeypatch
):
    from backend.app import rdagent_operational_broker as broker
    from backend.app.rdagent_operational_broker import _authorize

    monkeypatch.setattr(broker, "require_research_spec", Mock())
    from runtimes.rdagent_operational.peer import ControllerPeer

    # Concrete peer shape; kernel observation remains the explicit fixture double.
    peer = ControllerPeer(
        resolver.peer.admission, resolver.peer.binding, object(), object()
    )
    for _ in range(4):
        _authorize(peer, resolver.session, resolver.fence)
    assert resolver.authenticate.call_count == 4
    assert resolver.transaction.call_count == 4
    assert service.expected_operational_bootstrap.call_count == 4


@pytest.mark.parametrize("change", ["revoked", "owner", "generation", "peer"])
def test_broker_fixed_guard_rejects_drift_after_prior_success(
    resolver, change, monkeypatch
):
    from backend.app import rdagent_operational_broker as broker
    from backend.app.rdagent_operational_broker import _authorize

    monkeypatch.setattr(broker, "require_research_spec", Mock())
    from runtimes.rdagent_operational.peer import ControllerPeer

    peer = ControllerPeer(
        resolver.peer.admission, resolver.peer.binding, object(), object()
    )
    _authorize(peer, resolver.session, resolver.fence)
    if change == "revoked":
        resolver.state.status = "stopped"
    elif change == "owner":
        resolver.row.owner_id = uuid4()
    elif change == "generation":
        resolver.row.generation += 1
    else:
        resolver.peer.admission = resolver.peer.admission.model_copy(
            update={"id": uuid4()}
        )
    with pytest.raises(ProcessError):
        _authorize(peer, resolver.session, resolver.fence)
