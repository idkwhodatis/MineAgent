"""Real persisted codecs and journal bytes, with transaction/kernel authority doubles."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from test_rdagent_controller_process import records, write

from backend.app import rdagent_operational_admission as module
from backend.app import research_lifecycle, research_process_services
from backend.app.research_process_models import (
    ResearchProcessAdmission,
    ResearchProcessReceipt,
    ResearchProcessState,
)
from packages.research_runtime.process_contracts import ProcessError, digest
from packages.research_runtime.process_persistence_codec import encode_row


@pytest.fixture
def claim(tmp_path, monkeypatch):
    values = records(tmp_path)
    write(tmp_path, values)
    launch, identity, _, _ = values
    admission = launch.intent.admission
    scope = {
        k: getattr(admission, k)
        for k in ("owner_id", "run_id", "attempt_id", "generation")
    }
    fence = SimpleNamespace(**scope)
    row = ResearchProcessAdmission(**encode_row(admission))
    state = ResearchProcessState(
        admission_id=admission.id, **scope, status="launch_intended"
    )
    receipts = [
        ResearchProcessReceipt(
            id=uuid4(),
            admission_id=admission.id,
            **scope,
            kind="launch_intent",
            payload=launch.intent.model_dump(mode="json"),
            payload_sha256=digest(launch.intent.model_dump(mode="json")),
        )
    ]
    session = Mock()

    def get(model, key, *, with_for_update):
        assert key == admission.id and with_for_update is True
        return row if model is ResearchProcessAdmission else state

    session.get.side_effect = get
    session.scalars.return_value.all.side_effect = lambda: receipts
    check = Mock()
    monkeypatch.setattr(research_lifecycle, "require_neutral_transaction", check)
    monkeypatch.setattr(
        module, "utcnow", lambda: launch.intent.issued_at + timedelta(seconds=1)
    )
    observe = Mock(return_value=identity.leader)
    monkeypatch.setattr(
        module, "DaemonIdentityContext", lambda *args: SimpleNamespace(observe=observe)
    )

    def receipt(_session, _admission, kind, payload):
        old = next((r for r in receipts if r.kind == kind), None)
        if old is not None:
            assert old.payload == payload
            return old
        result = ResearchProcessReceipt(
            id=uuid4(),
            admission_id=admission.id,
            **scope,
            kind=kind,
            payload=payload,
            payload_sha256=digest(payload),
        )
        receipts.append(result)
        return result

    monkeypatch.setattr(research_process_services, "_receipt", receipt)
    return SimpleNamespace(
        session=session,
        fence=fence,
        admission=admission,
        row=row,
        state=state,
        receipts=receipts,
        intent=launch.intent,
        identity=identity,
        root=tmp_path,
        check=check,
        observe=observe,
    )


def issue(c):
    return module.claim_controller_launch(c.session, c.fence, c.admission.id)


def confirm(c, expected=None):
    return module.check_controller_claim(
        c.session,
        c.fence,
        c.admission.id,
        expected_intent=c.intent if expected is None else expected,
    )


def identify(c):
    return module.record_claimed_controller_identity(
        c.session, c.fence, c.admission.id, storage_root=c.root
    )


def test_one_shot_claim_fresh_check_actual_journal_identity(claim):
    assert issue(claim) == claim.intent
    assert claim.state.status == "uncertain"
    claim.session.flush.assert_called_once()
    assert confirm(claim) == claim.intent
    assert identify(claim) == claim.admission
    claim.observe.assert_called_once_with(claim.identity.leader.host_pid)
    assert claim.state.status == "running"
    assert {r.kind for r in claim.receipts} == {"launch_intent", "launch_identity"}
    assert all(
        call.kwargs == {"resource_scope": "processes"}
        for call in claim.check.call_args_list
    )
    assert (
        claim.check.call_count == 4
    )  # Claim, fresh check, identity check and ingestion.


def test_repeat_claim_never_reissues(claim):
    issue(claim)
    with pytest.raises(ProcessError, match="already_claimed"):
        issue(claim)
    assert claim.session.flush.call_count == 1


@pytest.mark.parametrize("state", ["running", "stopped", "uncertain"])
def test_noninitial_claim_refused(claim, state):
    claim.state.status = state
    with pytest.raises(ProcessError):
        issue(claim)
    claim.session.flush.assert_not_called()


@pytest.mark.parametrize("offset", [-1, 60, 61])
def test_expired_or_not_yet_issued_claim(claim, monkeypatch, offset):
    monkeypatch.setattr(
        module, "utcnow", lambda: claim.intent.issued_at + timedelta(seconds=offset)
    )
    with pytest.raises(ProcessError, match="expired"):
        issue(claim)
    assert claim.state.status == "launch_intended"


def test_claim_check_expiry_and_exact_expected(claim, monkeypatch):
    issue(claim)
    with pytest.raises(ProcessError, match="changed"):
        confirm(claim, claim.intent.model_copy(update={"id": uuid4()}))
    monkeypatch.setattr(module, "utcnow", lambda: claim.intent.not_after)
    with pytest.raises(ProcessError, match="expired"):
        confirm(claim)


@pytest.mark.parametrize("action", [issue, confirm, identify])
def test_stale_fence_precedes_reads(claim, action):
    claim.check.side_effect = ProcessError("stale_fence")
    with pytest.raises(ProcessError, match="stale_fence"):
        action(claim)
    claim.session.get.assert_not_called()
    claim.observe.assert_not_called()


@pytest.mark.parametrize("action", [issue, confirm, identify])
def test_foreign_scope_rejected(claim, action):
    claim.row.owner_id = uuid4()
    with pytest.raises(ProcessError, match="scope"):
        action(claim)
    claim.observe.assert_not_called()


def test_identity_requires_claim(claim):
    with pytest.raises(ProcessError, match="changed"):
        identify(claim)
    claim.observe.assert_not_called()


@pytest.mark.parametrize("failure", ["mismatch", "gone"])
def test_actual_kernel_observation_required(claim, failure):
    issue(claim)
    if failure == "mismatch":
        claim.observe.return_value = replace(claim.identity.leader, start_ticks=99999)
    else:
        claim.observe.side_effect = ProcessError("process_gone")
    with pytest.raises(ProcessError):
        identify(claim)
    assert claim.state.status == "uncertain"
    assert len(claim.receipts) == 1


def test_existing_identity_replay_never_revives_uncertain(claim):
    issue(claim)
    identify(claim)
    claim.state.status = "uncertain"
    claim.observe.reset_mock()
    assert identify(claim) == claim.admission
    assert claim.state.status == "uncertain"
    claim.observe.assert_not_called()


def test_corrupt_receipt_hash_rejected(claim):
    claim.receipts[0].payload_sha256 = "f" * 64
    with pytest.raises(ProcessError, match="receipt"):
        issue(claim)


def test_missing_intent_rejected(claim):
    claim.receipts.clear()
    with pytest.raises(ProcessError, match="intent"):
        issue(claim)
