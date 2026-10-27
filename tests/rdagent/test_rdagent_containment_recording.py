"""Real journal/ORM codecs and DB transaction doubles; no PostgreSQL acceptance."""

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from test_rdagent_controller_process import records, write

from backend.app import rdagent_controller_recovery as module
from backend.app.research_process_models import (
    ResearchProcessAdmission,
    ResearchProcessReceipt,
    ResearchProcessState,
)
from packages.research_runtime.process_contracts import ProcessError, digest
from packages.research_runtime.process_persistence_codec import encode_row


@pytest.fixture
def recorder(tmp_path, monkeypatch):
    values = records(tmp_path)
    directory = write(tmp_path, values)
    launch, _, _, stop = values
    admission = launch.intent.admission
    scope = {
        k: getattr(admission, k)
        for k in ("owner_id", "run_id", "attempt_id", "generation")
    }
    row = ResearchProcessAdmission(**encode_row(admission))
    state = ResearchProcessState(admission_id=admission.id, **scope, status="uncertain")
    # A new generation is already active; recovery must not edit it or old attempt.
    run = SimpleNamespace(generation=2, active_attempt_id=uuid4(), status="running")
    old_attempt = SimpleNamespace(generation=1, status="failed", sandbox_stopped=False)
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
    session.scalar.side_effect = lambda statement: (
        old_attempt if "execution_attempts" in str(statement) else run
    )

    def get(model, key, **kwargs):
        assert key == admission.id
        return row if model is ResearchProcessAdmission else state

    session.get.side_effect = get
    session.scalars.return_value.all.side_effect = lambda: receipts
    order = []
    monkeypatch.setattr(
        module, "advisory_lock", lambda session, name: order.append(name)
    )

    def receipt(session, actual, kind, payload):
        assert actual == admission
        existing = next((r for r in receipts if r.kind == kind), None)
        if existing is not None:
            if existing.payload != payload:
                raise ProcessError("receipt_drift")
            return existing
        value = ResearchProcessReceipt(
            id=uuid4(),
            admission_id=admission.id,
            **scope,
            kind=kind,
            payload=payload,
            payload_sha256=digest(payload),
        )
        receipts.append(value)
        return value

    monkeypatch.setattr(module, "_receipt", receipt)
    return SimpleNamespace(
        root=tmp_path,
        directory=directory,
        values=values,
        admission=admission,
        state=state,
        row=row,
        run=run,
        attempt=old_attempt,
        receipts=receipts,
        session=session,
        order=order,
        stop=stop,
    )


def record(r):
    return module.record_controller_containment(
        r.session, r.admission.id, storage_root=r.root
    )


def test_old_fence_exact_containment_only_and_replay(recorder):
    r = recorder
    assert record(r) == r.stop
    assert r.state.status == "stopped"
    assert {x.kind for x in r.receipts} == {
        "launch_intent",
        "launch_identity",
        "controller_child",
        "stop",
    }
    assert record(r) == r.stop and len(r.receipts) == 4
    assert r.run.generation == 2 and r.run.status == "running"
    assert r.attempt.status == "failed" and r.attempt.sandbox_stopped is False
    assert r.order == [f"submit:{r.admission.owner_id}"] * 2
    r.session.add.assert_not_called()  # Only the exact receipt writer above is permitted.
    r.session.commit.assert_not_called()
    r.session.rollback.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        "scope",
        "state",
        "hash",
        "duplicate",
        "unknown_kind",
        "missing_intent",
        "missing_run",
        "missing_stop",
        "missing_child",
    ],
)
def test_bad_retained_closure_never_marks_stopped(recorder, change):
    r = recorder
    if change == "scope":
        r.state.owner_id = uuid4()
    elif change == "state":
        r.state.status = "invented"
    elif change == "hash":
        r.receipts[0].payload_sha256 = "e" * 64
    elif change == "duplicate":
        r.receipts.append(r.receipts[0])
    elif change == "unknown_kind":
        r.receipts[0].kind = "extraction"
    elif change == "missing_intent":
        r.receipts.clear()
    elif change == "missing_run":
        r.session.scalar.side_effect = lambda statement: None
    elif change == "missing_stop":
        (r.directory / "stop.json").unlink()
    else:
        (r.directory / "child.json").unlink()
    with pytest.raises((ProcessError, OSError, ValueError)):
        record(r)
    assert r.state.status != "stopped"
    r.session.flush.assert_not_called()
    r.session.commit.assert_not_called()


def test_bootstrap_failure_stop_never_invents_child(recorder):
    r = recorder
    _launch, _identity, _, stop = r.values
    stop = stop.model_copy(
        update={"child_sha256": None, "child_kernel_identity_absent": None}
    )
    from packages.research_runtime.process_contracts import canonical

    (r.directory / "child.json").unlink()
    (r.directory / "stop.json").write_bytes(canonical(stop.model_dump(mode="json")))
    assert record(r) == stop
    assert {x.kind for x in r.receipts} == {"launch_intent", "launch_identity", "stop"}


def test_database_failure_keeps_physical_stop_for_later_transaction(recorder):
    r = recorder
    r.session.flush.side_effect = RuntimeError("DB unavailable")
    with pytest.raises(RuntimeError, match="DB unavailable"):
        record(r)
    assert (r.directory / "stop.json").is_file()
    r.session.flush.side_effect = None
    assert record(r) == r.stop


def test_admission_changed_after_lock_refused(recorder):
    r = recorder
    changed = ResearchProcessAdmission(
        **{**encode_row(r.admission), "owner_id": uuid4()}
    )
    r.session.get.side_effect = lambda model, key, **kw: (
        changed if kw.get("with_for_update") else r.row
    )
    with pytest.raises(ProcessError, match="admission_changed"):
        record(r)
    r.session.flush.assert_not_called()


def test_foreign_receipt_scope_refused(recorder):
    r = recorder
    r.receipts[0].attempt_id = uuid4()
    with pytest.raises(ProcessError, match="receipt"):
        record(r)
    r.session.flush.assert_not_called()
