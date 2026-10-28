"""Static required-case gate; not evidence of Docker or PostgreSQL execution."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_controller_workflow_requires_actual_case_without_skip():
    workflow = (ROOT / ".github/workflows/mineagent-rdagent-controller.yml").read_text()
    assert 'MINEAGENT_RD_CONTROLLER_REQUIRED: "1"' in workflow
    assert "tests/integration/test_rdagent_actual_controller.py" in workflow
    assert "test_actual_costeer_source_numeric_snapshot_generation" in workflow
    assert "('failure','error','skipped')" in workflow
    assert 'test "$status" = 0' in workflow
    assert "continue-on-error" not in workflow
    assert "permissions:\n  contents: read" in workflow


def test_controller_workflow_pins_base_wheels_and_all_three_images(tmp_path):
    workflow = (ROOT / ".github/workflows/mineagent-rdagent-controller.yml").read_text()
    for role in ("candidate", "normalizer", "orchestrator"):
        assert f"{role}.iid" in workflow
        assert f"MINEAGENT_{role.upper()}_IMAGE" in workflow
    assert workflow.index(
        "docker pull python:3.12.14-slim-bookworm@sha256:"
    ) < workflow.index("docker build ")
    for proof in (
        "runtimes/rdagent/wheel-proof.json",
        "runtimes/rdagent_operational/wheel-proof.json",
    ):
        assert proof in workflow
    assert "controller-build-two/operational-build-report.json" in workflow
    assert "persist-credentials: false" in workflow
    # Exercise the exact CI entry point without relying on PYTHONPATH injection.
    import json
    import os
    import subprocess
    import sys

    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    reports = []
    for name in ("first", "second"):
        output = tmp_path / name
        subprocess.run(
            [
                sys.executable,
                "-m",
                "runtimes.rdagent_operational.build_profile",
                "--output",
                str(output),
            ],
            cwd=ROOT,
            env=env,
            check=True,
            timeout=30,
        )
        reports.append(
            json.loads((output / "operational-build-report.json").read_text())
        )
    assert reports[0] == reports[1]
    assert reports[0] == json.loads(
        (ROOT / "runtimes/rdagent_operational/wheel-proof.json").read_text()
    )
    for name in ("rdagent-controller", "configured-analysis", "structured-factor"):
        source = (ROOT / f".github/workflows/mineagent-{name}.yml").read_text()
        assert "python runtimes/rdagent_operational/build_profile.py" not in source
        assert source.count("python -m runtimes.rdagent_operational.build_profile") == 2


def test_actual_fixture_uses_retained_source_pipeline_not_precursor():
    fixture = (ROOT / "tests/integration/rd_controller_fixture.py").read_text()
    assert "persist_maintained_source" not in fixture
    assert "create_maintained_run(" in fixture
    assert "execute_maintained_controller(" in fixture
    assert "canonical_bytes(manifest)" in fixture
    assert "observations.json" in fixture
    assert "DateWindow(start=date(2024, 3, 15), end=date(2024, 3, 16))" in fixture


def test_controller_gate_requires_real_cold_restore_and_same_recorded_pg_client():
    workflow = (ROOT / ".github/workflows/mineagent-rdagent-controller.yml").read_text()
    assert 'RUN_BACKUP_RESTORE_DRILL: "1"' in workflow
    assert "tests/integration/test_rdagent_execution_restore.py" in workflow
    assert "test_actual_stopped_controller_execution_snapshot_cold_restore" in workflow
    assert "assert len(cases)==3" in workflow
    assert "script/ci_pg_client.py" in workflow
    assert "--name tradegrad-ci-postgres" in workflow
    assert "--label io.tradegrad.ci=postgres" in workflow
    assert "TRADEGRAD_CI_POSTGRES_ID=" in workflow
    assert "$id|/tradegrad-ci-postgres|numeric-postgres" in workflow
    assert "DOCKER_CONFIG=" in workflow


def test_controller_gate_requires_real_callback_failure_containment():
    workflow = (ROOT / ".github/workflows/mineagent-rdagent-controller.yml").read_text()
    assert "tests/integration/test_rdagent_controller_recovery.py" in workflow
    assert (
        "test_actual_controller_identity_callback_loss_retains_containment" in workflow
    )


def test_controller_gate_requires_complete_contract_and_persistence_inventory():
    from script.neutral_acceptance import REQUIRED, REQUIRED_SOURCES

    workflow = (ROOT / ".github/workflows/mineagent-rdagent-controller.yml").read_text()
    assert (
        "tests/factors tests/rdagent tests/research_runtime tests/operations"
        in workflow
    )
    assert "tests/structured tests/structured_factor_strategy" in workflow
    assert "assert len(cases) == 4095" in workflow
    assert "var/numeric-proof/contracts-outcomes.xml" in workflow
    for source in (
        "tests/integration/test_rd_controller_persistence.py",
        "tests/integration/test_execution_snapshot_persistence.py",
    ):
        assert source in REQUIRED_SOURCES["postgres"]
        assert any(node.startswith(source + "::") for node in REQUIRED["postgres"])
