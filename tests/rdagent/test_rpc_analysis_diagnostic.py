"""Failure observations with explicit doubles, not Docker/PG containment proof."""

import json
import subprocess
from itertools import pairwise
from types import FunctionType, SimpleNamespace

import pytest

from packages.research_runtime.process_contracts import ProcessError
from runtimes.research_supervisor.numeric_supervisor import NumericDockerClient
from tests.rdagent.test_rpc_stage_diagnostic import Channel, _diagnostic
from workers.research import rdagent_operational as worker


def _serve(handler):
    return worker.serve_one(
        Channel(),
        sequence=0,
        deadline=__import__("time").monotonic() + 60,
        authenticate=lambda *_: None,
        handle=handler,
    )


def test_handler_snapshot_precedes_outer_containment_mask(monkeypatch, capsys):
    module = _diagnostic()
    original = ProcessError("analysis_process_kernel_identity")
    masked = ProcessError("rd_provider_containment_unknown")
    order = []
    inspect = module._rpc_failure_chain

    def snapshot(error):
        assert error is original
        order.append("handler_exception")
        return inspect(error)

    def handle(*_):
        order.append("handler_entered")
        raise original

    monkeypatch.setattr(module, "_rpc_failure_chain", snapshot)
    original_serve = worker.serve_one
    with pytest.raises(ProcessError) as caught, module.diagnose_rd_rpc():
        try:
            _serve(handle)
        except ProcessError as error:
            assert error is original
            order.append("controller_cleanup")
        # Match the controller's masking raise outside its handler except block.
        raise masked
    assert caught.value is masked and masked.__context__ is None
    assert worker.serve_one is original_serve
    assert order == ["handler_entered", "handler_exception", "controller_cleanup"]
    report = json.loads(capsys.readouterr().out.removeprefix("RD_RPC_TIMING:"))
    failure = report["failures"][0]
    assert failure["boundary"] == "handle_exception"
    assert failure["method"] == "execute_or_reuse"
    assert failure["errors"][0]["code"] == original.code
    assert report["tail"][-1]["stage"] == "handle"
    assert report["tail"][-1]["ok"] is False
    assert not any(row["stage"] == "send" for row in report["tail"])


def test_chain_retains_suppressed_and_split_cleanup_context_without_values():
    module = _diagnostic()
    original = ProcessError("analysis_process_identity_unobserved")
    cleanup = PermissionError("private-file-path")
    terminal = FileNotFoundError("private-journal-path")
    try:
        try:
            raise original
        finally:
            raise cleanup from None
    except PermissionError:
        try:
            raise terminal from ValueError("private-explicit-cause")
        except FileNotFoundError as error:
            report = module._rpc_failure_chain(error)
    assert [row["family"] for row in report["errors"]] == [
        "file_missing",
        "value",
        "permission",
        "process",
    ]
    assert [row["relation"] for row in report["errors"]] == [
        "raised",
        "cause",
        "context",
        "context",
    ]
    assert [row["parent"] for row in report["errors"]] == [None, 0, 0, 2]
    assert report["errors"][-1]["code"] == original.code
    assert not report["error_overflow"]
    assert "private" not in json.dumps(report)


@pytest.mark.parametrize("count", [2, 12])
def test_failure_graph_is_size_and_cycle_bounded(count):
    module = _diagnostic()
    errors = [ValueError("private") for _ in range(count)]
    for index, error in enumerate(errors):
        error.__context__ = errors[(index + 1) % count]
        error.__cause__ = error.__context__
    report = module._rpc_failure_chain(errors[0])
    assert len(report["errors"]) == min(count, 8)
    assert report["error_overflow"] is (count > 8)
    assert "private" not in json.dumps(report)


@pytest.mark.parametrize("broken", ["classification", "output"])
@pytest.mark.parametrize("error_type", [RuntimeError, SystemExit, KeyboardInterrupt])
def test_diagnostic_failure_cannot_replace_handler_error(
    monkeypatch, capsys, broken, error_type
):
    module = _diagnostic()
    original = ProcessError("analysis_process_identity_unobserved")

    def fail(*_args, **_kwargs):
        raise error_type("private-diagnostic-failure")

    def handle(*_):
        raise original

    monkeypatch.setattr(
        module,
        "_rpc_failure_chain" if broken == "classification" else "print",
        fail,
        raising=False,
    )
    with pytest.raises(ProcessError) as caught, module.diagnose_rd_rpc():
        _serve(handle)
    assert caught.value is original
    output = capsys.readouterr().out
    assert "private" not in output
    if broken == "classification":
        report = json.loads(output.removeprefix("RD_RPC_TIMING:"))
        assert report["failures"] == [
            {
                "boundary": "handle_exception",
                "method": "execute_or_reuse",
                "status": "diagnostic_unavailable",
            }
        ]
    else:
        assert output == ""


