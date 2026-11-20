"""Token failures expose only fixed phases and bounded HTTP status, never content."""

import json
from xml.etree import ElementTree as ET

import pytest

from script.ci_browser_diagnostics import _token_diagnostics, failure_sites


def test_token_projection_preserves_only_public_discriminators(tmp_path):
    directory = tmp_path / "frontend/tests/e2e"
    directory.mkdir(parents=True)
    (directory / "helpers.ts").write_text("\n" * 80)
    report = ET.Element("testsuites")
    case = ET.SubElement(report, "testcase", name="PRIVATE")
    failure = ET.SubElement(case, "failure", message="PRIVATE_PASSWORD")
    failure.text = "\n".join(  # noqa: FLY002 -- readable private-report fixture
        [
            'TOKEN_DIAGNOSTIC_V1:{"phase":"http","http_status":429}',
            'TOKEN_DIAGNOSTIC_V1:{"phase":"request","http_status":null}',
            "PRIVATE_BODY TOKEN https://private.example/token",
            "at /private/path/helpers.ts:42:3",
            "at /private/path/arbitrary.ts:1:1",
            "at helpers.ts:9999:1",
        ]
    )
    source = tmp_path / "private.xml"
    source.write_bytes(ET.tostring(report))
    rows = failure_sites(source, tmp_path)
    assert rows[0]["source_frames"] == [{"file": "helpers.ts", "line": 42, "column": 3}]
    assert rows[0]["token_diagnostics"] == [
        {"phase": "http", "http_status": 429},
        {"phase": "request", "http_status": None},
    ]
    assert "PRIVATE" not in json.dumps(rows)
    assert "https" not in json.dumps(rows)
    assert (
        "token_diagnostics"
        not in failure_sites(source, tmp_path, source_suite="engine-results")[0]
    )


@pytest.mark.parametrize(
    "payload",
    [
        '{"phase":"decode","http_status":200,"body":"PRIVATE"}',
        '{"phase":"PRIVATE","http_status":401}',
        '{"phase":"http","http_status":true}',
        '{"phase":"http","http_status":null}',
        '{"phase":"request","http_status":200}',
        '{"phase":"http","http_status":600}',
        '{"phase":"http","http_status":99}',
        '{"phase":"http","http_status":401,"phase":"decode"}',
        '{"phase":[],"http_status":401}',
        "null",
        "[" * 257,
    ],
)
def test_token_projection_rejects_unknown_malformed_or_private_fields(payload):
    assert _token_diagnostics("TOKEN_DIAGNOSTIC_V1:" + payload) == []


def test_token_projection_bounds_candidates_and_deduplicates():
    valid = 'TOKEN_DIAGNOSTIC_V1:{"phase":"decode","http_status":200}'
    assert _token_diagnostics((valid + "\n") * 100) == [
        {"phase": "decode", "http_status": 200}
    ]
    assert _token_diagnostics(("TOKEN_DIAGNOSTIC_V1:invalid\n" * 32) + valid) == []


def test_token_helper_symlink_not_allowed(tmp_path):
    directory = tmp_path / "frontend/tests/e2e"
    directory.mkdir(parents=True)
    target = tmp_path / "private.ts"
    target.write_text("\n" * 50)
    (directory / "helpers.ts").symlink_to(target)
    report = tmp_path / "private.xml"
    report.write_text(
        "<testsuites><testcase><failure>at helpers.ts:2:1</failure></testcase></testsuites>"
    )
    assert failure_sites(report, tmp_path)[0]["source_frames"] == []
