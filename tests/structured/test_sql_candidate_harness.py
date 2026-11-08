"""Offline regression of driver rejection and numeric evidence export only."""

import copy
import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from psycopg.errors import CheckViolation
from sqlalchemy.exc import DBAPIError

from script.sql_candidate_proof import COMPONENT_TABLES, export_component_measurements


class SavepointConnection:
    def __init__(self):
        self.errors = []

    @contextmanager
    def begin_nested(self):
        try:
            yield
        except BaseException as error:
            self.errors.append(error)
            raise


@pytest.mark.parametrize("wrapped", [False, True], ids=["raw-copy", "sqlalchemy"])
@pytest.mark.parametrize("result", ["matching", "wrong-state", "wrong-constraint"])
def test_rejection_preserves_raw_and_wrapped_driver_assertions(
    wrapped, result, request
):
    request.config.addinivalue_line("markers", "postgres: requires PostgreSQL")
    from tests.integration.test_row_validation_invariants import rejection

    error = CheckViolation(
        "synthetic failure", info={ord("n"): b"ck_rvi_rd_provider_payload"}
    )
    if result == "wrong-state":
        error.sqlstate = "23505"
    caught = DBAPIError("COPY", None, error) if wrapped else error
    connection = SavepointConnection()

    def operation():
        raise caught

    constraint = (
        "wrong_constraint"
        if result == "wrong-constraint"
        else "ck_rvi_rd_provider_payload"
    )
    if result == "matching":
        rejection(connection, operation, constraint)
    else:
        with pytest.raises(AssertionError):
            rejection(connection, operation, constraint)
    assert connection.errors == [caught]


@pytest.mark.parametrize("kind", [RuntimeError, ValueError])
def test_rejection_propagates_unrelated_exceptions(kind, request):
    request.config.addinivalue_line("markers", "postgres: requires PostgreSQL")
    from tests.integration.test_row_validation_invariants import rejection

    error = kind("unrelated error")
    connection = SavepointConnection()

    def operation():
        raise error

    with pytest.raises(kind) as caught:
        rejection(connection, operation, "ck_rvi_rd_provider_payload")
    assert caught.value is error
    assert connection.errors == [error]


def measurements(component):
    result = []
    for phase in ("before", "after"):
        for sample in range(3):
            row = {"phase": phase, "sample": sample, "write_ms": 1.25}
            row.update(
                {
                    "rows_written_per_table": 8,
                    "fetched_spec_rows": 64,
                    "same_spec_read_ms": 2.5,
                }
                if component == "minimal"
                else {
                    "rows_per_table": 4,
                    "checked_fetches": dict.fromkeys(COMPONENT_TABLES, 32),
                    "read_ms": 2.5,
                }
            )
            result.append(row)
    return result


@pytest.mark.parametrize("component", ["minimal", "expanded"])
def test_successful_measurements_survive_pytest_capture_as_numeric_artifacts(
    component, tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "MINEAGENT_SQL_CANDIDATE_MEASUREMENTS_DIR", "var/sql-candidate-proof"
    )
    samples = measurements(component)
    original = copy.deepcopy(samples)
    report = export_component_measurements(component, samples)
    assert capsys.readouterr().out == ""
    path = Path("var/sql-candidate-proof") / f"{component}-component-measurements.json"
    data = path.read_bytes()
    assert len(data) <= 16384
    assert json.loads(data) == report
    assert samples == original
    assert report["samples"] == original
    assert set(report) == {"scope", "component", "samples"}
    with pytest.raises(FileExistsError):
        export_component_measurements(component, samples)


@pytest.mark.parametrize(
    "mutation",
    [
        "extra-field",
        "text",
        "nan",
        "infinity",
        "boolean",
        "sample",
        "missing-row",
        "fetches",
    ],
)
def test_measurements_reject_arbitrary_text_nonfinite_or_unknown_fields(
    mutation, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "MINEAGENT_SQL_CANDIDATE_MEASUREMENTS_DIR", "var/sql-candidate-proof"
    )
    samples = measurements("expanded")
    if mutation == "extra-field":
        samples[0]["password"] = "do-not-export"
    elif mutation in {"text", "nan", "infinity", "boolean"}:
        samples[0]["write_ms"] = {
            "text": "do-not-export",
            "nan": float("nan"),
            "infinity": float("inf"),
            "boolean": True,
        }[mutation]
    elif mutation == "sample":
        samples[0]["sample"] = 7
    elif mutation == "missing-row":
        samples.pop()
    else:
        samples[0]["checked_fetches"]["unexpected"] = 32
    with pytest.raises(AssertionError):
        export_component_measurements("expanded", samples)
    assert not Path("var/sql-candidate-proof").exists()


def test_measurements_reject_an_unapproved_artifact_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "MINEAGENT_SQL_CANDIDATE_MEASUREMENTS_DIR", str(tmp_path / "elsewhere")
    )
    with pytest.raises(AssertionError):
        export_component_measurements("minimal", measurements("minimal"))
    assert not (tmp_path / "elsewhere").exists()


@pytest.mark.parametrize(
    ("component", "filename", "test_name"),
    [
        (
            "minimal",
            "test_row_validation_invariants.py",
            "test_component_nonconstant_read_savings_and_write_overhead",
        ),
        (
            "expanded",
            "test_row_validation_content.py",
            "test_component_extended_nonconstant_cost_and_write_overhead",
        ),
    ],
)
def test_benchmarks_export_validated_measurements_and_keep_uncaptured_output(
    component, filename, test_name
):
    import ast

    tree = ast.parse((Path("tests/integration") / filename).read_text())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == test_name
    )
    exported = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "export_component_measurements"
    ]
    assert len(exported) == 1
    assert exported[0].args[0].value == component
    assert exported[0].args[1].id == "samples"
    capture = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.With)
        and any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Attribute)
            and item.context_expr.func.attr == "disabled"
            and isinstance(item.context_expr.func.value, ast.Name)
            and item.context_expr.func.value.id == "capsys"
            for item in node.items
        )
    ]
    assert len(capture) == 1
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "print"
        for node in ast.walk(capture[0])
    )
    dumped = [
        node
        for node in ast.walk(capture[0])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "dumps"
    ]
    assert len(dumped) == 1 and dumped[0].args[0].id == "report"
    assert {key.arg: ast.literal_eval(key.value) for key in dumped[0].keywords}[
        "allow_nan"
    ] is False
    assert (
        "MINEAGENT_SQL_CANDIDATE_MEASUREMENTS_DIR: var/sql-candidate-proof"
        in Path(".github/workflows/mineagent-sql-candidates.yml").read_text()
    )
