from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from script.neutral_acceptance import (
    failure_outcomes,
    report_root,
    required_cases,
    validate_source,
    verify_report,
)


def report(tmp_path, profile="isolation"):
    expected = required_cases(profile)
    root = ET.Element("testsuites")
    suite = ET.SubElement(
        root,
        "testsuite",
        tests=str(len(expected)),
        failures="0",
        errors="0",
        skipped="0",
    )
    for module, name in expected:
        ET.SubElement(suite, "testcase", classname=module, name=name)
    path = tmp_path / "report.xml"
    ET.ElementTree(root).write(path)
    return path, root, suite


def save(path, root):
    ET.ElementTree(root).write(path)


def test_exact_real_inventory_passes(tmp_path):
    path, _, _ = report(tmp_path)
    verify_report(path, "isolation", 0)


@pytest.mark.parametrize("outcome", ["failure", "error", "skipped"])
def test_skips_and_failed_cases_never_count_as_real_acceptance(tmp_path, outcome):
    path, root, suite = report(tmp_path)
    ET.SubElement(suite[0], outcome, message="private token")
    save(path, root)
    with pytest.raises(ValueError):
        verify_report(path, "isolation", 0)
    diagnostics = str(failure_outcomes(path, "isolation"))
    assert outcome in diagnostics and "private" not in diagnostics


@pytest.mark.parametrize(
    "mutation",
    ["missing", "duplicate", "foreign", "wrong_total", "nested", "extra_suite"],
)
def test_partial_duplicate_and_untrusted_case_reports_fail_closed(tmp_path, mutation):
    path, root, suite = report(tmp_path)
    if mutation == "missing":
        suite.remove(suite[0])
    elif mutation == "duplicate":
        suite[1].attrib.update(suite[0].attrib)
    elif mutation == "foreign":
        suite[0].set("classname", "private.token")
    elif mutation == "wrong_total":
        suite.set("tests", "999")
    elif mutation == "nested":
        case = suite[0]
        suite.remove(case)
        ET.SubElement(suite, "wrapper").append(case)
    else:
        ET.SubElement(root, "testsuite")
    save(path, root)
    with pytest.raises(ValueError):
        verify_report(path, "isolation", 0)


def test_success_report_cannot_hide_failed_process_or_unknown_profile(tmp_path):
    path, _, _ = report(tmp_path)
    with pytest.raises(ValueError):
        verify_report(path, "isolation", 1)
    with pytest.raises(ValueError):
        verify_report(path, "unknown", 0)


def test_report_framing_symlinks_and_size_are_rejected(tmp_path, monkeypatch):
    path, _, _ = report(tmp_path)
    link = tmp_path / "link.xml"
    link.symlink_to(path)
    with pytest.raises(ValueError):
        report_root(link)
    monkeypatch.setattr("script.neutral_acceptance.MAX_REPORT_BYTES", 2)
    with pytest.raises(ValueError):
        report_root(path)
    monkeypatch.setattr("script.neutral_acceptance.MAX_REPORT_BYTES", 10000)
    path.write_text("<!DOCTYPE testsuites><testsuites/>")
    with pytest.raises(ValueError):
        report_root(path)


def test_failure_projection_cannot_export_unknown_case_or_raw_payload(tmp_path):
    path, root, suite = report(tmp_path)
    ET.SubElement(suite[0], "failure").text = "private token /private/path body"
    suite[1].set("name", "private-secret")
    ET.SubElement(suite[1], "error").text = "private token"
    save(path, root)
    value = failure_outcomes(path, "isolation")
    assert len(value) == 1
    assert "private" not in str(value)
    assert set(value[0]) == {"source", "case", "outcome"}


def test_hosted_explicit_fixture_admission_is_required():
    source = {
        "GITHUB_ACTIONS": "true",
        "MINEAGENT_REQUIRE_NEUTRAL_ACCEPTANCE": "1",
        "MINEAGENT_RESEARCH_ISOLATION_REQUIRED": "1",
        "DATA_PROVIDER_MODE": "fixture",
        "E2E_LIVE_PROVIDERS": "0",
        "RUN_BACKUP_RESTORE_DRILL": "1",
        "MINEAGENT_RESEARCH_SUPERVISOR_IMAGE": "sha256:" + "a" * 64,
        "TEST_DATABASE_URL": "fixture",
        "DATABASE_URL": "fixture",
    }
    validate_source(source)
    for key in source:
        with pytest.raises(ValueError):
            validate_source({k: v for k, v in source.items() if k != key})


