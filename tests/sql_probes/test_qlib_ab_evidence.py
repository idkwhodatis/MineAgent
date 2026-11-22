import json
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

from tests.sql_probes import qlib_ab_evidence as evidence

REV = "a" * 40
REPORT = {
    "policy": "explicit_opt_in",
    "method": "generation_boundary",
    "child": "first",
    "rpc_ordinal": 75,
    "overflow": False,
    "inclusive_overlapping": True,
    "transactions": [
        {
            "ordinal": 6,
            "publication": "proposal",
            "resource_scope": "objects",
            "publication_stage": "reserve_object",
            "completion": "commit",
            "components": [
                {
                    "component": "dbapi_commit",
                    "count": 1,
                    "failures": 0,
                    "total_ms": 21.5,
                    "max_ms": 21.5,
                },
                {
                    "component": "orm_flush",
                    "count": 1,
                    "failures": 0,
                    "total_ms": 7,
                    "max_ms": 7,
                },
            ],
        },
        {
            "ordinal": 8,
            "publication": "proposal",
            "resource_scope": "objects",
            "publication_stage": "finalize_object",
            "completion": "rollback",
            "components": [],
        },
    ],
}


@pytest.mark.parametrize("succeed", [True, False])
def test_real_pytest_junit_contains_closed_data_on_pass_and_failure(tmp_path, succeed):
    case = tmp_path / "tests/integration/test_rdagent_workflow_user_flow.py"
    case.parent.mkdir(parents=True)
    case.write_text(
        dedent(f"""
        import pytest

        @pytest.mark.parametrize("opt_in", [True], ids=["explicit-opt-in-two-children"])
        def test_normal_workflow_children_consume_frozen_inputs_and_real_numeric_results(opt_in, record_property):
            record_property("qlib_ascii_ab_source", "baseline")
            print("private canary")
            print({(evidence.PREFIX + json.dumps(REPORT))!r})
            if not {succeed!r}:
                record_property("qlib_ascii_ab_phase", "fixture_before_restore")
                pytest.fail("private failure")
            record_property("qlib_ascii_ab_restore", "baseline")
            record_property("qlib_ascii_ab_phase", "post_restore")
        """)
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            str(case.relative_to(tmp_path)),
            "--junitxml=private.xml",
            "-o",
            "junit_logging=system-out",
            "-o",
            "junit_log_passing_tests=true",
        ],
        cwd=tmp_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    assert result.returncode == (0 if succeed else 1)
    projected = evidence.project(tmp_path / "private.xml", "baseline", REV)
    assert projected["acceptance"] == ("passed" if succeed else "failure")
    assert projected["phase"] == ("accepted" if succeed else "fixture_before_restore")
    assert projected["source_attested"] and projected["restore_attested"] is succeed
    assert projected["commits"] == [
        {
            "child": "first",
            "method": "generation_boundary",
            "rpc": 75,
            "transaction": 6,
            "publication": "proposal",
            "stage": "reserve_object",
            "dbapi_count": 1,
            "dbapi_ms": 21.5,
        }
    ]
    assert "private" not in json.dumps(projected)
    with pytest.raises(ValueError):
        evidence.project(tmp_path / "private.xml", "ascii_reuse", REV)


def test_duplicate_reports_rejected_and_overflow_is_unavailable():
    line = evidence.PREFIX + json.dumps(REPORT)
    with pytest.raises(ValueError):
        evidence._project_rows(line + "\n" + line)
    assert (
        evidence._project_rows(
            evidence.PREFIX + json.dumps({**REPORT, "overflow": True})
        )
        == []
    )


def test_checkout_requires_exact_clean_identity(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for args in (
        ["init", "-q"],
        [
            "-c",
            "user.name=T",
            "-c",
            "user.email=t@example.com",
            "commit",
            "--allow-empty",
            "-qm",
            "fixture",
        ],
    ):
        subprocess.run(["git", *args], check=True)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    monkeypatch.setenv("GITHUB_SHA", REV)
    with pytest.raises(ValueError):
        evidence.validate_checkout(revision)
    monkeypatch.setenv("GITHUB_SHA", revision)
    evidence.validate_checkout(revision)
    Path("dirty").write_text("ignored until tracked")
    subprocess.run(["git", "add", "dirty"], check=True)
    with pytest.raises(ValueError):
        evidence.validate_checkout(revision)


def test_arms_disable_wait_channel_that_bypasses_xml_capture():
    from tests.sql_probes.qlib_ascii_ab import validate_arm

    env = {
        "RUN_BACKUP_RESTORE_DRILL": "1",
        "DATA_PROVIDER_MODE": "fixture",
        "MODEL_PROFILES_ENABLED": "false",
        "E2E_LIVE_PROVIDERS": "0",
        "MINEAGENT_QLIB_RPC_COST": "1",
        "MINEAGENT_QLIB_WAIT_PROFILE": "0",
    }
    validate_arm("baseline", True, env)
    with pytest.raises(ValueError):
        validate_arm("baseline", True, {**env, "MINEAGENT_QLIB_WAIT_PROFILE": "1"})


@pytest.mark.parametrize(
    ("arm", "phase", "accepted"),
    [
        ("baseline", "database_identity", True),
        ("baseline", "pinned_baseline", True),
        ("baseline", "source_attestation", True),
        ("baseline", "graph_body_edit", False),
        ("ascii_reuse", "graph_body_edit", True),
        ("ascii_reuse", "private message", False),
        ("ascii_reuse", "accepted", False),
    ],
)
def test_early_phase_only_projects_closed_arm_specific_values(
    tmp_path, arm, phase, accepted
):
    from xml.etree import ElementTree as ET

    case = ET.Element("testcase", name=evidence.CASE, classname=evidence.CLASS)
    ET.SubElement(case, "failure").text = "private traceback"
    props = ET.SubElement(case, "properties")
    ET.SubElement(props, "property", name="qlib_ascii_ab_phase", value=phase)
    path = tmp_path / "private.xml"
    ET.ElementTree(case).write(path)
    if not accepted:
        with pytest.raises(ValueError):
            evidence.project(path, arm, REV)
        return
    result = evidence.project(path, arm, REV)
    assert result["phase"] == phase and not result["source_attested"]
    assert result["commits"] == []
    assert "private" not in json.dumps(result)
