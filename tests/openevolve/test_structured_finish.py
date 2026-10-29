"""Terminal transaction doubles; no PG/kernel acceptance claim."""

from contextlib import contextmanager, nullcontext
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import research_lifecycle as lifecycle
from backend.app.services import DomainError
from packages.research_runtime.budget_contracts import BudgetVector
from workers.research import oe_structured_finish as finish


def arranged(monkeypatch):
    scope = dict(
        zip(
            ("owner_id", "run_id", "attempt_id", "generation"),
            (uuid4(), uuid4(), uuid4(), 1),
        )
    )
    fence = NS(**scope)
    epoch = NS(id=uuid4(), controller_admission_id=uuid4(), **scope)
    final = NS(id=uuid4(), epoch_id=epoch.id)
    outcome = NS(admission_id=uuid4())
    admissions = [NS(id=epoch.controller_admission_id), NS(id=outcome.admission_id)]
    states = [NS(admission_id=a.id, status="stopped") for a in admissions]
    vector = BudgetVector(process_slots=3)
    budget = NS(
        limits=vector.model_dump(),
        used=BudgetVector(process_slots=2).model_dump(),
        reserved=BudgetVector().model_dump(),
    )
    run, attempt = (
        NS(id=scope["run_id"], status="running"),
        NS(id=scope["attempt_id"], status="running"),
    )
    calls = []
    data = NS(
        outcomes=[outcome],
        admissions=admissions,
        states=states,
        budget=budget,
        reject=False,
    )

    class Session:
        def __init__(self):
            self.info = {lifecycle._NEUTRAL_KEY: {}}

        def get(self, *a):
            return epoch

        def scalars(self, q):
            if not hasattr(q, "column_descriptions"):
                return NS(all=lambda: ["028_oe_structured_search"])
            name = q.column_descriptions[0]["entity"].__name__
            return {
                "OEStructuredEngineOutcome": data.outcomes,
                "ResearchProcessAdmission": data.admissions,
                "ResearchProcessState": data.states,
            }[name]

        def scalar(self, q):
            return data.budget

        def flush(self):
            calls.append("flush")

    session = Session()

    @contextmanager
    def txn(*a, **kw):
        yield session
        calls.append("commit")

    def complete(*a):
        calls.append("physical_complete")
        if data.reject:
            raise ValueError("missing durable completion")
        return final

    monkeypatch.setattr(finish, "shared_lifecycle", lambda *a: nullcontext())
    monkeypatch.setattr(finish, "native_storage_guard", lambda *a: nullcontext())
    monkeypatch.setattr(finish, "neutral_transaction", txn)
    monkeypatch.setattr(
        finish, "require_neutral_transaction", lambda *a, **kw: (run, attempt)
    )
    monkeypatch.setattr(
        finish,
        "decode_spec",
        lambda *a: (None, NS(search=NS(maximum_mutation_slots=2), budget=vector)),
    )
    monkeypatch.setattr(finish, "complete_stopped_epoch", complete)
    monkeypatch.setattr(
        lifecycle, "require_neutral_transaction", lambda *a, **kw: (run, attempt)
    )
    invoke = lambda: finish.finish_structured_run(
        NS(kw={"bind": object()}), fence, epoch.id, NS(storage_root="unused")
    )
    return data, run, attempt, calls, invoke, final


def test_success_only_after_complete_and_commit(monkeypatch):
    _, run, attempt, calls, invoke, final = arranged(monkeypatch)
    assert invoke() == final.id
    assert calls == ["physical_complete", "commit"]
    assert run.status == attempt.status == "succeeded"
    assert attempt.sandbox_stopped is True and attempt.exit_code is None


@pytest.mark.parametrize(
    "defect",
    [
        "completion",
        "extra_unit",
        "running",
        "missing_outcome",
        "held",
        "overspent",
        "missing_dimension",
        "boolean",
    ],
)
def test_incomplete_obligations_never_terminal(monkeypatch, defect):
    data, run, attempt, calls, invoke, _ = arranged(monkeypatch)
    if defect == "completion":
        data.reject = True
    elif defect == "extra_unit":
        data.admissions.append(NS(id=uuid4()))
    elif defect == "running":
        data.states[0].status = "running"
    elif defect == "missing_outcome":
        data.outcomes.clear()
    elif defect == "held":
        data.budget.reserved["calls"] = 1
    elif defect == "overspent":
        data.budget.used["process_slots"] = 4
    elif defect == "missing_dimension":
        del data.budget.reserved["cost_microunits"]
    elif defect == "boolean":
        data.budget.limits["calls"] = False
    with pytest.raises((DomainError, ValueError)):
        invoke()
    assert run.status == attempt.status == "running"
    assert "commit" not in calls


@pytest.mark.parametrize("mode", ["success", "duplicate", "issuer_failure"])
def test_task_consumer_never_finalizes_failed_or_duplicate(monkeypatch, tmp_path, mode):
    from backend.app.core import atomic_storage, db
    from workers.research import oe_structured_controller, oe_structured_queue

    calls = []
    engine = NS(dispose=lambda: calls.append("dispose"))
    factory, fence, epoch = object(), object(), uuid4()
    monkeypatch.setattr(db, "create_db_engine", lambda *a: engine)
    monkeypatch.setattr(db, "session_factory", lambda *a: factory)
    monkeypatch.setattr(atomic_storage, "provision_directory", lambda *a, **kw: None)
    monkeypatch.setattr(
        oe_structured_queue, "shared_lifecycle", lambda *a: nullcontext()
    )
    monkeypatch.setattr(
        oe_structured_queue,
        "claim_structured_run",
        lambda *a: None if mode == "duplicate" else fence,
    )

    def issue(*args):
        assert args[:2] == (factory, fence)
        calls.append("issuer")
        if mode == "issuer_failure":
            raise ValueError("unconfirmed completion")
        return epoch

    def terminal(*args):
        assert args[:3] == (factory, fence, epoch)
        calls.append("terminal")
        return "committed"

    monkeypatch.setattr(oe_structured_controller, "execute_controller_epoch", issue)
    monkeypatch.setattr(finish, "finish_structured_run", terminal)
    invoke = lambda: oe_structured_queue.execute(
        str(uuid4()), 1, settings=NS(storage_root=tmp_path)
    )
    if mode == "issuer_failure":
        with pytest.raises(ValueError, match="unconfirmed"):
            invoke()
        assert calls == ["issuer", "dispose"]
    elif mode == "duplicate":
        assert invoke() is None
        assert calls == ["dispose"]
    else:
        assert invoke() == "committed"
        assert calls == ["issuer", "terminal", "dispose"]
