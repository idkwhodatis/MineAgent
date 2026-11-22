"""Ensure optional native projection remains exact and private."""

from pathlib import Path

from script.workflow_heartbeat_production_report import (
    BRIDGE,
    CASES,
    RESTORE,
    _anchored_phase,
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
