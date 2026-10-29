"""Actual retained journal/native guard with explicit database doubles, not PG."""

import hashlib
from types import SimpleNamespace as NS
from unittest.mock import Mock
from uuid import uuid4

import pytest

from backend.app import oe_structured_controller_recovery as m
from backend.app.factor_native_io import NativeStorageError, native_storage_guard
from backend.app.models import ResearchRun
from backend.app.oe_structured_operational_models import OEStructuredEpoch
from backend.app.research_process_models import (
    ResearchProcessAdmission,
    ResearchProcessState,
)
from packages.research_runtime.process_contracts import ProcessError, digest
from packages.research_runtime.process_persistence_codec import encode_row
from tests.structured_controller_runtime.fixtures import records
from tests.structured_controller_runtime.test_recovery import write


@pytest.fixture
def case(tmp_path, monkeypatch):
    (tmp_path / "factors").mkdir(mode=0o700)
    values = records(tmp_path)
    launch, identity, child, stop = values
    raw_bootstrap = '{"actual":"retained bootstrap fixture"}'
    bootstrap_sha = hashlib.sha256(raw_bootstrap.encode()).hexdigest()
    original = launch.intent
    inventory = {
        "schema_version": "mineagent.oe-structured-bootstrap-inventory.v1",
        "files": [
            {
                "destination": "/bootstrap/bootstrap.json",
                "sha256": bootstrap_sha,
                "size_bytes": len(raw_bootstrap),
            },
            {
                "destination": "/bootstrap/seed.json",
                "sha256": original.seed_sha256,
                "size_bytes": original.seed_size_bytes,
            },
        ],
    }
    admission = original.admission.model_copy(
        update={"input_sha256": digest(inventory)}
    )
    intent = original.model_copy(
        update={
            "admission": admission,
            "bootstrap_sha256": bootstrap_sha,
            "bootstrap_size_bytes": len(raw_bootstrap),
        }
    )
    launch = launch.model_copy(update={"intent": intent})
    identity = identity.model_copy(
        update={
            "admission_sha256": digest(admission.model_dump(mode="json")),
            "launch_sha256": digest(launch.model_dump(mode="json")),
        }
    )
    child = child.model_copy(
        update={
            "admission_sha256": identity.admission_sha256,
            "identity_sha256": digest(identity.model_dump(mode="json")),
        }
    )
    stop = stop.model_copy(
        update={
            "launch_sha256": identity.launch_sha256,
            "identity_sha256": child.identity_sha256,
            "child_sha256": digest(child.model_dump(mode="json")),
        }
    )
    values = (launch, identity, child, stop)
    admission = launch.intent.admission
    write(tmp_path, values)
    scope = {
        k: getattr(admission, k)
        for k in ("owner_id", "run_id", "attempt_id", "generation")
    }
    row = NS(**encode_row(admission))
    state = NS(**scope, status="uncertain", updated_at=None)
    run = NS(id=admission.run_id, owner_id=admission.owner_id)
    attempt = NS(id=admission.attempt_id, **scope)
    epoch = NS(
        bootstrap_canonical=raw_bootstrap,
        bootstrap_sha256=bootstrap_sha,
        id=admission.logical_operation_id,
        controller_admission_id=admission.id,
        **scope,
    )
    receipts = [
        NS(
            kind="launch_intent",
            payload=launch.intent.model_dump(mode="json"),
            payload_sha256=digest(launch.intent.model_dump(mode="json")),
            **scope,
        )
    ]
    config = NS(runtime=NS(controller_image_id=admission.image_id))
    monkeypatch.setattr(
        "backend.app.oe_structured_identity.decode_spec",
        lambda session, value: (None, config),
    )
    monkeypatch.setattr(m, "_try_neutral_lock", lambda *args: None)

    class Session:
        def __init__(self):
            self.revision = ["028_oe_structured_search"]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        commit = Mock()

        def get(self, cls, key, **kw):
            return {
                ResearchProcessAdmission: row,
                ResearchProcessState: state,
                OEStructuredEpoch: epoch,
                ResearchRun: run,
            }.get(cls)

        def scalar(self, query):
            return run if "FROM research_runs" in str(query) else attempt

        def scalars(self, query):
            return NS(
                all=lambda: (
                    self.revision if "alembic_version" in str(query) else receipts
                )
            )

        flush = Mock()

    session = Session()
    added = []

    def receipt(session, admission, kind, payload):
        for old in receipts:
            if old.kind == kind:
                if old.payload != payload:
                    raise ProcessError("receipt_drift")
                return old
        value = NS(kind=kind, payload=payload, payload_sha256=digest(payload), **scope)
        receipts.append(value)
        added.append(kind)
        return value

    monkeypatch.setattr(m, "_receipt", receipt)
    return NS(
        root=tmp_path,
        values=values,
        admission=admission,
        session=session,
        row=row,
        state=state,
        run=run,
        attempt=attempt,
        epoch=epoch,
        receipts=receipts,
        config=config,
        added=added,
    )


