"""The dedicated Stage1 gate cannot confuse collection/skips with acceptance."""

import ast
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from script.factor_browser_acceptance import (
    REQUIRED_BROWSER_CASES,
    REQUIRED_FACTOR_MIGRATION_CASES,
    REQUIRED_IMPORT_BROWSER_CASE,
    REQUIRED_ISOLATION_CASES,
    REQUIRED_MIGRATION_CASES,
    REQUIRED_OUTBOX_CASES,
    REQUIRED_PG_CASES,
    REQUIRED_RESTORE_CASES,
    REQUIRED_RUN_IDENTITY_CASES,
    node_environment,
    validated_source,
    verify_report,
)


def write_report(path, identities, outcome=None):
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite", tests=str(len(identities)))
    for classname, name in identities:
        case = ET.SubElement(suite, "testcase", classname=classname, name=name)
        if outcome:
            ET.SubElement(case, outcome)
    ET.ElementTree(root).write(path)
    return path


def browser_identities():
    return [("factor-workbench.spec.ts", name) for name in REQUIRED_BROWSER_CASES]


def test_exact_browser_inventory_passes_and_missing_duplicate_or_skipped_fails(
    tmp_path,
):
    path = tmp_path / "report.xml"
    identities = browser_identities()
    verify_report(write_report(path, identities), "browser")
    for bad in (
        identities[:-1],
        identities + identities[:1],
        [("other.spec.ts", name) for _, name in identities],
    ):
        with pytest.raises(ValueError):
            verify_report(write_report(path, bad), "browser")
    verify_report(
        write_report(
            path,
            identities + [("factor-workbench.spec.ts", REQUIRED_IMPORT_BROWSER_CASE)],
        ),
        "browser-import",
    )
    with pytest.raises(ValueError):
        verify_report(write_report(path, identities), "browser-import")
    for outcome in ("skipped", "failure", "error"):
        with pytest.raises(ValueError):
            verify_report(write_report(path, identities, outcome), "browser")


def test_pg_and_isolation_require_real_case_inventory(tmp_path):
    path = tmp_path / "report.xml"
    pg = [("tests.integration.test_factor_library", name) for name in REQUIRED_PG_CASES]
    pg += [
        ("tests.integration.test_factor_restore", name)
        for name in REQUIRED_RESTORE_CASES
    ]
    pg += list(REQUIRED_MIGRATION_CASES)
    pg += [("tests.integration.test_outbox", name) for name in REQUIRED_OUTBOX_CASES]
    pg += [
        ("tests.integration.test_factor_migrations", name)
        for name in REQUIRED_FACTOR_MIGRATION_CASES
    ]
    pg += [
        (
            "tests.integration.test_run_identity",
            name + (f"[{index}]" if count > 1 else ""),
        )
        for name, count in REQUIRED_RUN_IDENTITY_CASES.items()
        for index in range(count)
    ]
    verify_report(write_report(path, pg), "postgres")
    # Every exact required identity, not just the total, is admission evidence.
    for index in range(len(pg)):
        with pytest.raises(ValueError):
            verify_report(write_report(path, pg[:index] + pg[index + 1 :]), "postgres")
    for outcome in ("skipped", "failure", "error"):
        with pytest.raises(ValueError):
            verify_report(write_report(path, pg, outcome), "postgres")
    with pytest.raises(ValueError):
        verify_report(write_report(path, pg + pg[:1]), "postgres")
    isolation = [
        (
            "tests.isolation.test_factor_parser",
            name + (f"[{index}]" if count > 1 else ""),
        )
        for name, count in REQUIRED_ISOLATION_CASES.items()
        for index in range(count)
    ]
    verify_report(write_report(path, isolation), "isolation")
    with pytest.raises(ValueError):
        verify_report(write_report(path, isolation[:-1]), "isolation")


def test_report_symlink_entity_and_empty_are_not_evidence(tmp_path):
    path = tmp_path / "report.xml"
    for raw in (
        b"<testsuites/>",
        b"<!DOCTYPE x><testsuites/>",
        b'<!ENTITY x "bad"><testsuites/>',
    ):
        path.write_bytes(raw)
        with pytest.raises(ValueError):
            verify_report(path, "browser")
    link = tmp_path / "link.xml"
    link.symlink_to(path)
    with pytest.raises(ValueError):
        verify_report(link, "browser")


def test_no_database_model_docker_or_ambient_node_hooks_reach_browser():
    source = {
        "PATH": "/bin",
        "HOME": "/home/fixture",
        "E2E_EMAIL": "synthetic@example.com",
        "E2E_PASSWORD": "fixture-only",
        "DATABASE_URL": "private",
        "SECRET_KEY": "private",
        "MINEAGENT_FACTOR_PARSER_IMAGE": "private",
        "OPENAI_API_KEY": "private",
        "NODE_OPTIONS": "private",
        "HTTP_PROXY": "private",
    }
    environment = node_environment(source)
    assert environment["E2E_FACTOR_ACCEPTANCE"] == "1"
    assert environment["E2E_LIVE_PROVIDERS"] == "0"
    assert environment["E2E_PASSWORD"] == "fixture-only"
    assert not any(
        key in environment
        for key in (
            "DATABASE_URL",
            "SECRET_KEY",
            "MINEAGENT_FACTOR_PARSER_IMAGE",
            "OPENAI_API_KEY",
            "NODE_OPTIONS",
            "HTTP_PROXY",
        )
    )


