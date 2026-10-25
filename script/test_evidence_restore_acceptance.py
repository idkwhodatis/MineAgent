"""No skip, collection, duplicate, or failed teardown can satisfy the fast gate."""

from xml.etree import ElementTree as ET

import pytest

from script.evidence_restore_acceptance import REQUIRED_CASE, verify_report


def write(path, *, outcome=None, name=None, count=1):
    root = ET.Element("testsuites")
    suite = ET.SubElement(
        root, "testsuite", tests=str(count), failures="0", errors="0", skipped="0"
    )
    for _ in range(count):
        case = ET.SubElement(
            suite, "testcase", classname=REQUIRED_CASE[0], name=name or REQUIRED_CASE[1]
        )
        if outcome:
            ET.SubElement(case, outcome, message="private fixture credentials")
    ET.ElementTree(root).write(path)
    return path


def test_exact_completed_case_and_zero_exit_are_required(tmp_path):
    path = tmp_path / "report.xml"
    verify_report(write(path), 0)
    for status in (1, 2, 5, 124):
        with pytest.raises(ValueError):
            verify_report(write(path), status)
    for kwargs in (
        {"count": 0},
        {"count": 2},
        {"name": "collection_only"},
        *({"outcome": value} for value in ("skipped", "error", "failure")),
    ):
        with pytest.raises(ValueError):
            verify_report(write(path, **kwargs), 0)


def test_unsafe_or_missing_report_is_not_evidence(tmp_path):
    path = tmp_path / "report.xml"
    with pytest.raises(ValueError):
        verify_report(path, 0)
    write(path)
    link = tmp_path / "link.xml"
    link.symlink_to(path)
    with pytest.raises(ValueError):
        verify_report(link, 0)
    path.write_text("<!DOCTYPE x><testsuites/>")
    with pytest.raises(ValueError):
        verify_report(path, 0)


def test_summary_never_exports_private_failure_text(tmp_path, monkeypatch):
    from script.evidence_restore_acceptance import main

    monkeypatch.chdir(tmp_path)
    path = write(tmp_path / "private.xml", outcome="failure")
    monkeypatch.setattr("sys.argv", ["gate", "--report", str(path), "--exit-code", "1"])
    assert main() == 1
    output = (
        tmp_path / "var/ci-evidence/completed-evidence-restore-junit.xml"
    ).read_text()
    assert "private fixture credentials" not in output
    assert REQUIRED_CASE[1] not in output
    assert "failure" in output


def test_workflow_requires_frozen_real_stack_and_exact_case():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/mineagent-evidence-restore.yml").read_text()
    assert "postgres:18.6" in workflow
    for runtime in (
        "backend",
        "runtimes/data",
        "runtimes/research",
        "runtimes/evaluator",
    ):
        assert f"--require-hashes -r {runtime}/requirements.lock" in workflow
    assert 'TRADEGRAD_REQUIRE_EVIDENCE_RESTORE: "1"' in workflow
    assert "DATA_PROVIDER_MODE: fixture" in workflow
    assert 'MODEL_PROFILES_ENABLED: "false"' in workflow
    assert '[[ "$image" =~ ^sha256:[0-9a-f]{64}$ ]]' in workflow
    assert "tests/system/test_research_evidence_restore.py -q" in workflow
    assert '--exit-code "$status"' in workflow
    assert "script/ci_pg_client.py" in workflow
    assert "--collect-only" not in workflow
    uploads = workflow.split(
        "name: Preserve only sanitized outcome and capacity evidence", 1
    )[1]
    assert "private" not in uploads
    assert "var/ci-evidence/completed-evidence-restore-junit.xml" in uploads
