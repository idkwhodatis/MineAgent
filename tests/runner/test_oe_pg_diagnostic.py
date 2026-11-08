"""Closed PostgreSQL counter sampling doubles, not actual populated-flow evidence."""

import importlib
import json
from pathlib import Path

import pytest

from tests.rdagent.test_pg_function_diagnostic import Engine


def module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "integration"))
    return importlib.import_module("oe_pg_diagnostic")


def test_only_before_after_readonly_queries_and_fixed_whitelist(monkeypatch):
    d, output = module(monkeypatch), []
    name = d.FUNCTIONS[0]
    engine = Engine([(name, 4, 8.0, 3.0)])
    with d.diagnose_oe_functions(engine, lambda *a: output.append(a)):
        before = list(engine.commands)
        assert before.count("connect") == 1 and before[-1] == "close"
        engine.rows = [(name, 7, 12.25, 4.5)]
        assert engine.commands == before
    assert engine.commands.count("connect") == 2
    assert engine.commands.count("SET TRANSACTION READ ONLY") == 2
    assert output[0][0] == "OE_PG_FUNCTIONS_V1:"
    report = output[0][1]
    assert report["functions"][0] == {
        "function": name,
        "status": "sampled",
        "calls": 3,
        "total_ms": 4.25,
        "self_ms": 1.5,
    }
    assert len(d.FUNCTIONS) == len(set(d.FUNCTIONS)) == 27
    assert len(report["functions"]) == 27
    assert all(
        row["status"] == "missing_or_untracked" for row in report["functions"][1:]
    )


@pytest.mark.parametrize(
    "bad",
    [
        ("PRIVATE", 1, 1.0, 1.0),
        ("mineagent_oes_consistent", True, 1.0, 1.0),
        ("mineagent_oes_consistent", 1, float("nan"), 1.0),
        ("mineagent_oes_consistent", 1, 1.0, -1.0),
    ],
)
def test_sanitizer_fail_closed_and_no_private_output(monkeypatch, bad):
    d, output = module(monkeypatch), []
    with d.diagnose_oe_functions(Engine([bad]), lambda *a: output.append(a)):
        pass
    assert output[0][1] == {"status": "unavailable"}
    assert "PRIVATE" not in json.dumps(output)


@pytest.mark.parametrize("setting", ["none", "all", "PRIVATE"])
def test_unavailable_tracking_does_not_query_or_claim_zero(monkeypatch, setting):
    d, output, engine = module(monkeypatch), [], Engine([], setting)
    with d.diagnose_oe_functions(engine, lambda *a: output.append(a)):
        pass
    assert "query" not in engine.commands
    assert output[0][1] == {"status": "unavailable"}
    assert "PRIVATE" not in json.dumps(output)


def test_missing_baseline_and_reset_have_explicit_nonzero_status(monkeypatch):
    d, output = module(monkeypatch), []
    name, engine = d.FUNCTIONS[0], Engine([])
    with d.diagnose_oe_functions(engine, lambda *a: output.append(a)):
        engine.rows = [(name, 7, 8.0, 3.0)]
    first = output[0][1]["functions"][0]
    assert first["status"] == "baseline_missing" and first["delta"] is None
    assert first["observed_calls"] == 7 and "calls" not in first
    output.clear()
    with d.diagnose_oe_functions(engine, lambda *a: output.append(a)):
        engine.rows = [(name, 6, 8.0, 3.0)]
    assert output[0][1] == {"status": "counter_reset_or_invalid"}


def test_database_and_output_failure_preserve_same_worker_error(monkeypatch):
    d = module(monkeypatch)
    error = RuntimeError("worker")

    def broken(*a):
        raise ValueError("PRIVATE")

    with (
        pytest.raises(RuntimeError) as caught,
        d.diagnose_oe_functions(Engine(RuntimeError("PRIVATE")), broken),
    ):
        raise error
    assert caught.value is error


def test_wiring_places_stats_outside_timed_work_without_acceptance_changes(monkeypatch):
    root = Path(__file__).parents[1] / "integration"
    source = (root / "oe_queue_diagnostic.py").read_text()
    assert source.index("diagnose_oe_functions(engine, emit),") < source.index(
        "diagnose_receipt_cost(emit),"
    )
    assert "pg_stat_reset" not in (root / "oe_pg_diagnostic.py").read_text()
    flow = (root / "test_oe_structured_normal_flow.py").read_text()
    assert "with diagnose_oe_queue(factory, rid), queue.open():" in flow
    assert 'assert run.status == "succeeded"' in flow
