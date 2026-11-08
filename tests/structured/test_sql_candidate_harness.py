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


def test_every_keyset_workload_uses_its_exact_installed_frozen_rule(request):
    request.config.addinivalue_line("markers", "postgres: requires PostgreSQL")
    from backend.app import row_validation_content_sql as installed
    from tests.integration.test_schema_keyset_candidate import (
        frozen_function,
        frozen_rule,
        workloads,
    )

    expected = {
        "rd_config": (
            "026_rd_seeded_submissions.py",
            "mineagent_rd_provider_payload",
            "config",
        ),
        "rd_workflow_config": (
            "029_research_workflow.py",
            "mineagent_rd_workflow_config",
            None,
        ),
        **{
            f"oe_{kind}": (
                "030_declared_upload_basket.py",
                "mineagent_oes_contract_v030_shape",
                kind,
            )
            for kind in ("config", "request", "candidate", "measurement")
        },
        "oe_bootstrap": (
            "031_workflow_structured_stage.py",
            "mineagent_oes_epoch_check",
            None,
        ),
        "oe_workflow_bootstrap": (
            "031_workflow_structured_stage.py",
            "mineagent_oe_workflow_shape",
            "bootstrap",
        ),
        "structured_input": (
            "027a_contract_repairs.py",
            "mineagent_structured_contract",
            "input",
        ),
    }
    corpus = {label: (value, rule) for label, value, rule in workloads()}
    assert corpus.keys() == expected.keys()
    for label, (filename, function, kind) in expected.items():
        assert frozen_function(filename, function) == installed.original(function), (
            label
        )
        assert corpus[label][1] == frozen_rule(filename, function, kind), label
    # Legacy DTOs take 031's fallback, then the unchanged installed 030 helper.
    assert (
        corpus["oe_config"][0]["schema_version"]
        == "mineagent.oe-structured-provider-run.v1"
    )
    assert (
        corpus["oe_request"][0]["schema_version"]
        == "mineagent.oe-structured-provider-submission.v1"
    )
    dispatch = frozen_function(
        "031_workflow_structured_stage.py", "mineagent_oes_contract"
    )
    assert (
        "RETURN @SCHEMA@.mineagent_oes_contract_before_workflow(kind,v) IS TRUE;"
        in dispatch
    )
    delegate = frozen_function(
        "031_workflow_structured_stage.py", "mineagent_oes_contract_before_workflow"
    )
    assert delegate == installed.original("mineagent_oes_contract_before_workflow")
    assert (
        "RETURN @SCHEMA@.mineagent_oes_contract_v030_shape(kind,v) IS TRUE;" in delegate
    )
    assert (
        corpus["oe_config"][0]["fixed_inputs"]["market_qualification"]
        == "synthetic_by_construction"
    )
    assert corpus["oe_measurement"][1] == frozen_rule(
        "028_oe_structured_search.py", "mineagent_oes_contract", "measurement"
    )


@pytest.mark.parametrize(
    ("kind", "definition", "field"),
    [
        ("request", "StructuredSeedParameters", "seed_parameters"),
        ("candidate", "StructuredProgram", "program"),
    ],
)
def test_real_half_weight_uses_installed_030_not_obsolete_028(
    kind, definition, field, request
):
    request.config.addinivalue_line("markers", "postgres: requires PostgreSQL")
    from tests.integration.test_schema_keyset_candidate import frozen_rule, workloads

    value, rule = next(
        (value, rule) for label, value, rule in workloads() if label == "oe_" + kind
    )
    assert value[field]["maximum_weight"] == 0.5
    old = frozen_rule("028_oe_structured_search.py", "mineagent_oes_contract", kind)
    for weight in ("maximum_weight", "maximum_gross_weight"):
        assert old["$defs"][definition]["properties"][weight]["minimum"] == 1
        current = rule["$defs"][definition]["properties"][weight]
        assert current["exclusiveMinimum"] == 0 and current["maximum"] == 1
        assert "minimum" not in current


def test_legacy_bootstrap_uses_031_spec_selected_rule_with_real_half_weight(request):
    request.config.addinivalue_line("markers", "postgres: requires PostgreSQL")
    from tests.integration.test_schema_keyset_candidate import (
        frozen_function,
        frozen_rule,
        workloads,
    )

    value, rule = next(
        (value, rule) for label, value, rule in workloads() if label == "oe_bootstrap"
    )
    assert value["schema_version"] == "mineagent.oe-structured-bootstrap.v2"
    assert value["spec"]["domain"]["seed_program"]["maximum_weight"] == 0.5
    assert rule == frozen_rule(
        "031_workflow_structured_stage.py", "mineagent_oes_epoch_check"
    )
    assert (
        rule["$defs"]["StructuredProgram"]["properties"]["maximum_weight"][
            "exclusiveMinimum"
        ]
        == 0
    )
    body = frozen_function(
        "031_workflow_structured_stage.py", "mineagent_oes_epoch_check"
    )
    assert (
        "CASE WHEN s.config->>'schema_version'='mineagent.oe-structured-provider-run.v2'"
        in body
    )
    assert (
        "ELSE @SCHEMA@.mineagent_rd_schema(j,rule,coalesce(rule->'$defs','{}'::jsonb),0)"
        in body
    )


def test_keyset_rule_selection_preserves_real_producer_dto_projection(
    monkeypatch, request
):
    request.config.addinivalue_line("markers", "postgres: requires PostgreSQL")
    from packages.factors.contracts import canonical_bytes
    from packages.openevolve_structured.provider_bootstrap import (
        ProviderStructuredBootstrap,
    )
    from tests.integration.test_schema_keyset_candidate import workloads
    from tests.openevolve import test_structured_persisted_identity as producer

    graph = producer.graph()
    monkeypatch.setattr(producer, "graph", lambda: graph)
    corpus = {label: value for label, value, _ in workloads()}
    spec, candidate = graph[2], graph[4]
    assert corpus["oe_config"] == spec.config
    assert corpus["oe_request"] == spec.request
    assert canonical_bytes(corpus["oe_request"]).decode() == spec.request_canonical
    assert (
        canonical_bytes(corpus["oe_candidate"]).decode()
        == candidate.candidate_canonical
    )
    bootstrap = ProviderStructuredBootstrap.model_validate_json(
        json.dumps(corpus["oe_bootstrap"])
    )
    assert bootstrap.model_dump(mode="json") == corpus["oe_bootstrap"]
    assert corpus["oe_bootstrap"]["spec"] == spec.config["search"]


def test_pg18_benchmark_reads_database_collation_without_changing_measurement_query(
    request,
):
    import ast

    request.config.addinivalue_line("markers", "postgres: requires PostgreSQL")
    from sqlalchemy import text

    from tests.integration.test_schema_keyset_candidate import BENCHMARK_ENVIRONMENT_SQL

    assert BENCHMARK_ENVIRONMENT_SQL == (
        "SELECT version() AS version,d.datcollate AS lc_collate,"
        "current_setting('server_encoding') AS encoding,"
        "current_setting('track_functions') AS tracking "
        "FROM pg_catalog.pg_database AS d WHERE d.datname=current_database()"
    )
    assert not text(BENCHMARK_ENVIRONMENT_SQL)._bindparams
    tree = ast.parse(
        Path("tests/integration/test_schema_keyset_candidate.py").read_text()
    )
    benchmark = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "test_opt_in_table_backed_workload_benchmark"
    )
    calls = [
        node
        for node in ast.walk(benchmark)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "exec_driver_sql"
        and node.args
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "BENCHMARK_ENVIRONMENT_SQL"
    ]
    assert len(calls) == 1
