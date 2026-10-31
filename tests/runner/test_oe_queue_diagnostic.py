"""Offline observer privacy/wiring, not a substitute for actual OE execution."""

import ast
import importlib.util
import json
from pathlib import Path
from types import FunctionType, SimpleNamespace

import pytest
from sqlalchemy.exc import DBAPIError

from packages.research_runtime.process_contracts import ProcessError


def module(monkeypatch):
    directory = Path(__file__).parents[1] / "integration"
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location(
        "oe_queue_diagnostic_test", directory / "oe_queue_diagnostic.py"
    )
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_queue_failure_closed_codes_database_and_no_exception_text(monkeypatch):
    diagnostic = module(monkeypatch)
    for code in diagnostic.CODES:
        assert diagnostic.failure(ProcessError(code))["code"] == code
    for error in (
        ValueError("synthetic_private"),
        ProcessError("synthetic_private"),
        ProcessError("oe_structured_original_lease_private"),
    ):
        result = diagnostic.failure(error)
        assert result["code"] == "unknown"
        assert "synthetic_private" not in json.dumps(result)
    original = Exception("synthetic_private SQL payload")
    original.sqlstate = "23514"
    original.diag = SimpleNamespace(
        table_name="research_runs",
        constraint_name="ck_research_run_legacy_config",
        message_primary="synthetic_private",
    )
    result = diagnostic.failure(
        DBAPIError("synthetic_private SQL", {"secret": "synthetic_private"}, original)
    )
    assert result["database"] == {
        "sqlstate": "23514",
        "family": "check_violation",
        "category": "run_config",
    }
    assert "synthetic_private" not in json.dumps(result)

    def execute(error):
        raise error

    exact_frame = FunctionType(
        execute.__code__, {"__name__": "workers.research.oe_structured_queue"}
    )
    try:
        exact_frame(ProcessError("oe_structured_original_lease"))
    except ProcessError as error:
        assert diagnostic.failure(error)["stage"] == "queue"


def test_observer_rethrows_identical_error_bounds_reports_and_restores(
    monkeypatch, capsys
):
    diagnostic = module(monkeypatch)
    from workers.research import oe_structured_queue

    error = ProcessError("oe_structured_original_lease")
    calls = []

    def original(*args, **kwargs):
        calls.append((args, kwargs))
        raise error

    monkeypatch.setattr(oe_structured_queue, "execute", original)
    monkeypatch.setattr(diagnostic, "queue_state", lambda *_: {"status": "running"})
    source = (Path(__file__).parents[2] / "workers/research/queue.py").read_text()
    task = next(
        n
        for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.FunctionDef) and n.name == "execute_oe_structured_run"
    )
    task.decorator_list = []
    namespace = {"settings": "synthetic_private"}
    exec(  # noqa: S102 -- exact checked-in task body, no untrusted code
        compile(ast.Module(body=[task], type_ignores=[]), "actual_queue_task", "exec"),
        namespace,
    )
    with diagnostic.diagnose_oe_queue(object(), "synthetic_private"):
        for _ in range(10):
            with pytest.raises(ProcessError) as caught:
                namespace["execute_oe_structured_run"]("synthetic_private", 1)
            assert caught.value is error
    assert oe_structured_queue.execute is original and len(calls) == 10
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 9
    assert sum(line.startswith("OE_QUEUE_FAILURE_V1:") for line in lines) == 8
    assert lines[-1] == 'OE_QUEUE_STATE_V1:{"status": "running"}'
    assert "synthetic_private" not in "".join(lines)
    sentinel = object()
    monkeypatch.setattr(oe_structured_queue, "execute", lambda *_: sentinel)
    with diagnostic.diagnose_oe_queue(object(), "synthetic_private"):
        assert oe_structured_queue.execute() is sentinel

    def unavailable(*_):
        raise ValueError("synthetic_private")

    monkeypatch.setattr(diagnostic, "queue_state", unavailable)
    with (
        pytest.raises(ProcessError) as caught,
        diagnostic.diagnose_oe_queue(object(), "synthetic_private"),
    ):
        raise error
    assert caught.value is error
    assert "diagnostic_unavailable" in capsys.readouterr().out


def test_state_projection_is_bounded_and_never_emits_identity_or_raw_error(monkeypatch):
    diagnostic = module(monkeypatch)
    run = SimpleNamespace(status="running", id="synthetic_private")
    attempt = SimpleNamespace(
        status="synthetic_private",
        error="synthetic_private",
        sandbox_stopped=1,
        exit_code=True,
    )
    result = diagnostic.project_state(
        run, [attempt] * 9, [("synthetic_private", "synthetic_private")] * 17
    )
    assert result["attempt_overflow"] and result["unit_overflow"]
    assert len(result["attempts"]) == 8 and len(result["units"]) == 16
    assert result["attempts"][0] == {
        "status": "unknown",
        "error": "other",
        "stopped": False,
        "exit_code": None,
    }
    assert "synthetic_private" not in json.dumps(result)
    assert diagnostic.project_state(None, [], [("openevolve_orchestrator", "running")])[
        "units"
    ] == [{"role": "openevolve_orchestrator", "state": "running"}]


def test_actual_queue_wiring_preserves_success_assertion_and_fixed_stage_catalog(
    monkeypatch,
):
    diagnostic = module(monkeypatch)
    root = Path(__file__).parents[2]
    flow = (root / "tests/integration/test_oe_structured_normal_flow.py").read_text()
    assert "with diagnose_oe_queue(factory, rid), queue.open():" in flow
    assert 'assert run.status == "succeeded"' in flow
    assert "wait=False" in flow and "concurrency=1" in flow
    for name, function in diagnostic.STAGES:
        path = root / (name.replace(".", "/") + ".py")
        assert path.is_file() and f"def {function}(" in path.read_text()
    source = (root / "tests/integration/oe_queue_diagnostic.py").read_text()
    assert ".limit(9)" in source and ".limit(17)" in source
    assert "session.commit" not in source and "session.add" not in source


def test_static_supervisor_and_validation_categories_are_closed(monkeypatch):
    from pydantic import ConfigDict, ValidationError, create_model

    diagnostic = module(monkeypatch)
    root = Path(__file__).parents[2]
    source = (root / "runtimes/openevolve_structured_host/supervisor.py").read_text()
    literals = {
        n.value
        for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.Constant) and type(n.value) is str
    }
    for message, category in diagnostic.VALUE_CATEGORIES.items():
        assert message in literals
        assert diagnostic.failure(ValueError(message))["category"] == category
        for private in (message + " synthetic_private", "synthetic_private " + message):
            result = diagnostic.failure(ValueError(private))
            assert result["category"] == "other"
            assert "synthetic_private" not in json.dumps(result)
    for count in (1, 40):
        model = create_model(
            "SyntheticPrivateModel",
            __config__=ConfigDict(strict=True),
            **{f"synthetic_private_{i}": (int, ...) for i in range(count)},
        )
        with pytest.raises(ValidationError) as caught:
            model.model_validate(
                {
                    f"synthetic_private_{i}": "synthetic_private_input"
                    for i in range(count)
                }
            )
        result = diagnostic.failure(caught.value)
        assert result["family"] == "validation"
        assert result["validation_count"] == count
        assert result["validation_overflow"] is (count > 32)
        assert result["validation_types"] == (["int_type"] if count == 1 else [])
        assert "synthetic_private" not in json.dumps(result)
