"""Closed failure-only DOM diagnostic transport, never browser acceptance."""

import json
from pathlib import Path
from xml.etree import ElementTree as ET

from script.ci_browser_diagnostics import (
    COMPARISON_STATUS_PREFIX,
    _comparison_status_diagnostics,
    failure_sites,
)


def marker(value):
    return COMPARISON_STATUS_PREFIX + json.dumps(value)


def test_comparison_marker_rejects_unknown_fields_values_duplicates_and_bounds():
    valid = {
        "presence": "single",
        "status": "failed",
        "error_code": "comparison_timeout",
    }
    assert _comparison_status_diagnostics(marker(valid)) == [valid]
    for bad in (
        {**valid, "secret": "private"},
        {**valid, "status": "private"},
        {**valid, "error_code": "private"},
        {**valid, "presence": "missing"},
        {**valid, "status": []},
        {**valid, "error_code": []},
    ):
        assert _comparison_status_diagnostics(marker(bad)) == []
    assert (
        _comparison_status_diagnostics(
            COMPARISON_STATUS_PREFIX
            + '{"presence":"single","presence":"single","status":"failed","error_code":null}'
        )
        == []
    )
    assert _comparison_status_diagnostics(COMPARISON_STATUS_PREFIX + "x" * 513) == []
    assert (
        _comparison_status_diagnostics(
            (COMPARISON_STATUS_PREFIX + "bad\n") * 32 + marker(valid)
        )
        == []
    )
    assert _comparison_status_diagnostics((marker(valid) + "\n") * 32) == [valid]


def test_failed_case_stdout_is_projected_but_success_suite_and_private_text_are_not(
    tmp_path,
):
    valid = {"presence": "single", "status": "running", "error_code": None}
    report = ET.Element("testsuites")
    suite = ET.SubElement(report, "testsuite")
    ET.SubElement(suite, "system-out").text = marker(valid)
    for failed in (False, True):
        case = ET.SubElement(suite, "testcase", name="private")
        if failed:
            ET.SubElement(case, "failure").text = "private error"
        ET.SubElement(case, "system-out").text = "private payload\n" + marker(valid)
    path = tmp_path / "report.xml"
    ET.ElementTree(report).write(path)
    output = failure_sites(path, tmp_path)
    assert output == [
        {
            "case_index": 1,
            "outcome": "failed",
            "comparison_diagnostics": [valid],
            "source_frames": [],
        }
    ]
    assert "private" not in json.dumps(output)
    assert "comparison_diagnostics" not in json.dumps(
        failure_sites(path, tmp_path, source_suite="engine-results")
    )


def test_original_assertion_deadline_and_test_only_boundary_remain():
    root = Path(__file__).resolve().parents[2]
    source = (root / "frontend/tests/e2e/independent-verification.spec.ts").read_text()
    expected = """  await withComparisonFailureDiagnostic(page, async () => {
    await expect(page.getByTestId("comparison-status")).toHaveText(
      "succeeded",
      {
        timeout: 210000,
      },
    );
  });"""
    assert source.count(expected) == 1
    helper = (root / "frontend/tests/e2e/comparison-status-diagnostics.ts").read_text()
    assert (
        "console.log(comparisonDiagnosticPrefix + JSON.stringify(diagnostic))" in helper
    )
    assert "throw error;" in helper
    assert "error.message" not in helper and "error.stack" not in helper
    assert not any(x in helper for x in (".request", "fetch(", "waitFor"))
    assert (
        helper.count(
            'timer = setTimeout(() => reject(new Error("diagnostic_timeout")), 250)'
        )
        == 1
    )
    assert "finally {\n      clearTimeout(timer);" in helper
    assert helper.count("comparisonStatusDiagnostic(page),") == 1