@pytest.mark.parametrize("fault", ["before_identity", "identity_callback"])
def test_analysis_breadcrumbs_distinguish_construction_from_persistence(
    monkeypatch, tmp_path, fault
):
    from runtimes.factor_execution.attach_stream import AttachStream
    from runtimes.research_supervisor.analysis_supervisor import (
        AnalysisSupervisor,
        _mint_analysis_authority,
    )
    from tests.qlib.test_analysis_journal import records
    from tests.qlib.test_analysis_supervisor import bound

    module = _diagnostic()
    intent, files = bound()
    authority = _mint_analysis_authority(intent, files, {})
    supervisor = AnalysisSupervisor(intent.admission.image_id, "sha256:" + "f" * 64)
    identity = records()[1]
    original = ValueError("private-original")
    cleanup = OSError("private-cleanup")
    reads, stops = [], []
    # A real AttachStream instance with an inert socket double. No AF_UNIX call.
    reader = object.__new__(AttachStream)
    reader._socket = SimpleNamespace(close=lambda: reads.append("closed"))
    monkeypatch.setattr(AttachStream, "connect", lambda *_a, **_k: reader)
    monkeypatch.setattr(supervisor, "_verify_staged_launch", lambda *_: None)
    monkeypatch.setattr(supervisor, "_verify", lambda state, *_: state)
    monkeypatch.setattr(supervisor.client, "_inspect", lambda *_: {"State": {}})
    monkeypatch.setattr(supervisor.client, "_command", lambda *_a, **_k: (0, b"a" * 64))

    def observe(*_):
        if fault == "before_identity":
            raise original
        return identity

    def callback(*_):
        reads.append("identity_callback")
        raise original

    def stop(*_args, **kwargs):
        stops.append(kwargs)
        if kwargs:
            raise ProcessError("analysis_process_uncertain")
        raise cleanup

    monkeypatch.setattr(supervisor, "_observe_identity", observe)
    monkeypatch.setattr(supervisor, "stop_and_verify", stop)
    with pytest.raises(OSError) as caught:
        supervisor.execute(tmp_path, authority, on_identity=callback)
    assert caught.value is cleanup and len(stops) == 2
    assert stops[0]["original_deadline"] == authority.deadline + 1
    assert stops[1] == {}
    report = module._rpc_failure_chain(caught.value)
    first = report["errors"][0]
    assert first["analysis_launch_constructed"] is True
    assert first["analysis_reader_constructed"] is True
    assert first["analysis_identity_constructed"] is (fault == "identity_callback")
    assert first["analysis_extraction_constructed"] is False
    assert first["analysis_stage"] == "execute"
    assert "value" in [row["family"] for row in report["errors"]]
    assert "private" not in json.dumps(report)
    assert reads == (
        ["identity_callback", "closed"] if fault == "identity_callback" else ["closed"]
    )
    assert list(tmp_path.rglob("stop.json")) == []
    assert bool(list(tmp_path.rglob("identity.json"))) is (fault == "identity_callback")


@pytest.mark.parametrize(
    "args, expected",
    [
        (["image", "inspect", "private"], "image_inspect"),
        (["container", "inspect", "private"], "container_inspect"),
        (["create", "private"], "create"),
        (["start", "private"], "start"),
        (["top", "private"], "top"),
        (["rm", "--force", "private"], "remove_force"),
        (["private-command", "private"], "unknown"),
    ],
)
def test_docker_operation_is_closed_without_executing_cli(monkeypatch, args, expected):
    module = _diagnostic()

    def no_process(*_args, **_kwargs):
        raise OSError("private-process-error")

    monkeypatch.setattr(subprocess, "Popen", no_process)
    with pytest.raises(ProcessError) as caught:
        NumericDockerClient()._command(args)
    report = module._rpc_failure_chain(caught.value)
    assert report["errors"][0]["docker_operation"] == expected
    assert "analysis_stage" not in report["errors"][0]
    assert "private" not in json.dumps(report)


