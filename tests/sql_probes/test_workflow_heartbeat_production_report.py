"""Ensure optional native projection remains exact and private."""

from pathlib import Path

from script.workflow_heartbeat_production_report import (
    BRIDGE,
    CASES,
    RESTORE,
    _anchored_phase,
    _bridge_phase,
    _drain_observations,
    _drain_verdict,
    coordination,
    outcome,
)


def test_exact_fixture_source_anchors():
    fixture = Path("tests/integration/workflow_structured_stage_fixture.py").read_text()
    bridge = fixture.split("def run_bridge(", 1)[1].split("def assert_restore(", 1)[0]
    occurrences = {"drain(env.factory, env.settings)": 2}
    for anchor, expected_phase in BRIDGE.items():
        indices = [
            i
            for i, value in enumerate(bridge.splitlines(), 1)
            if value.strip() == anchor
        ]
        assert (
            _anchored_phase(
                bridge, indices[-1], BRIDGE, "bridge", occurrences=occurrences
            )
            == expected_phase
        )
    restore = Path(
        "tests/integration/test_workflow_structured_stage_flow.py"
    ).read_text()
    for anchor, expected_phase in RESTORE.items():
        index = next(
            i
            for i, value in enumerate(restore.splitlines(), 1)
            if value.strip() == anchor
        )
        assert (
            _anchored_phase(restore, index, RESTORE, "before_bridge") == expected_phase
        )


def _write(root, test_case, contents):
    module, test = CASES[test_case]
    path = root / f"{test_case}.xml"
    path.write_text(
        '<testsuite><testcase classname="tests.integration.'
        + module
        + '" name="'
        + test
        + '">'
        + contents
        + "</testcase></testsuite>"
    )
    return path


def test_closed_projection_will_not_return_raw_test_output(tmp_path):
    _write(
        tmp_path,
        "bridge",
        "<failure>secret-canary</failure><properties>"
        '<property name="proof_phase" value="rd_drain"/>'
        '<property name="proof_code" value="secret-canary"/>'
        "</properties>",
    )
    assert outcome(tmp_path, "bridge") == ("failure", "rd_drain", "unavailable")


def test_projection_fails_closed_on_mismatched_inventory_and_skip(tmp_path):
    path = _write(tmp_path, "heartbeat", "<skipped>private</skipped>")
    assert outcome(tmp_path, "heartbeat") == ("skipped", "unavailable", "unavailable")
    path.write_text(path.read_text().replace(CASES["heartbeat"][1], "different"))
    assert outcome(tmp_path, "heartbeat") == (
        "unavailable",
        "unavailable",
        "unavailable",
    )


def test_projection_only_accepts_exact_pass(tmp_path):
    _write(tmp_path, "restore", "")
    assert outcome(tmp_path, "restore") == ("passed", "complete", "unavailable")


def test_coordination_projection_requires_exact_distinct_cases(tmp_path):
    classname = "tests.integration.test_workflow_heartbeat_rpc_coordination"
    expected = {
        "heartbeat-commits-first": "heartbeat_commits_first",
        "rpc-commits-first": "rpc_commits_first",
        "rpc-rolls-back-first": "rpc_rolls_back_first",
    }
    path = tmp_path / "coordination.xml"

    def write(parameters):
        records = "".join(
            f'<testcase classname="{classname}" '
            f'name="test_exact_workflow_rpc_coordinates_with_heartbeat[{param}]">'
            f"{detail}</testcase>"
            for param, detail in parameters
        )
        path.write_text(f"<testsuite>{records}</testsuite>")

    write([(key, "") for key in expected])
    assert coordination(tmp_path) == dict.fromkeys(expected.values(), "passed")
    write(
        [
            ("heartbeat-commits-first", ""),
            ("rpc-commits-first", ""),
            ("rpc-rolls-back-first", "<skipped>private-canary</skipped>"),
        ]
    )
    assert coordination(tmp_path) == {
        "heartbeat_commits_first": "passed",
        "rpc_commits_first": "passed",
        "rpc_rolls_back_first": "skipped",
    }
    write([("heartbeat-commits-first", "")] * 3)
    assert coordination(tmp_path) == dict.fromkeys(expected.values(), "unavailable")
    path.unlink()
    path.symlink_to("not-a-real-report.xml")
    assert coordination(tmp_path) == dict.fromkeys(expected.values(), "unavailable")


def test_waiting_release_checkpoint_and_worker_drain_do_not_collapse():
    fixture = Path("tests/integration/workflow_structured_stage_fixture.py").read_text()
    source = fixture.split("def run_bridge(", 1)[1].split("def assert_restore(", 1)[0]
    lines = source.splitlines()
    release = next(
        i for i, line in enumerate(lines, 1) if line.strip() == '== "waiting_release"'
    )
    assert _bridge_phase(source, release - 3) == "rd_drain"
    assert _bridge_phase(source, release - 2) == "rd_waiting_release"
    assert _bridge_phase(source, release + 4) == "rd_checkpoint_readback"


def test_only_closed_first_error_and_exact_known_roles_escape_private_stdout():
    captured = (
        'RD_QUEUE_FAILURE:{"family":"database","sqlstate":"55P03","stage":"queue","code":"private-test-key","untrusted":"secret"}\n'
        'RD_QUEUE_FAILURE:{"family":"domain","sqlstate":"none","stage":"controller","code":"deadline"}\n'
        'RD_QUEUE_STATE:{"status":"failed","unit_overflow":false,"units":[{"role":"rd_orchestrator","state":"stopped"},{"role":"native_evaluator","state":"secret-fixture"},{"role":"factor_normalizer","state":"running"},{"role":"factor_normalizer","state":"stopped"}]}\n'
    )
    assert _drain_observations(captured) == {
        "family": "database",
        "sqlstate": "55P03",
        "stage": "queue",
        "run": "failed",
        "rd_orchestrator": "stopped",
    }
    assert _drain_observations(captured + "RD_QUEUE_STATE:{invalid}") == {}


def test_projection_revalidates_properties_and_does_not_publish_passes(tmp_path):
    properties = (
        '<properties><property name="proof_phase" value="rd_waiting_release"/>'
        '<property name="rd_drain_code" value="deadline"/>'
        '<property name="rd_drain_family" value="secret-canary"/>'
        '<property name="rd_drain_run" value="failed"/>'
        '<property name="rd_drain_run" value="running"/></properties>'
    )
    _write(tmp_path, "bridge", properties + "<failure>private</failure>")
    assert _drain_verdict(tmp_path, "bridge") == {
        "proof_phase": "rd_waiting_release",
        "code": "deadline",
    }
    _write(tmp_path, "bridge", properties)
    assert _drain_verdict(tmp_path, "bridge") == {}
