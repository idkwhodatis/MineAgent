"""Candidate CI boundary and evidence contracts, not actual PostgreSQL results."""

import ast
import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from sqlalchemy import text

from script import sql_candidate_proof as proof

WORKFLOW = Path(".github/workflows/mineagent-sql-candidates.yml")
EXPECTED = ["tests/example.py::test_first", "tests/example.py::test_second"]


def xml_report():
    root = ET.Element("testsuites")
    suite = ET.SubElement(
        root, "testsuite", tests="2", failures="0", errors="0", skipped="0"
    )
    for index, name in enumerate(("test_second", "test_first")):
        case = ET.SubElement(
            suite, "testcase", classname="tests.example", name=name, time=str(index + 1)
        )
        ET.SubElement(case, "system-out").text = "Do not export private diagnostics"
    return root


def test_workflow_is_read_only_loopback_disposable_and_bounded():
    source = WORKFLOW.read_text()
    for required in (
        "branches: [master]",
        "contents: read",
        "persist-credentials: false",
        "timeout-minutes: 25",
        'python-version: "3.12"',
        "postgres:18.6",
        "--require-hashes -r backend/requirements.lock",
        "--publish 127.0.0.1::5432",
        "--memory 2g --memory-swap 2g --cpus 2 --pids-limit 128",
        "-c statement_timeout=10000 -c lock_timeout=5000",
        "-c track_functions=pl",
        'MINEAGENT_SQL_COST_BENCHMARK: "1"',
        "mineagent-sql-candidate-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}",
        "--cidfile",
        "if: always()",
        "{{.Id}}|{{.Name}}|",
        'docker rm --force --volumes "$cid"',
        "--source-evidence",
        "path: var/sql-candidate-proof/",
        'MODEL_PROFILES_ENABLED: "false"',
    ):
        assert required in source
    for forbidden in (
        "docker build",
        "docker push",
        "qlib",
        "sudo",
        "continue-on-error",
        "pull_request_target",
        "${{ secrets.",
        "alembic upgrade",
        "  workflow_run:",
    ):
        assert forbidden not in source
    assert source.count("docker run ") == 1
    assert source.count("--group ") == 3
    assert source.count("--collect-only") == 3
    assert source.count("--junitxml=") == 3
    assert source.count("if: always()") == 2


def test_workflow_selects_all_candidates_without_deselection_and_tracks_inputs():
    source = WORKFLOW.read_text()
    for forbidden in ("--ignore", "--deselect", "-k ", "-m postgres", "--maxfail"):
        assert forbidden not in source
    for path in (
        "backend/alembic/**",
        "script/schema_keyset_candidate.py",
        "script/sql_candidate_proof.py",
        "tests/structured/*sql_candidate*",
        "tests/integration/conftest.py",
        "tests/integration/test_row_validation*",
        "tests/integration/test_schema_keyset_candidate.py",
    ):
        assert f'      - "{path}"' in source
    for group in ("source", "keyset", "rows"):
        for node in proof.inventory(group):
            assert node.split("::")[0] in source


def test_candidates_are_not_imported_by_runtime_or_migrations_and_timeout_is_unchanged():
    candidates = (
        "row_validation_invariant_sql",
        "row_validation_content_sql",
        "schema_keyset_candidate",
    )
    permitted = {
        Path("backend/app/row_validation_invariant_sql.py"),
        Path("backend/app/row_validation_content_sql.py"),
    }
    for base in ("backend", "packages", "runtimes", "workers"):
        for path in Path(base).rglob("*.py"):
            if path in permitted:
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [alias.name for alias in node.names]
                    if isinstance(node, ast.ImportFrom):
                        names.append(node.module or "")
                    assert not any(
                        part in name.split(".") for name in names for part in candidates
                    ), path
    assert (
        "statement_timeout=10000" in Path("tests/integration/conftest.py").read_text()
    )
    assert (
        'REQUIRED_SCHEMA_REVISION = "032f_shared_validation_cost"'
        in Path("backend/app/core/db.py").read_text()
    )


def test_keyset_harness_uses_installed_config_and_exact_authoring_projection(request):
    request.config.addinivalue_line(
        "markers", "postgres: requires a real PostgreSQL TEST_DATABASE_URL"
    )
    from tests.integration.test_schema_keyset_candidate import frozen_rule, workloads

    corpus = {label: (value, rule) for label, value, rule in workloads()}
    assert len(corpus) == 9
    value, rule = corpus["oe_config"]
    assert rule == frozen_rule(
        "030_declared_upload_basket.py", "mineagent_oes_contract_v030_shape", "config"
    )
    for key in ("maximum_weight", "maximum_gross_weight"):
        field = rule["$defs"]["StructuredProgram"]["properties"][key]
        assert field["exclusiveMinimum"] == 0 and field["maximum"] == 1
        assert 0 < value["search"]["domain"]["seed_program"][key] <= 1
    value, rule = corpus["structured_input"]
    manifest = rule["$defs"]["StrategyManifest"]
    assert "authoring" not in manifest["properties"]
    assert "authoring" not in manifest["required"]
    assert "authoring" not in value["engine"]["manifest"]


