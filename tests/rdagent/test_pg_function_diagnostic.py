"""Actual diagnostic sanitizer with connection doubles; not a PG runtime claim."""

import importlib.util
import json
from contextlib import contextmanager
from pathlib import Path

import pytest


def module():
    path = Path(__file__).parents[1] / "integration/rd_pg_diagnostic.py"
    spec = importlib.util.spec_from_file_location("rd_pg_diagnostic_test", path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class Engine:
    def __init__(self, rows, setting="pl"):
        self.rows, self.setting, self.commands = rows, setting, []

    @contextmanager
    def connect(self):
        self.commands.append("connect")
        try:
            yield self
        finally:
            self.commands.append("close")

    def exec_driver_sql(self, sql):
        assert sql in ("SET TRANSACTION READ ONLY", "SHOW track_functions")
        self.commands.append(sql)
        return self

    def scalar_one(self):
        return self.setting

    def execute(self, query, params):
        assert "schemaname = 'public'" in str(query)
        assert tuple(params) == ("names",)
        self.commands.append("query")
        return self

    def all(self):
        if isinstance(self.rows, Exception):
            raise self.rows
        return self.rows


def test_exact_before_after_and_no_extra_calls(capsys):
    m = module()
    name = m.FUNCTIONS[0]
    engine = Engine([(name, 4, 8.0, 3.0)])
    with m.diagnose_provider_functions(engine, stage="parent"):
        assert engine.commands[-1] == "close"
        engine.rows = [(name, 7, 12.25, 4.5)]
    assert engine.commands.count("connect") == 2
    report = json.loads(capsys.readouterr().out.split(":", 1)[1])
    assert report["functions"][0] == {
        "function": name,
        "status": "sampled",
        "calls": 3,
        "total_ms": 4.25,
        "self_ms": 1.5,
    }
    assert all(x["status"] == "missing_or_untracked" for x in report["functions"][1:])


@pytest.mark.parametrize(
    "row",
    [
        ("PRIVATE", 1, 1.0, 1.0),
        (None, 1, 1.0, 1.0),
        ("mineagent_rd_source_deferred", True, 1.0, 1.0),
        ("mineagent_rd_source_deferred", -1, 1.0, 1.0),
        ("mineagent_rd_source_deferred", 10**12 + 1, 1.0, 1.0),
        ("mineagent_rd_source_deferred", 1, float("nan"), 1.0),
        ("mineagent_rd_source_deferred", 1, float("inf"), 1.0),
        ("mineagent_rd_source_deferred", 1, -1.0, 1.0),
        ("mineagent_rd_source_deferred", 1, "PRIVATE", 1.0),
        ("mineagent_rd_source_deferred", 1, 1.0, False),
        ("mineagent_rd_source_deferred", 1, 1.0, 10**12 + 1),
    ],
)
def test_malformed_rows_fail_closed_without_text(row, capsys):
    m = module()
    with m.diagnose_provider_functions(Engine([row]), stage="seeded_child"):
        pass
    out = capsys.readouterr().out
    assert "PRIVATE" not in out
    assert json.loads(out.split(":", 1)[1]) == {
        "stage": "seeded_child",
        "status": "unavailable",
    }


def test_duplicate_function_and_counter_reset():
    m = module()
    row = (m.FUNCTIONS[0], 1, 2.0, 1.0)
    assert m._snapshot(Engine([row, row])) is None
    assert m._difference({row[0]: (2, 2.0, 1.0)}, {row[0]: (1, 2.0, 1.0)}) == {
        "status": "counter_reset_or_invalid"
    }


@pytest.mark.parametrize("setting", ["none", "all", "PRIVATE"])
def test_tracking_unavailable_does_not_query(setting):
    m = module()
    engine = Engine([], setting)
    assert m._snapshot(engine) is None
    assert "query" not in engine.commands


def test_diagnostic_failure_preserves_actual_failure(capsys):
    m = module()
    error = RuntimeError("actual worker failure")
    with (
        pytest.raises(RuntimeError) as caught,
        m.diagnose_provider_functions(Engine(RuntimeError("PRIVATE")), stage="parent"),
    ):
        raise error
    assert caught.value is error
    assert "PRIVATE" not in capsys.readouterr().out


def test_output_failure_preserves_actual_failure(monkeypatch):
    m = module()

    def fail(*args, **kwargs):
        raise RuntimeError("PRIVATE")

    monkeypatch.setattr("builtins.print", fail)
    error = ValueError("actual")
    with (
        pytest.raises(ValueError) as caught,
        m.diagnose_provider_functions(Engine([]), stage="parent"),
    ):
        raise error
    assert caught.value is error


def test_unknown_stage_rejected_before_database():
    m = module()
    engine = Engine([])
    with (
        pytest.raises(ValueError, match="invalid diagnostic stage"),
        m.diagnose_provider_functions(engine, stage="PRIVATE"),
    ):
        pytest.fail("must not enter")
    assert engine.commands == []


def test_first_observed_row_never_fabricates_zero_baseline():
    m = module()
    name = m.FUNCTIONS[0]
    report = m._difference({}, {name: (3, 7.25, 2.5)})
    assert report["functions"][0] == {
        "function": name,
        "status": "baseline_missing",
        "delta": None,
        "observed_calls": 3,
        "observed_total_ms": 7.25,
        "observed_self_ms": 2.5,
    }
    assert "calls" not in report["functions"][0]
