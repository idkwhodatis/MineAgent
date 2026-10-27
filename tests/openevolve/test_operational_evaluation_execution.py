"""Actual host orchestration with explicit Docker/DB doubles, not acceptance."""

from contextlib import contextmanager
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from test_operational_native_process import case as native_case

from backend.app import openevolve_evaluation_execution as s
from backend.app.openevolve_controller_services import typed_bootstrap


def setup(monkeypatch, tmp_path, fault=None):
    c = native_case.__wrapped__(monkeypatch, tmp_path)
    bootstrap, _ = typed_bootstrap(c.launch)
    calls = []
    candidate = uuid4()

    class Session:
        def scalar(self, query):
            return None

    @contextmanager
    def transaction(*args, **kwargs):
        calls.append("begin")
        yield Session()
        calls.append("commit")

    monkeypatch.setattr(s, "neutral_transaction", transaction)
    monkeypatch.setattr(s, "_scope", lambda *a: (candidate, bootstrap))
    monkeypatch.setattr(s, "prepare_evaluation", lambda *a, **k: c.evaluation.id)
    monkeypatch.setattr(
        s, "render_prepared_input", lambda *a, **k: (c.manifest, {"fixed": b"bytes"})
    )
    monkeypatch.setattr(s, "admit_prepared_input", lambda *a, **k: c.intent)

    def peer(*a, **k):
        calls.append("peer")
        if fault == "late_peer" and calls.count("peer") == 3:
            raise ValueError("peer exited before start")

    monkeypatch.setattr(s, "resolve_evaluation_peer", peer)
    monkeypatch.setattr(
        s, "claim_native_launch", lambda *a: c.intent if fault != "claim" else None
    )
    monkeypatch.setattr(
        s, "check_native_claim", lambda *a, **k: calls.append("claim-recheck")
    )

    def identity(*a, **k):
        calls.append("identity")
        if fault == "identity":
            raise ValueError("identity persistence failed")

    monkeypatch.setattr(s, "record_native_identity", identity)
    monkeypatch.setattr(
        s, "record_native_stopped", lambda *a, **k: calls.append("positive-stop")
    )
    monkeypatch.setattr(
        s,
        "settle_native_conservatively",
        lambda *a, **k: calls.append("full-settlement"),
    )
    monkeypatch.setattr(
        s, "_persist_result", lambda *a: calls.append("retained-result")
    )

    def mint(*a):
        calls.append("mint")
        return object()

    monkeypatch.setattr(s, "_mint_native_authority", mint)

    class Supervisor:
        def __init__(self, image, parent):
            assert image == bootstrap.native_runtime.protected_image_id
            assert parent == bootstrap.native_runtime.native_parent_image_id

        def verified_inventory(self, role):
            assert role == "native"
            calls.append("inspect")
            return {"fixture": "explicit Docker double"}

        def execute(self, root, authority, *, on_identity, before_submission):
            before_submission()
            calls.append("create")
            before_submission()
            calls.append("start")
            on_identity(NS(untrusted_callback_payload=True))
            if fault == "execution":
                raise RuntimeError("unknown external outcome")
            calls.append("actual-double-stop")

    monkeypatch.setattr(s, "NativeSupervisor", Supervisor)
    return c, calls, NS(storage_root=tmp_path)


def test_claim_committed_then_two_fresh_boundaries_then_stop_settlement(
    monkeypatch, tmp_path
):
    c, calls, settings = setup(monkeypatch, tmp_path)
    result = s.execute_window(
        None,
        c.fence,
        None,
        event=None,
        ticket_key=b"k" * 32,
        window_id=c.evaluation.window_id,
        settings=settings,
    )
    assert result == c.evaluation.id
    assert calls.index("commit", calls.index("inspect")) < calls.index("mint")
    assert calls.count("claim-recheck") == 2
    assert (
        calls.index("identity")
        < calls.index("actual-double-stop")
        < calls.index("positive-stop")
        < calls.index("full-settlement")
        < calls.index("retained-result")
    )
    assert calls.count("peer") == 3  # none during truthful post-submission settlement


@pytest.mark.parametrize("fault", ["claim", "late_peer", "identity", "execution"])
def test_unknown_or_stale_launch_never_invents_stop_or_success(
    monkeypatch, tmp_path, fault
):
    c, calls, settings = setup(monkeypatch, tmp_path, fault)
    with pytest.raises((ValueError, RuntimeError)):
        s.execute_window(
            None,
            c.fence,
            None,
            event=None,
            ticket_key=b"k" * 32,
            window_id=c.evaluation.window_id,
            settings=settings,
        )
    assert (
        "positive-stop" not in calls
        and "full-settlement" not in calls
        and "retained-result" not in calls
    )
    if fault == "claim":
        assert "mint" not in calls and "create" not in calls
    if fault == "late_peer":
        assert "create" in calls and "start" not in calls


def test_exact_runtime_build_inputs_not_operator_hash_strings():
    import hashlib
    from pathlib import Path

    from runtimes.openevolve_evaluator.recipe import wrapper_inventory_sha256

    runtime = NS(
        wrapper_inventory_sha256=wrapper_inventory_sha256(),
        dependency_lock_sha256=hashlib.sha256(
            (
                Path(__file__).parents[2] / "runtimes/evaluator/requirements.lock"
            ).read_bytes()
        ).hexdigest(),
    )
    s.verify_native_runtime_binding(runtime)
    runtime.dependency_lock_sha256 = "0" * 64
    with pytest.raises(ValueError, match="fixed accepted build inputs"):
        s.verify_native_runtime_binding(runtime)
