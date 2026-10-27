"""Containment orchestration tests with explicit DB/Docker doubles, no real stop."""

import sys
from contextlib import nullcontext
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import pytest

from packages.research_runtime.process_contracts import ProcessError
from tests.qlib.test_analysis_journal import records
from workers.research import analysis_recovery as target


@pytest.mark.parametrize("fault", [None, "missing", "stop", "drift", "record"])
def test_containment_only_order(monkeypatch, fault):
    admission = records()[0].intent.admission
    calls = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, *args):
            return None if fault == "missing" else object()

        def commit(self):
            calls.append("commit")

    class Factory:
        def __init__(self):
            self.kw = {"bind": object()}

        def __call__(self):
            return Session()

    def binding(*args):
        calls.append("binding")
        return admission, (
            "sha256:" + "e" * 64
            if fault == "drift" and calls.count("binding") == 2
            else "sha256:" + "d" * 64
        )

    class Supervisor:
        def __init__(self, image, parent):
            assert image == admission.image_id and parent == "sha256:" + "d" * 64

        def stop_and_verify(self, *args):
            calls.append("stop")
            if fault == "stop":
                raise ProcessError("uncertain")

    def record(*args, **kwargs):
        calls.append("record")
        if fault == "record":
            raise ProcessError("uncertain")

    monkeypatch.setattr(target, "shared_lifecycle", lambda *a: nullcontext())
    monkeypatch.setattr(target, "_binding", binding)
    monkeypatch.setattr(target, "AnalysisSupervisor", Supervisor)
    monkeypatch.setattr(target, "record_analysis_containment", record)
    if fault in ("stop", "drift", "record"):
        with pytest.raises(ProcessError):
            target.recover_analysis_unit(Factory(), "/retained", admission.id)
        assert "commit" not in calls
    else:
        assert target.recover_analysis_unit(Factory(), "/retained", admission.id) is (
            fault is None
        )
        assert calls == (
            ["binding", "stop", "binding", "record", "commit"] if fault is None else []
        )


@pytest.mark.parametrize("fault", [None, "owner_id", "image", "input"])
def test_recovery_uses_exact_persisted_plan_and_intent(monkeypatch, fault):
    launch = records()[0]
    intent = launch.intent
    a = intent.admission
    module = ModuleType("backend.app.factor_analysis_models")
    module.FactorAnalysisPlan = type("Plan", (), {})
    module.FactorDirectAnalysisIntent = type("Intent", (), {})
    monkeypatch.setitem(sys.modules, module.__name__, module)
    row = SimpleNamespace(
        **{
            k: getattr(a, k) for k in ("owner_id", "run_id", "attempt_id", "generation")
        },
        id=a.logical_operation_id,
        plan_id=uuid4(),
        **{
            k: getattr(intent.inputs, k)
            for k in (
                "snapshot_id",
                "label_source_id",
                "protocol_sha256",
                "analysis_input_sha256",
                "analysis_input_size_bytes",
            )
        },
    )
    plan = SimpleNamespace(
        owner_id=a.owner_id,
        run_id=a.run_id,
        qlib_image_id=a.image_id,
        qlib_installed_parent_image_id="sha256:" + "d" * 64,
    )
    if fault == "owner_id":
        row.owner_id = uuid4()
    if fault == "image":
        plan.qlib_image_id = "sha256:" + "f" * 64
    if fault == "input":
        row.analysis_input_sha256 = "f" * 64
    monkeypatch.setattr(
        target,
        "_persisted",
        lambda *args: (
            a,
            object(),
            {"launch_intent": SimpleNamespace(payload=intent.model_dump(mode="json"))},
        ),
    )
    session = SimpleNamespace(
        get=lambda cls, id: plan if cls is module.FactorAnalysisPlan else row
    )
    if fault:
        with pytest.raises(ProcessError, match="binding"):
            target._binding(session, a.id)
    else:
        assert target._binding(session, a.id) == (
            a,
            plan.qlib_installed_parent_image_id,
        )