def test_analysis_trace_rejects_name_spoofing_and_bounds_depth():
    module = _diagnostic()

    def fail():
        raise ValueError("private")

    fake = FunctionType(
        fail.__code__.replace(co_name="execute", co_filename="/private/path"),
        {"__name__": "runtimes.research_supervisor.analysis_supervisor"},
    )
    with pytest.raises(ValueError) as caught:
        fake()
    assert module._analysis_trace_context(caught.value) == {}

    def recurse(depth):
        if depth:
            recurse(depth - 1)
        else:
            fail()

    with pytest.raises(ValueError) as caught:
        recurse(100)
    assert module._analysis_trace_context(caught.value) == {"trace_overflow": True}


@pytest.mark.parametrize("target", ["analysis_verify", "shared_command"])
def test_analysis_trace_rejects_equal_but_distinct_code(monkeypatch, target):
    from runtimes.qlib.runtime_contract import verify_inspect

    module = _diagnostic()

    def no_process(*_args, **_kwargs):
        raise OSError("private-process-error")

    monkeypatch.setattr(subprocess, "Popen", no_process)
    if target == "analysis_verify":
        original = verify_inspect
        args = ({},)
        kwargs = {
            "image": "private",
            "inputs": {},
            "role": "invalid",
            "deadline_ms": "private",
            "inventory": {},
            "masked_paths": [],
        }
        expected = {"analysis_stage": "verify_inspect"}
    else:
        original = NumericDockerClient._command
        args, kwargs = (NumericDockerClient(), ["create", "private"]), {}
        expected = {"docker_operation": "create"}
    replacement = original.__code__.replace(co_filename="/private/forged-path")
    assert replacement == original.__code__
    assert replacement is not original.__code__
    copied = FunctionType(replacement, original.__globals__)
    copied.__kwdefaults__ = original.__kwdefaults__
    with pytest.raises((ValueError, ProcessError)) as caught:
        original(*args, **kwargs)
    assert module._analysis_trace_context(caught.value) == expected
    with pytest.raises((ValueError, ProcessError)) as caught:
        copied(*args, **kwargs)
    assert module._analysis_trace_context(caught.value) == {}


def test_handler_failure_inventory_is_bounded(capsys):
    module = _diagnostic()

    def handle(*_):
        raise ValueError("private")

    with module.diagnose_rd_rpc():
        for _ in range(10):
            with pytest.raises(ValueError):
                _serve(handle)
    output = capsys.readouterr().out
    report = json.loads(output.removeprefix("RD_RPC_TIMING:"))
    assert len(report["failures"]) == 8
    assert "private" not in output


def test_masked_controller_projection_requires_exact_owned_frame(monkeypatch):
    from uuid import uuid4

    from packages.research_runtime.fences import AttemptFence

    module = _diagnostic()
    original = ProcessError("analysis_process_claim")
    masked = ProcessError("rd_provider_containment_unknown")
    fence = AttemptFence(uuid4(), uuid4(), uuid4(), 1, uuid4())

    def run(fence):
        try:
            raise original
        except ProcessError as error:
            failure = error
        assert failure is original
        raise masked

    monkeypatch.setattr(worker, "_execute_controller", run)
    with pytest.raises(ProcessError) as caught:
        run(fence)
    assert caught.value is masked and masked.__context__ is None
    assert module._masked_controller_failure(caught.value, fence.run_id) == {
        "chain": "single",
        "masked_family": "process",
        "masked_code": "analysis_process_claim",
        "masked_stage": "unknown",
        "masked_sqlstate": "none",
        "state": "observed",
    }
    assert module._masked_controller_failure(caught.value, uuid4()) == {}
    # Structurally equivalent code is not the live worker frame identity.
    alternate = FunctionType(run.__code__.replace(), globals(), closure=run.__closure__)
    monkeypatch.setattr(worker, "_execute_controller", alternate)
    assert module._masked_controller_failure(caught.value, fence.run_id) == {}


