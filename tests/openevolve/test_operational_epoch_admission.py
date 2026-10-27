"""Real bootstrap/journal codecs with explicit persistence/budget doubles."""

import hashlib
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import openevolve_epoch_admission as module
from backend.app.openevolve_controller_services import typed_bootstrap
from tests.openevolve.test_operational_native_process import case as native_case


def setup(monkeypatch, tmp_path):
    base = native_case.__wrapped__(monkeypatch, tmp_path)
    bootstrap, _ = typed_bootstrap(base.launch)
    records, calls = [], []
    spec_row = NS(spec=bootstrap.spec.model_dump(mode="json"))
    profile_row = NS(profile_canonical=bootstrap.native_inputs.model_dump_json())
    source = NS(owner_id=base.fence.owner_id, code="x = 1\n")

    class Session:
        previous = None

        def get(self, model, identity):
            return {
                module.StrategySearchSpec: spec_row,
                module.SearchResolvedExecutionProfile: profile_row,
                module.StrategyVersion: source,
            }[model]

        def scalar(self, query):
            return self.previous

        def add(self, row):
            calls.append("add")
            records.append(row)

        def flush(self):
            calls.append("flush")

    session = Session()
    for name in (
        "require_neutral_transaction",
        "_current_search",
        "bind_search_spec",
        "bind_resolved_execution_profile",
        "initialize_budget",
    ):
        monkeypatch.setattr(
            module, name, lambda *a, _name=name, **k: calls.append(_name)
        )
    monkeypatch.setattr(
        module,
        "verify_native_runtime_binding",
        lambda value: calls.append("runtime_inventory"),
    )

    def reserve(*args, **kwargs):
        assert not records
        assert kwargs["settings"] == "settings"
        calls.append("reserve")
        return NS(id=uuid4())

    monkeypatch.setattr(module, "reserve_budget", reserve)

    def persist(session, fence, intent):
        assert len(records) == 2
        assert records[0].admission_id == records[1].admission_id == intent.admission.id
        assert records[1].input_inventory_sha256 == intent.admission.input_sha256
        calls.append("persist")

    monkeypatch.setattr(module, "persist_controller_intent", persist)
    return base, bootstrap, session, source, calls, records


def test_full_declared_inventory_and_hold_precede_persistence(monkeypatch, tmp_path):
    base, old, session, _, calls, rows = setup(monkeypatch, tmp_path)
    intent, bootstrap, raw, seed = module.prepare_initial_epoch(
        session,
        base.fence,
        runtime=old.native_runtime,
        process_ticket="a" * 64,
        settings="settings",
    )
    assert seed == b"x = 1\n"
    assert intent.bootstrap_sha256 == hashlib.sha256(raw).hexdigest()
    assert intent.admission.logical_operation_id == rows[1].id
    assert bootstrap.epoch_id == rows[0].id
    assert calls.index("reserve") < calls.index("add") < calls.index("persist")
    assert (intent.not_after - intent.issued_at).total_seconds() == 600


def test_existing_epoch_never_reissues(monkeypatch, tmp_path):
    base, old, session, _, calls, rows = setup(monkeypatch, tmp_path)
    session.previous = object()
    with pytest.raises(ValueError, match="first epoch"):
        module.prepare_initial_epoch(
            session,
            base.fence,
            runtime=old.native_runtime,
            process_ticket="a" * 64,
            settings="settings",
        )
    assert "reserve" not in calls and not rows


def test_changed_actual_seed_rejected_before_hold(monkeypatch, tmp_path):
    base, old, session, source, calls, rows = setup(monkeypatch, tmp_path)
    source.code = "substituted"
    with pytest.raises(ValueError, match="seed bytes"):
        module.prepare_initial_epoch(
            session,
            base.fence,
            runtime=old.native_runtime,
            process_ticket="a" * 64,
            settings="settings",
        )
    assert "reserve" not in calls and not rows
