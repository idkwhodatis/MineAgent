"""The browser launcher publishes only closed values after real click failure."""

import json
from xml.etree import ElementTree as ET

from script.ci_browser_diagnostics import (
    _REPLAY_MOTION_FLAGS,
    REPLAY_MARKER_PREFIX,
    _replay_marker_diagnostics,
    failure_sites,
)

_SAMPLE = {
    "table": "single",
    "page_matches": "single",
    "table_matches": "single",
    "window_matches": True,
    "listing_matches": False,
    "target": "center_blocked",
    "viewport_edge": None,
    "table_center_clipped": True,
    "click_report": "intercepted",
    "click_error": "action_timeout",
    "actionability_passed_observed": True,
    "scrolling_observed": True,
}


def _marker(record):
    return REPLAY_MARKER_PREFIX + json.dumps(record)


def test_replay_marker_projection_is_closed_bounded_and_consistent():
    assert _replay_marker_diagnostics((_marker(_SAMPLE) + "\n") * 40) == [_SAMPLE]
    for invalid in (
        {**_SAMPLE, "unexpected": "private"},
        {**_SAMPLE, "table": []},
        {**_SAMPLE, "page_matches": "private"},
        {**_SAMPLE, "table_matches": None},
        {**_SAMPLE, "target": "private"},
        {**_SAMPLE, "target": "not_unique"},
        {**_SAMPLE, "window_matches": 1},
        {**_SAMPLE, "listing_matches": "private"},
        {**_SAMPLE, "viewport_edge": "below"},
        {**_SAMPLE, "target": "outside_viewport"},
        {**_SAMPLE, "table_center_clipped": 1},
        {**_SAMPLE, "target": "disabled"},
        {**_SAMPLE, "click_report": "private"},
        {**_SAMPLE, "click_report": []},
        {**_SAMPLE, "click_error": "private"},
        {**_SAMPLE, "click_error": []},
        {**_SAMPLE, "click_error": None},
        {"click_report": "not_stable", "target": "center_blocked"},
    ):
        assert _replay_marker_diagnostics(_marker(invalid)) == []
    assert _replay_marker_diagnostics(REPLAY_MARKER_PREFIX + "x" * 1025) == []
    assert _replay_marker_diagnostics(
        REPLAY_MARKER_PREFIX + "bad\n" + _marker(_SAMPLE)
    ) == [_SAMPLE]
    assert (
        _replay_marker_diagnostics(
            (REPLAY_MARKER_PREFIX + "bad\n") * 32 + _marker(_SAMPLE)
        )
        == []
    )
    outside = {
        **_SAMPLE,
        "target": "outside_viewport",
        "viewport_edge": "below",
        "table_center_clipped": False,
    }
    assert _replay_marker_diagnostics(_marker(outside)) == [outside]
    duplicate = _marker(_SAMPLE).replace('"table":', '"table":"single","table":', 1)
    assert _replay_marker_diagnostics(duplicate) == []


def test_replay_diagnostic_only_failed_e2e_case_stdout_is_published(tmp_path):
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    ET.SubElement(suite, "system-out").text = _marker(_SAMPLE)
    success = ET.SubElement(suite, "testcase", name="private")
    ET.SubElement(success, "system-out").text = _marker(_SAMPLE)
    failed = ET.SubElement(suite, "testcase", name="private")
    ET.SubElement(failed, "failure").text = "private failure and URL"
    ET.SubElement(failed, "system-err").text = _marker(
        {**_SAMPLE, "target": "disabled"}
    )
    ET.SubElement(failed, "system-out").text = "private output\n" + _marker(_SAMPLE)
    path = tmp_path / "junit.xml"
    ET.ElementTree(root).write(path)
    assert failure_sites(path, tmp_path) == [
        {
            "case_index": 1,
            "outcome": "failed",
            "replay_marker_diagnostics": [_SAMPLE],
            "source_frames": [],
        }
    ]
    assert (
        "replay_marker_diagnostics"
        not in failure_sites(path, tmp_path, source_suite="engine-results")[0]
    )


def test_replay_projection_preserves_closed_error_when_dom_snapshot_is_unavailable():
    unavailable = {
        "click_report": "not_stable",
        "click_error": "action_timeout",
        "actionability_passed_observed": False,
        "scrolling_observed": False,
    }
    assert _replay_marker_diagnostics(_marker(unavailable)) == [unavailable]
    assert _replay_marker_diagnostics(_marker({**unavailable, "table": "single"})) == []


def test_motion_parser_accepts_only_closed_geometry_aggregates():
    motion = {
        "samples": "many",
        "matched_samples": "few",
        **dict.fromkeys(_REPLAY_MOTION_FLAGS, False),
    }
    assert _replay_marker_diagnostics(_marker({**_SAMPLE, "motion": motion})) == [
        {**_SAMPLE, "motion": motion}
    ]
    for invalid in (
        {**motion, "raw_coordinate": 10.5},
        {**motion, "samples": "private"},
        {**motion, "samples": "none"},
        {**motion, "document_y_changed": 1},
    ):
        assert _replay_marker_diagnostics(_marker({**_SAMPLE, "motion": invalid})) == []
