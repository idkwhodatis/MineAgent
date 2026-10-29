"""Actual typed journals + DB/kernel doubles; no PG/Docker or live-peer claim."""

from types import SimpleNamespace as NS

import pytest

from backend.app import oe_structured_controller_process_services as service
from backend.app.factor_native_io import native_storage_guard
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.process_contracts import ProcessError, digest
from runtimes.research_supervisor.supervisor import operation_path, write_record
from tests.structured_controller_runtime.fixtures import records


def fixture(monkeypatch, root, state="launch_intended"):
    launch, identity, child, stop = records(root)
    admission, intent = launch.intent.admission, launch.intent
    fence = NS(**{key: getattr(admission, key) for key in service.SCOPE})
    projection = NS(status=state, updated_at=None)
    receipts = {"launch_intent": NS(payload=intent.model_dump(mode="json"))}
    session = NS(flush=lambda: None)
    calls = []
    monkeypatch.setattr(
        service, "_persisted", lambda *args: (admission, projection, receipts, intent)
    )
    monkeypatch.setattr(service, "_expected", lambda *args: intent)
    monkeypatch.setattr(service, "_held", lambda *args: None)
    monkeypatch.setattr(service, "_current", lambda *args: calls.append("fence"))

    def receipt(session, declaration, kind, payload):
        if kind in receipts:
            assert receipts[kind].payload == payload
        else:
            receipts[kind] = NS(payload=payload, payload_sha256=digest(payload))

    monkeypatch.setattr(service, "_receipt", receipt)
    (root / "factors").mkdir()
    path = operation_path(root, admission) / "control"
    path.mkdir(parents=True)
    for name, value in (
        ("launch", launch),
        ("identity", identity),
        ("child", child),
        ("stop", stop),
    ):
        write_record(path / (name + ".json"), value.model_dump(mode="json"))
    monkeypatch.setattr(
        service,
        "DaemonIdentityContext",
        lambda *args: NS(observe=lambda pid: identity.leader),
    )
    return session, fence, admission, projection, receipts, intent, path, calls


def test_claim_once_and_fresh_check_never_reclaims_uncertain(monkeypatch, tmp_path):
    session, fence, admission, state, _, intent, _, _ = fixture(monkeypatch, tmp_path)
    assert service.claim_controller_intent(session, fence, admission.id, NS()) == intent
    assert state.status == "uncertain"
    assert (
        service.check_controller_claim(
            session, fence, admission.id, NS(), expected_intent=intent
        )
        == intent
    )
    with pytest.raises(ProcessError, match="already_claimed"):
        service.claim_controller_intent(session, fence, admission.id, NS())


@pytest.mark.parametrize("state", ["running", "stopped", "uncertain", "unknown"])
def test_noninitial_claim_fails(monkeypatch, tmp_path, state):
    session, fence, admission, _, _, _, _, _ = fixture(monkeypatch, tmp_path, state)
    with pytest.raises(ProcessError):
        service.claim_controller_intent(session, fence, admission.id, NS())


def test_actual_journal_identity_child_stop_and_full_charge(monkeypatch, tmp_path):
    session, fence, admission, state, receipts, _, _, calls = fixture(
        monkeypatch, tmp_path, "uncertain"
    )
    amounts = BudgetVector(
        process_slots=1, wall_milliseconds=600000, storage_bytes=service.STORAGE_FLOOR
    )
    held = NS(
        id=admission.reservation_id,
        **vars(fence),
        amounts=amounts.model_dump(),
        purpose="process_unit",
        state="reserved",
        logical_key=f"oe-structured-controller:{admission.logical_operation_id}",
    )
    session.get = lambda *args, **kwargs: held
    session.scalar = lambda *args: None
    charged = []
    monkeypatch.setattr(
        service, "settle_budget", lambda *args, **kwargs: charged.append(kwargs)
    )
    with native_storage_guard(tmp_path):
        service.record_controller_identity(
            session, fence, admission.id, storage_root=tmp_path
        )
        assert state.status == "running"
        service.record_controller_child(
            session, fence, admission.id, storage_root=tmp_path
        )
        service.settle_stopped_controller(
            session, fence, admission.id, storage_root=tmp_path
        )
    assert state.status == "stopped" and set(receipts) == service.KINDS - {
        "controller_completion"
    }
    assert len(calls) == 3
    assert charged == [{"charge": amounts, "outcome": "conservative_unknown"}]