def test_masked_controller_projection_distinguishes_no_prior_failure(monkeypatch):
    from uuid import uuid4

    from packages.research_runtime.fences import AttemptFence

    module = _diagnostic()
    fence = AttemptFence(uuid4(), uuid4(), uuid4(), 1, uuid4())

    def run(fence):
        failure = None
        assert failure is None
        raise ProcessError("rd_provider_containment_unknown")

    monkeypatch.setattr(worker, "_execute_controller", run)
    with pytest.raises(ProcessError) as caught:
        run(fence)
    assert module._masked_controller_failure(caught.value, fence.run_id) == {
        "state": "no_prior_failure"
    }


def test_masked_controller_projection_separates_unique_and_ambiguous_leaves():
    from sqlalchemy.exc import DBAPIError

    module = _diagnostic()

    class DatabaseFailure(Exception):
        sqlstate = "55P03"

    database = DBAPIError("private-sql", {"secret": "sensitive"}, DatabaseFailure())
    original = ProcessError("analysis_process_uncertain")
    original.__context__ = database
    result = module._masked_controller_summary(original)
    assert result["chain"] == "unique_leaf"
    assert result["masked_code"] == "analysis_process_uncertain"
    assert result["nested_family"] == "database"
    assert result["nested_sqlstate"] == "55P03"
    assert "private" not in json.dumps(result)

    original.__cause__ = PermissionError("hidden-path")
    result = module._masked_controller_summary(original)
    assert result["chain"] == "ambiguous"
    assert not any(key.startswith("nested") for key in result)


def test_masked_controller_projection_marks_cycle_and_overflow():
    module = _diagnostic()
    errors = [ValueError("private") for _ in range(9)]
    errors[0].__context__ = errors[0]
    assert module._masked_controller_summary(errors[0])["chain"] == "ambiguous"
    for outer, inner in pairwise(errors):
        outer.__context__ = inner
    result = module._masked_controller_summary(errors[0])
    assert result["chain"] == "overflow"
    assert not any(key.startswith("nested") for key in result)


@pytest.mark.parametrize(
    ("controller_limit", "expected"),
    [
        (9, "gate_expired_controller"),
        (10, "gate_expired_local"),
        (None, "gate_expired"),
    ],
)
def test_expired_stop_receipt_gate_reports_exact_origin_and_boundary(
    monkeypatch, controller_limit, expected
):
    from backend.app import rdagent_provider_broker as broker
    from backend.app import rdagent_workflow_coordination as coordination
    from backend.app.services import DomainError
    from packages.rdagent_provider.process_receipts import SDKStopped

    gate = coordination._Gate()
    monkeypatch.setattr(coordination, "monotonic", lambda: 9)
    monkeypatch.setattr(broker, "receipt_bytes", lambda _: b"private-receipt")
    monkeypatch.setattr(broker, "_publish", lambda *_: object())
    monkeypatch.setattr(broker, "neutral_transaction", lambda *_, **__: gate.hold(9))
    document = SDKStopped.model_construct()
    module = _diagnostic()
    with pytest.raises(DomainError) as failure:
        broker._retain_receipt(object(), object(), object(), document, "private-key")
    cut = module._neutral_busy_cut(failure.value, controller_limit=controller_limit)
    assert cut == {"neutral_busy_origin": expected, "receipt_boundary": "bind"}


def test_same_thread_gate_rejection_and_postgres_scope_are_distinct():
    from backend.app import rdagent_workflow_coordination as coordination
    from backend.app.research_lifecycle import _try_neutral_lock
    from backend.app.services import DomainError

    module = _diagnostic()
    gate = coordination._Gate()
    deadline = coordination.monotonic() + 60
    with (
        gate.hold(deadline),
        pytest.raises(DomainError) as failure,
        gate.hold(deadline),
    ):
        pytest.fail("reentrant acquisition unexpectedly admitted")
    assert module._neutral_busy_cut(failure.value) == {
        "neutral_busy_origin": "gate_same_thread_or_closed",
    }
    for scope, category in (
        ("submit:private-identifier", "postgres_owner"),
        ("storage", "postgres_resource"),
    ):
        with pytest.raises(DomainError) as rejected:
            _try_neutral_lock(SimpleNamespace(scalar=lambda *_: False), scope)
        assert module._neutral_busy_cut(rejected.value) == {
            "neutral_busy_origin": category
        }