def call(c):
    with native_storage_guard(c.root):
        return m.record_controller_containment(
            c.session, c.admission.id, storage_root=c.root
        )


def test_stale_unit_actual_stop_is_recorded_without_new_completion_or_charge(case):
    assert call(case) == case.values[3]
    assert case.state.status == "stopped"
    assert case.added == ["launch_identity", "controller_child", "stop"]
    assert call(case) == case.values[3]
    assert case.added == ["launch_identity", "controller_child", "stop"]
    assert not (
        case.root
        / "research-processes"
        / str(case.admission.owner_id)
        / str(case.admission.id)
        / "control"
        / "completion.json"
    ).exists()


def test_missing_native_guard_rejected_before_sql(case):
    with pytest.raises(NativeStorageError):
        m.record_controller_containment(
            case.session, case.admission.id, storage_root=case.root
        )
    assert not case.added


@pytest.mark.parametrize(
    "fault",
    [
        "revision",
        "epoch",
        "image",
        "state_scope",
        "receipt_hash",
        "intent",
        "missing_stop",
        "bootstrap",
    ],
)
def test_drift_has_no_terminal_state_promotion(case, fault):
    c = case
    if fault == "revision":
        c.session.revision = ["026_rd_seeded_submissions"]
    elif fault == "epoch":
        c.epoch.controller_admission_id = uuid4()
    elif fault == "image":
        c.config.runtime.controller_image_id = "sha256:" + "f" * 64
    elif fault == "state_scope":
        c.state.owner_id = uuid4()
    elif fault == "receipt_hash":
        c.receipts[0].payload_sha256 = "f" * 64
    elif fault == "intent":
        c.receipts[0].payload = dict(c.receipts[0].payload, seed_sha256="e" * 64)
        c.receipts[0].payload_sha256 = digest(c.receipts[0].payload)
    elif fault == "bootstrap":
        c.epoch.bootstrap_canonical += " "
    else:
        (
            c.root
            / "research-processes"
            / str(c.admission.owner_id)
            / str(c.admission.id)
            / "control"
            / "stop.json"
        ).unlink()
    with pytest.raises((ProcessError, ValueError, FileNotFoundError)):
        call(c)
    assert c.state.status == "uncertain"


def test_worker_entry_import_and_exact_public_api():
    import inspect

    from workers.research.oe_structured_controller_recovery import (
        recover_controller_unit,
    )

    assert tuple(inspect.signature(recover_controller_unit).parameters) == (
        "factory",
        "settings",
        "admission_id",
    )


def test_worker_uses_immutable_runtime_even_if_model_disabled_and_commits_only_stop(
    case, monkeypatch
):
    from contextlib import nullcontext

    from workers.research import oe_structured_controller_recovery as worker

    c = case

    class Factory:
        def __init__(self):
            self.kw = {"bind": object()}

        def __call__(self):
            return c.session

    monkeypatch.setattr(worker, "shared_lifecycle", lambda *args: nullcontext())
    monkeypatch.setattr(worker, "decode_spec", lambda session, run: (None, c.config))
    inventory = Mock(return_value={"actual": "inventory"})
    monkeypatch.setattr(worker, "image_inventory", inventory)
    supervisor = Mock()
    monkeypatch.setattr(worker, "ControllerSupervisor", supervisor)
    recover = Mock()
    monkeypatch.setattr(worker, "recover_controller", recover)
    settings = NS(storage_root=c.root, oe_structured_enabled=False)
    assert worker.recover_controller_unit(Factory(), settings, c.admission.id) is True
    inventory.assert_called_once_with(c.config.runtime)
    recover.assert_called_once_with(supervisor.return_value, c.root, c.admission)
    c.session.commit.assert_called_once()
    assert c.state.status == "stopped" and "controller_completion" not in c.added


def test_all_recovery_database_locks_are_nonblocking(case, monkeypatch):
    c = case
    locks = []
    monkeypatch.setattr(
        m, "_try_neutral_lock", lambda session, scope: locks.append(scope)
    )
    original_get, original_scalar = c.session.get, c.session.scalar
    row_locks = []

    def get(cls, key, **kwargs):
        if "with_for_update" in kwargs:
            row_locks.append(kwargs["with_for_update"])
        return original_get(cls, key, **kwargs)

    def scalar(query):
        assert query._for_update_arg.nowait is True
        return original_scalar(query)

    c.session.get, c.session.scalar = get, scalar
    call(c)
    assert locks == [f"submit:{c.admission.owner_id}"]
    assert row_locks == [{"nowait": True}, {"nowait": True}]
