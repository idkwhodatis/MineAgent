import sys
from pathlib import Path

import pytest

from script import workflow_rd_controller_residue as obs
from script.workflow_heartbeat_production_report import CASES


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"", "absent_observed"),
        (b"running\n", "running"),
        (b"created\n", "created"),
        (b"exited\nexited\n", "exited"),
        (b"running\nexited\n", "mixed"),
        (b"dead\n", "dead"),
        (b"removing\n", "removing"),
        (b"private-canary\n", "unavailable"),
        (b"created\n" * obs._MAX_BYTES, "unavailable"),
        (None, "unavailable"),
    ],
)
def test_only_closed_docker_states(raw, expected):
    assert obs._classify(raw) == expected


def test_capture_bounds_and_hides_unused_streams(monkeypatch, capsys):
    assert obs._CMD == [
        "docker",
        "ps",
        "--all",
        "--filter",
        "label=io.mineagent.managed=mineagent-rdagent-orchestrator-v2",
        "--format",
        "{{.State}}",
    ]
    monkeypatch.setattr(
        obs,
        "_CMD",
        [
            sys.executable,
            "-c",
            "import sys; print('created'); print('private-canary', file=sys.stderr)",
        ],
    )
    assert obs._capture() == b"created\n"
    monkeypatch.setattr(
        obs,
        "_CMD",
        [
            sys.executable,
            "-c",
            "print('running\\n' * 4000)",
        ],
    )
    assert obs._capture() is None
    monkeypatch.setattr(
        obs,
        "_CMD",
        [
            sys.executable,
            "-c",
            "import sys; print('running'); sys.exit(1)",
        ],
    )
    assert obs._capture() is None
    assert capsys.readouterr() == ("", "")


def test_coarse_projection_does_not_open_standard_details(monkeypatch, tmp_path):
    monkeypatch.setattr(obs, "_capture", lambda: b"exited\n")
    module, case = CASES["bridge"]
    path = tmp_path / "bridge.xml"
    path.write_text(
        "<testsuite>"
        + f'<testcase name="{case}" classname="tests.integration.{module}">'
        + '<properties><property name="proof_phase" value="oe_claim"/>'
        + '<property name="rd_drain_oe_claim_site" value="dispatch"/></properties>'
        + "<failure>private-canary</failure></testcase></testsuite>"
    )
    assert obs.project("after_bridge", tmp_path) == (
        "RD_CONTROLLER_RESIDUE_V1 phase=after_bridge docker_state=exited bridge_status=failure"
    )
    path.unlink()
    assert obs.project("after_restore", tmp_path).endswith("bridge_status=unavailable")
    with pytest.raises(ValueError, match="Closed teardown phase"):
        obs.project("private-canary", tmp_path)


def test_separate_steps_only_run_after_unchanged_containment_failure():
    source = Path(
        ".github/workflows/mineagent-production-heartbeat-native-proof.yml"
    ).read_text()
    assert (
        source.count("run: python -m script.workflow_heartbeat_production_report\n")
        == 1
    )
    assert (
        "steps.bridge_clean.outcome == 'success' && steps.restore_clean.outcome == 'success'"
        in source
    )
    assert "steps.pg_cleanup.outcome == 'success'" in source
    for phase, guard in (
        ("after_bridge", "bridge_clean"),
        ("after_restore", "restore_clean"),
    ):
        reporter = (
            f"        if: ${{{{ !cancelled() && steps.{guard}.outcome == 'failure' }}}}\n"
            "        timeout-minutes: 1\n"
            "        shell: bash\n"
            f"        run: python -m script.workflow_rd_controller_residue {phase}\n"
        )
        assert source.count(reporter) == 1
        assert source.index(f"id: {guard}\n") < source.index(reporter)