@pytest.mark.parametrize(
    "mutation",
    [
        "not_claimed",
        "leader",
        "journal",
        "stale_fence",
        "missing_stop",
        "child_before_identity",
    ],
)
def test_no_fabricated_start_child_or_stop(monkeypatch, tmp_path, mutation):
    session, fence, admission, state, receipts, _, path, _ = fixture(
        monkeypatch, tmp_path, "uncertain"
    )
    action = service.record_controller_identity
    if mutation == "not_claimed":
        state.status = "launch_intended"
    elif mutation == "leader":
        monkeypatch.setattr(
            service, "DaemonIdentityContext", lambda *args: NS(observe=lambda pid: None)
        )
    elif mutation == "journal":
        (path / "identity.json").write_bytes(b"{}")
    elif mutation == "stale_fence":

        def stale(*args):
            raise ProcessError("stale_fence")

        monkeypatch.setattr(service, "_current", stale)
    elif mutation == "missing_stop":
        (path / "stop.json").unlink()
        action = service.record_controller_stopped
    else:
        action = service.record_controller_child
    with (
        native_storage_guard(tmp_path),
        pytest.raises((ProcessError, ValueError, OSError)),
    ):
        action(session, fence, admission.id, storage_root=tmp_path)
    assert set(receipts) == {"launch_intent"}
    assert state.status != "running"


@pytest.mark.parametrize("duplicate", [False, True])
def test_persist_is_flush_only_and_duplicate_is_not_a_grant(
    monkeypatch, tmp_path, duplicate
):
    session, fence, admission, _, _, intent, _, _ = fixture(monkeypatch, tmp_path)
    added = []
    session.add = added.append
    session.scalar = lambda statement: admission if duplicate else None
    if duplicate:
        with pytest.raises(ProcessError, match="already_admitted"):
            service.persist_controller_intent(session, fence, intent, NS())
        assert not added
    else:
        assert (
            service.persist_controller_intent(session, fence, intent, NS()) == admission
        )
        assert len(added) == 2
        assert added[1].status == "launch_intended"
        assert not hasattr(session, "commit")


def test_model_copy_cannot_change_intent_profile(monkeypatch, tmp_path):
    session, fence, admission, _, _, intent, _, _ = fixture(monkeypatch, tmp_path)
    corrupt = intent.model_copy(
        update={"admission": admission.model_copy(update={"profile": "legacy024"})}
    )
    with pytest.raises((ProcessError, ValueError)):
        service.persist_controller_intent(session, fence, corrupt, NS())


@pytest.mark.parametrize("mutation", [None, "refund", "scope", "missing", "state"])
def test_stopped_settlement_exact_replay_does_not_recharge(
    monkeypatch, tmp_path, mutation
):
    session, fence, admission, state, _, _, _, _ = fixture(
        monkeypatch, tmp_path, "uncertain"
    )
    amounts = BudgetVector(
        process_slots=1, wall_milliseconds=600000, storage_bytes=service.STORAGE_FLOOR
    ).model_dump()
    held = NS(
        id=admission.reservation_id,
        **vars(fence),
        purpose="process_unit",
        state="settled",
        amounts=amounts,
        logical_key=f"oe-structured-controller:{admission.logical_operation_id}",
    )
    prior = NS(**vars(fence), outcome="conservative_unknown", charge=amounts.copy())
    if mutation == "refund":
        prior.charge["storage_bytes"] -= 1
    elif mutation == "scope":
        from uuid import uuid4

        prior.owner_id = uuid4()
    elif mutation == "missing":
        prior = None
    elif mutation == "state":
        held.state = "reserved"
    session.get = lambda *args, **kwargs: held
    session.scalar = lambda statement: prior
    calls = []

    def settled(*args, **kwargs):
        calls.append(kwargs)
        return prior

    monkeypatch.setattr(service, "settle_budget", settled)
    with native_storage_guard(tmp_path):
        if mutation:
            with pytest.raises(ProcessError):
                service.settle_stopped_controller(
                    session, fence, admission.id, storage_root=tmp_path
                )
            assert not calls
        else:
            assert (
                service.settle_stopped_controller(
                    session, fence, admission.id, storage_root=tmp_path
                )
                is prior
            )
            assert (
                service.settle_stopped_controller(
                    session, fence, admission.id, storage_root=tmp_path
                )
                is prior
            )
            assert (
                calls
                == [
                    {
                        "charge": BudgetVector.model_validate(amounts),
                        "outcome": "conservative_unknown",
                    }
                ]
                * 2
            )
    assert state.status == "stopped"  # Truthful observed stop, not a fake refund.