def test_workflow_preserves_frozen_env_and_exact_fixture_ownership():
    path = (
        Path(__file__).resolve().parents[2] / ".github/workflows/mineagent-neutral.yml"
    )
    text = path.read_text()
    for required in [
        "--require-hashes",
        "postgres:18.6",
        "--cidfile",
        "neutral-postgres.cid",
        "pg_dump",
        "io.mineagent.managed=mineagent-research-supervisor-v1",
        "--profile postgres",
        "--profile isolation",
        "--profile restore",
    ]:
        assert required in text
    assert "docker system prune" not in text
    assert "persist-credentials: false" in text
    assert "var/ci-evidence/neutral-*-junit.xml" in text


@pytest.mark.parametrize(
    "run_failed,cleanup_ok", [(False, True), (True, True), (False, False)]
)
def test_runner_requires_process_success_and_cleanup_before_erasing_private_files(
    tmp_path, monkeypatch, capsys, run_failed, cleanup_ok
):
    import script.neutral_acceptance as module

    private = tmp_path / "private"
    private.mkdir()
    path, _, _ = report(private)
    monkeypatch.setattr(
        module, "__file__", str(tmp_path / "script/neutral_acceptance.py")
    )
    monkeypatch.setattr(module, "validate_source", lambda source: None)
    monkeypatch.setattr(module.tempfile, "mkdtemp", lambda **kwargs: str(private))
    monkeypatch.setattr(
        module.sys, "argv", ["neutral_acceptance", "--profile", "isolation"]
    )
    monkeypatch.setenv("PYTEST_ADDOPTS", "--collect-only")
    events = []

    class Owner:
        started = False

        def __init__(self, deadline):
            pass

        def contain(self):
            self.started = True

        def run(self, arguments, **kwargs):
            assert "--collect-only" not in arguments
            assert "PYTEST_ADDOPTS" not in kwargs["env"]
            assert kwargs["env"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
            assert all(node in arguments for node in module.REQUIRED["isolation"])
            events.append("run")
            if run_failed:
                raise RuntimeError("private token")

        def cleanup(self):
            assert path.exists()
            events.append("cleanup")
            return cleanup_ok

    monkeypatch.setattr(module, "ProcessOwner", Owner)
    assert module.main() == (0 if cleanup_ok and not run_failed else 1)
    assert events == ["run", "cleanup"]
    assert private.exists() is (not cleanup_ok)
    assert "private token" not in capsys.readouterr().out
    assert (tmp_path / "var/ci-evidence/neutral-isolation-junit.xml").is_file()


def test_every_authored_real_case_is_pinned_in_exact_profile():
    """Compare actual pytest IDs, including shared corpus/derived parameters.

    Collection does not execute fixtures, create a database, or satisfy any
    PostgreSQL/isolation/restore acceptance. Their no-skip gates remain separate.
    """
    import subprocess
    import sys

    from script.neutral_acceptance import REQUIRED, REQUIRED_SOURCES

    root = Path(__file__).resolve().parents[2]
    sources = sorted(set().union(*REQUIRED_SOURCES.values()))
    collected = subprocess.run(
        [sys.executable, "-m", "pytest", *sources, "--collect-only", "-q"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert collected.returncode == 0, "Required real-test collection failed"
    assert len(collected.stdout.encode()) <= 2_000_000
    nodes = [
        line
        for line in collected.stdout.splitlines()
        if any(line.startswith(source + "::") for source in sources)
    ]
    assert len(nodes) == len(set(nodes)), "Duplicate required test identity"
    for profile, profile_sources in REQUIRED_SOURCES.items():
        actual = {
            node for node in nodes if node.split("::", 1)[0] in profile_sources
        }
        assert actual == set(REQUIRED[profile])


def test_diagnostics_project_only_existing_source_lines_and_closed_classes(tmp_path):
    path, root, suite = report(tmp_path)
    ET.SubElement(suite[0], "error").text = (
        "backend/app/research_lifecycle.py:12: DomainError private token "
        "backend/app/research_lifecycle.py:999999: PrivateSecretException "
        "tests/private_secrets.py:1: token "
        "../../../private/user.py:1: body"
    )
    save(path, root)
    diagnostics = failure_outcomes(path, "isolation")
    assert diagnostics[0]["source_frames"] == [
        {"file": "backend/app/research_lifecycle.py", "line": 12}
    ]
    assert diagnostics[0]["exception_classes"] == ["DomainError"]
    assert "private" not in str(diagnostics)
    assert "token" not in str(diagnostics)
