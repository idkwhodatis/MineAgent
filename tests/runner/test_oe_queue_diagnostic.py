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
        ProcessError("oe_structured_controller_context_private"),
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


@pytest.mark.parametrize(
    "code", ["provider_input_rejected", "provider_stop_receipt_unconfirmed"]
)
def test_fixed_provider_failure_codes_without_private_state(monkeypatch, code):
    from packages.rdagent_provider.transport import ProviderFailure

    diagnostic = module(monkeypatch)
    error = ProviderFailure(code, outcome_unknown=False, contained=True)
    error.add_note("synthetic_private_note")
    error.__cause__ = ValueError("synthetic_private_cause")
    assert diagnostic.failure(error) == {
        "family": "provider",
        "code": code,
        "stage": "unknown",
    }
    assert "synthetic_private" not in json.dumps(diagnostic.failure(error))


@pytest.mark.parametrize(
    "code",
    [
        "provider_input_rejected_private",
        "private_provider_input_rejected",
        "provider_stop_receipt_unconfirmed_private",
        "private_provider_stop_receipt_unconfirmed",
        "provider_admission_unconfirmed",
        "provider_identity_receipt_unconfirmed",
        "PROVIDER_INPUT_REJECTED",
        None,
        1,
    ],
)
def test_provider_code_spoofs_and_other_unknowns_stay_closed(monkeypatch, code):
    from packages.rdagent_provider.transport import ProviderFailure

    diagnostic = module(monkeypatch)
    error = ProviderFailure(code)
    projected = diagnostic.failure(error)
    assert projected == {"family": "provider", "code": "unknown", "stage": "unknown"}
    assert "private" not in json.dumps(projected)


def test_provider_family_cannot_be_spoofed_by_class_name(monkeypatch):
    diagnostic = module(monkeypatch)
    impostor = type("ProviderFailure", (RuntimeError,), {})
    error = impostor("synthetic_private")
    error.code = "provider_input_rejected"
    assert diagnostic.failure(error)["family"] == "other"

    class Code(str):
        pass

    from packages.rdagent_provider.transport import ProviderFailure

    error = ProviderFailure(Code("provider_input_rejected"))
    assert diagnostic.failure(error) == {
        "family": "provider",
        "code": "unknown",
        "stage": "unknown",
    }


@pytest.mark.parametrize(
    "failure_at, expected, launches, wires",
    [
        ("intent_deadline", "provider_input_rejected", 0, 0),
        ("started", "provider_stop_receipt_unconfirmed", 1, 0),
        ("stopped", "provider_stop_receipt_unconfirmed", 1, 1),
    ],
)
def test_actual_oe_transport_reproduces_distinct_closed_failures(
    monkeypatch, failure_at, expected, launches, wires
):
    """Real broker/complete_chat/execute and DTOs, synthetic DB/clock/process only.

    This proves reachability, not which branch occurred in hosted run41570049735.
    No SDK process, Docker, network, real credentials or database is used.
    """
    import sys

    from backend.app import oe_structured_provider_broker as broker
    from packages.openevolve_structured.provider_transport import complete_chat
    from packages.rdagent_provider import transport
    from tests.openevolve.test_structured_provider_broker import arranged

    diagnostic = module(monkeypatch)
    case = arranged(monkeypatch)
    case.peer._context.deadline = 130.0
    clock = SimpleNamespace(ns=100_000_000_000)
    times = SimpleNamespace(
        monotonic=lambda: clock.ns / 1_000_000_000, monotonic_ns=lambda: clock.ns
    )
    monkeypatch.setattr(broker, "time", times)
    monkeypatch.setattr(transport, "time", times)
    monkeypatch.setattr(
        broker,
        "Path",
        lambda _: SimpleNamespace(
            read_text=lambda: "00000000-0000-0000-0000-000000000001"
        ),
    )
    retain = broker.retain_physical_receipt
    counts = {"launch": 0, "wire": 0}

    def retained(*args):
        retain(*args)
        document = args[4]
        if failure_at == "intent_deadline" and document.kind == "intent":
            clock.ns = document.deadline_monotonic_ns + 1
        elif document.kind == failure_at:
            raise ProcessError("synthetic_private_callback")

    def sdk(*args, **kwargs):
        kwargs["python_executable"] = sys.executable
        return complete_chat(*args, **kwargs)

    def launch(*args, **kwargs):
        counts["launch"] += 1
        return SimpleNamespace(pid=123)

    def identity(process, deadline):
        intent = case.documents[0]
        return transport.ProcessIdentity(
            123, 42, 123, 123, 2, str(intent.boot_id), deadline
        )

    def wire(*args):
        counts["wire"] += 1
        return b"result_is_never_parsed_after_stop_callback_failure"

    monkeypatch.setattr(broker, "retain_physical_receipt", retained)
    monkeypatch.setattr(broker, "complete_chat", sdk)
    monkeypatch.setattr(
        transport, "subprocess", SimpleNamespace(Popen=launch, PIPE=-1, DEVNULL=-3)
    )
    monkeypatch.setattr(transport, "_observe_process", identity)
    monkeypatch.setattr(transport, "_stop_group", lambda _: True)
    monkeypatch.setattr(transport, "_wire_exchange", wire)
    with pytest.raises(transport.ProviderFailure) as caught:
        case.call()
    assert caught.value.code == expected
    assert counts == {"launch": launches, "wire": wires}
    assert case.documents[0].kind == "intent"
    assert "submitted" in case.events
    result = diagnostic.failure(caught.value)
    assert result == {"family": "provider", "code": expected, "stage": "provider"}
    assert "synthetic_private" not in json.dumps(result)