def test_keyset_collation_probe_binds_json_instead_of_parsing_colon_as_sql_parameter():
    tree = ast.parse(
        Path("tests/integration/test_schema_keyset_candidate.py").read_text()
    )
    probes = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "SELECT pg_collation_for" in node.value
    ]
    assert len(probes) == 1
    assert set(text(probes[0])._bindparams) == {"v"}
    assert "CAST(:v AS jsonb)" in probes[0]


@pytest.mark.parametrize("change", ["missing", "duplicate", "substitution"])
def test_collection_rejects_inexact_named_inventory(change):
    actual = EXPECTED.copy()
    if change == "missing":
        actual.pop()
    elif change == "duplicate":
        actual.append(actual[0])
    else:
        actual[-1] = "tests/example.py::test_impostor"
    with pytest.raises(AssertionError, match="collection differs"):
        proof.check_collection(EXPECTED, "\n".join(actual))


@pytest.mark.parametrize("outcome", ["failure", "error", "skipped", "aggregate"])
def test_junit_rejects_every_failure_error_skip_and_aggregate(outcome):
    root = xml_report()
    if outcome == "aggregate":
        root.find("testsuite").set("errors", "1")
    else:
        ET.SubElement(root.find(".//testcase"), outcome)
    with pytest.raises(AssertionError):
        proof.check_junit(EXPECTED, ET.tostring(root))


@pytest.mark.parametrize("change", ["missing", "duplicate", "substitution"])
def test_junit_rejects_missing_duplicate_or_wrong_named_case(change):
    root = xml_report()
    suite = root.find("testsuite")
    if change == "missing":
        suite.remove(suite.find("testcase"))
    elif change == "duplicate":
        suite.append(suite.find("testcase"))
    else:
        suite.find("testcase").set("name", "test_impostor")
    with pytest.raises(AssertionError, match="named inventory differs"):
        proof.check_junit(EXPECTED, ET.tostring(root))


def test_success_evidence_is_named_outcome_only_and_duration_matches_case():
    proof.check_collection(EXPECTED, "\n".join(EXPECTED) + "\n2 tests collected")
    result = proof.check_junit(EXPECTED, ET.tostring(xml_report()))
    assert result == [
        {"node": EXPECTED[0], "outcome": "passed", "seconds": "2"},
        {"node": EXPECTED[1], "outcome": "passed", "seconds": "1"},
    ]
    assert "private diagnostics" not in json.dumps(result)


def test_inventory_keeps_all_44_pg_cases_and_component_benchmark_separate():
    manifest = json.loads(proof.MANIFEST.read_text())
    assert set(manifest) == {"source", "keyset", "rows"}
    assert len(proof.inventory("keyset")) == 11
    assert len(proof.inventory("rows")) == 33
    assert len(proof.inventory("source")) == 56
    assert (
        "tests/integration/test_schema_keyset_candidate.py::test_opt_in_table_backed_workload_benchmark"
        in manifest["keyset"]
    )
    assert all("tests/integration/" not in node for node in manifest["source"])
    assert "not production or native deadline acceptance" in proof.SCOPE


def test_catalog_normalization_only_removes_documented_oids_and_generated_schema():
    from script.sql_candidate_catalog import OID_FIELDS, normalized

    schema = "tradegrad_test_" + "a" * 32
    raw = {
        "schema": schema,
        "function_oid": 12345,
        "identity": f"{schema}.mineagent_rd_schema(jsonb,jsonb,jsonb,integer)",
        "flags": {"conenforced": True, "convalidated": True, "condeferrable": True},
        "definition": 'CHECK (value COLLATE "C" IS NOT NULL)',
        "rows": [{"attnum": 2, "column_type_oid": 25, "type_identity": "text"}],
    }
    result = normalized(raw, schema)
    assert result == {
        "schema": "@SCHEMA@",
        "identity": "@SCHEMA@.mineagent_rd_schema(jsonb,jsonb,jsonb,integer)",
        "flags": raw["flags"],
        "definition": raw["definition"],
        "rows": [{"attnum": 2, "type_identity": "text"}],
    }
    assert all(key.endswith("_oid") for key in OID_FIELDS)
    assert raw["function_oid"] == 12345
    with pytest.raises(AssertionError):
        normalized(raw, "public")
    source = WORKFLOW.read_text()
    assert "MINEAGENT_SQL_CANDIDATE_CATALOG_DIR: var/sql-candidate-proof" in source
    assert (
        "files=(tests/integration/test_sql_candidate_catalog.py tests/integration/test_row_validation_invariants.py tests/integration/test_row_validation_content.py)"
        in source
    )