def test_nonhosted_or_disabled_fixture_cannot_launch(tmp_path):
    for source in (
        {},
        {
            "GITHUB_ACTIONS": "true",
            "MINEAGENT_REQUIRE_FACTOR_BROWSER": "1",
            "FACTOR_LIBRARY_ENABLED": "false",
            "DATA_PROVIDER_MODE": "fixture",
        },
        {
            "GITHUB_ACTIONS": "true",
            "MINEAGENT_REQUIRE_FACTOR_BROWSER": "1",
            "FACTOR_LIBRARY_ENABLED": "true",
            "DATA_PROVIDER_MODE": "live",
        },
    ):
        with pytest.raises(ValueError):
            validated_source(source, tmp_path)


def test_test_selectors_and_required_inventory_remain_synchronized():
    root = Path(__file__).resolve().parents[2]
    spec = (root / "frontend/tests/e2e/factor-workbench.spec.ts").read_text()
    for name in REQUIRED_BROWSER_CASES:
        assert name in spec
    workflow = (root / ".github/workflows/mineagent-factors.yml").read_text()
    assert "postgres:18.6" in workflow
    assert "--require-hashes" in workflow
    assert 'MINEAGENT_FACTOR_ISOLATION_REQUIRED: "1"' in workflow
    assert 'FACTOR_LIBRARY_ENABLED: "true"' in workflow
    assert "--verify postgres" in workflow and "--verify isolation" in workflow
    assert "python -m script.factor_browser_acceptance" in workflow
    assert "--collect-only" not in workflow
    for module, name in REQUIRED_MIGRATION_CASES:
        selector = module.replace(".", "/") + ".py::" + name
        assert selector in workflow
    assert "tests/integration/test_factor_migrations.py" in workflow
    assert "tests/integration/test_run_identity.py" in workflow
    for selector in (
        "tests/integration/test_outbox.py",
        "backend/tests/test_neutral_run_identity.py",
        "backend/tests/test_schema_capabilities.py",
        "tests/research_runtime",
    ):
        assert selector in workflow
    assert (
        "test_real_factor_cold_restore_and_legacy_upgrade[011_note_pin_trigger]"
        in REQUIRED_RESTORE_CASES
    )
    assert (
        "test_real_factor_cold_restore_and_legacy_upgrade[013_factor_import]"
        in REQUIRED_RESTORE_CASES
    )
    assert (
        "test_real_factor_cold_restore_and_legacy_upgrade[014_run_identity]"
        in REQUIRED_RESTORE_CASES
    )


def test_create_diagnostics_exports_only_closed_states(tmp_path):
    from script.factor_browser_acceptance import create_diagnostics

    path = tmp_path / "report.xml"
    root = ET.Element("testsuite")
    case = ET.SubElement(root, "testcase", name="private-name")
    failure = ET.SubElement(case, "failure")
    failure.text = (
        "private-token private-source "
        "FACTOR_CREATE_DIAGNOSTIC_V1:phase=response;status=422;requests=1;"
        "form=valid;alert=yes;saving=no "
        "FACTOR_CREATE_DIAGNOSTIC_V1:phase=private;status=200;requests=1;"
        "form=valid;alert=yes;saving=no "
        "FACTOR_CREATE_DIAGNOSTIC_V1:phase=response;status=999;requests=1;"
        "form=valid;alert=yes;saving=no"
    )
    ET.ElementTree(root).write(path)
    assert create_diagnostics(path) == [
        {
            "case_index": 0,
            "phase": "response",
            "http_status": 422,
            "requests": 1,
            "form": "valid",
            "alert": "yes",
            "saving": "no",
        }
    ]
    assert "private" not in str(create_diagnostics(path))
    path.write_text("<!DOCTYPE x><testsuite/>")
    with pytest.raises(ValueError):
        create_diagnostics(path)


def test_factor_migration_inventory_matches_every_guard_test():
    root = Path(__file__).resolve().parents[2]
    module = ast.parse(
        (root / "tests/integration/test_factor_migrations.py").read_text()
    )
    names = {
        node.name
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    }
    assert names == {name.split("[", 1)[0] for name in REQUIRED_FACTOR_MIGRATION_CASES}


def test_run_identity_inventory_matches_every_parameterized_guard():
    root = Path(__file__).resolve().parents[2]
    module = ast.parse((root / "tests/integration/test_run_identity.py").read_text())
    actual = {}
    for node in module.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
            continue
        count = 1
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "parametrize"
            ):
                assert len(decorator.args) >= 2
                values = decorator.args[1]
                assert isinstance(values, (ast.List, ast.Tuple))
                count *= len(values.elts)
        actual[node.name] = count
    assert actual == REQUIRED_RUN_IDENTITY_CASES


def test_outbox_inventory_matches_every_recovery_case():
    root = Path(__file__).resolve().parents[2]
    module = ast.parse((root / "tests/integration/test_outbox.py").read_text())
    actual = set()
    for node in module.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
            continue
        suffixes = [""]
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "parametrize"
            ):
                values = decorator.args[1]
                assert isinstance(values, (ast.List, ast.Tuple))
                suffixes = [
                    prefix + "[" + ast.literal_eval(value) + "]"
                    for prefix in suffixes
                    for value in values.elts
                ]
        actual.update(node.name + suffix for suffix in suffixes)
    assert actual == set(REQUIRED_OUTBOX_CASES)
