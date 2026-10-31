"""Offline malicious private-report tests; no browser/server execution."""

import json
import os
from xml.etree import ElementTree as ET

import pytest

from script.oe_browser_diagnostic import MAX_BYTES, browser_failure


def report(path, text, kind="failure"):
    root = ET.Element("testsuites")
    case = ET.SubElement(root, "testcase", name="private-user-title")
    ET.SubElement(case, kind).text = text
    ET.ElementTree(root).write(path)


def test_only_known_checkout_frames_and_finite_assertion_kind(tmp_path):
    path = tmp_path / "private.xml"
    checkout = tmp_path / "checkout"
    report(
        path,
        f"Error: expect(received).toBe(expected)\nsecret private-value\n    at {checkout}/frontend/tests/oe-normal-acceptance/normal.spec.ts:84:3\n    at signIn ({checkout}/frontend/tests/e2e/helpers.ts:20:7)\n    at /foreign/normal.spec.ts:99:5\n    at https://private.invalid/secret:12:5",
    )
    value = browser_failure(path, checkout)
    assert value == {
        "report": "parsed",
        "failures": [
            {
                "kind": "assertion",
                "source_frames": [
                    {"file": "normal.spec.ts", "line": 84, "column": 3},
                    {"file": "helpers.ts", "line": 20, "column": 7},
                ],
            }
        ],
    }
    assert not any(
        word in json.dumps(value)
        for word in ("private", "secret", "foreign", str(checkout))
    )

    for name in ("history.spec.ts", "create.spec.ts", "observation-checkpoint.spec.ts"):
        report(
            path,
            f"Error: expect(received).toBe(expected)\n    at {checkout}/frontend/tests/workflow-history-acceptance/{name}:45:7\n    at /external/{name}:2:3\nprivate payload",
        )
        projected = browser_failure(path, checkout)
        assert projected["failures"] == [
            {
                "kind": "assertion",
                "source_frames": [{"file": name, "line": 45, "column": 7}],
            }
        ]
        assert "external" not in json.dumps(projected) and "private" not in json.dumps(
            projected
        )


def test_unknown_and_timeout_never_export_message_or_untrusted_attributes(tmp_path):
    path = tmp_path / "private.xml"
    report(path, "arbitrary private response", "error")
    assert browser_failure(path, tmp_path)["failures"] == [
        {"kind": "unknown", "source_frames": []}
    ]
    report(path, "Test timeout of 90000ms exceeded: private-value")
    assert browser_failure(path, tmp_path)["failures"] == [
        {"kind": "timeout", "source_frames": []}
    ]


def test_missing_symlink_malformed_entities_and_oversize_fail_closed(
    tmp_path, monkeypatch
):
    path = tmp_path / "private.xml"
    assert browser_failure(path, tmp_path)["report"] == "unavailable"
    for raw in (
        b"not xml",
        b"<x>\x00</x>",
        b"<x>\xff</x>",
        '<!DOCTYPE x [<!ENTITY p "private">]><x>&p;</x>'.encode("utf-16"),
        b'<!DOCTYPE x [<!ENTITY p SYSTEM "file:///private">]><x/>',
        b"x" * (MAX_BYTES + 1),
    ):
        path.write_bytes(raw)
        assert browser_failure(path, tmp_path) == {"report": "invalid", "failures": []}
    link = tmp_path / "link.xml"
    link.symlink_to(path)
    assert browser_failure(link, tmp_path)["report"] == "unavailable"

    # Opening a FIFO must be nonblocking and rejected before any read.
    fifo = tmp_path / "fifo.xml"
    os.mkfifo(fifo)
    original_open = os.open
    opened = []

    def checked_open(name, flags):
        assert flags & os.O_NOFOLLOW and flags & os.O_NONBLOCK
        fd = original_open(name, flags)
        opened.append(fd)
        return fd

    monkeypatch.setattr(os, "open", checked_open)
    assert browser_failure(fifo, tmp_path) == {"report": "invalid", "failures": []}
    with pytest.raises(OSError):
        os.fstat(opened[-1])

    # Swap immediately at open: no earlier path stat may authorize a symlink.
    swapped = tmp_path / "swapped.xml"
    swapped.write_text("<testsuites/>")

    def swap_open(name, flags):
        swapped.unlink()
        swapped.symlink_to(path)
        return checked_open(name, flags)

    monkeypatch.setattr(os, "open", swap_open)
    assert browser_failure(swapped, tmp_path)["report"] == "unavailable"


def test_failure_and_frame_counts_and_numeric_fields_are_bounded(tmp_path):
    path = tmp_path / "private.xml"
    source = tmp_path / "frontend/tests/oe-normal-acceptance/normal.spec.ts"
    root = ET.Element("testsuites")
    for _ in range(20):
        case = ET.SubElement(root, "testcase")
        ET.SubElement(case, "failure").text = (
            f"\n    at {source}:10000:2\n    at {source}:2:1000\n"
            + f"    at {source}:10:2\n" * 20
        )
    ET.ElementTree(root).write(path)
    value = browser_failure(path, tmp_path)
    assert len(value["failures"]) == 4
    assert all(len(row["source_frames"]) == 4 for row in value["failures"])
    assert all(
        frame["line"] == 10 and frame["column"] == 2
        for row in value["failures"]
        for frame in row["source_frames"]
    )


def test_submit_http_diagnostic_requires_closed_tokens_and_known_source(tmp_path):
    path = tmp_path / "private.xml"
    source = tmp_path / "frontend/tests/oe-normal-acceptance/normal.spec.ts"
    frame = f"\n    at {source}:265:5"
    report(path, "Error: OE_SUBMIT_HTTP_V1:503:database_unavailable\nprivate" + frame)
    value = browser_failure(path, tmp_path)["failures"][0]
    assert value["submit_http"] == {"status": "503", "code": "database_unavailable"}
    assert "private" not in json.dumps(value)
    for marker in (
        "OE_SUBMIT_HTTP_V1:599:database_unavailable",
        "OE_SUBMIT_HTTP_V1:503:private",
        "OE_SUBMIT_HTTP_V1:503:database_unavailable-private",
        "OE_SUBMIT_HTTP_V1:503:database_unavailable private",
    ):
        report(path, "Error: " + marker + frame)
        assert "submit_http" not in browser_failure(path, tmp_path)["failures"][0]
    report(
        path,
        "Error: OE_SUBMIT_HTTP_V1:503:database_unavailable\n    at /foreign/normal.spec.ts:265:5",
    )
    assert "submit_http" not in browser_failure(path, tmp_path)["failures"][0]
