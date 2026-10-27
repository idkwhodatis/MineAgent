"""Pure protected frame checks; no lifecycle/namespace acceptance claim."""

import hashlib
import json
import struct

import pytest

from runtimes.qlib.protected_output import frame, parse_frame, validate_export


def payload(content=b'{"answer":1}', **changes):
    metadata = {
        "schema": "mineagent.qlib-analysis-quiescence.v1",
        "basename": "analysis.json",
        "child_exit_code": 0,
        "descendants_reaped": True,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        **changes,
    }
    raw = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
    return struct.pack(">I", len(raw)) + raw + content


def test_distinct_version_and_bounded_content():
    wire = frame(2, payload())
    assert wire.startswith(b"MAQA1\0")
    kind, raw = parse_frame(wire)
    assert kind == 2
    assert validate_export(raw, expected_basename="analysis.json") == b'{"answer":1}'


@pytest.mark.parametrize(
    "changes",
    [
        {"child_exit_code": 1},
        {"child_exit_code": False},
        {"descendants_reaped": 1},
        {"size": True},
        {"basename": "result.h5"},
        {"sha256": "f" * 64},
    ],
)
def test_forged_protected_metadata_rejected(changes):
    with pytest.raises(ValueError):
        validate_export(payload(**changes), expected_basename="analysis.json")


def test_legacy_duplicate_oversized_frames_rejected():
    valid = frame(2, payload())
    for raw in (
        b"MANU2\0" + valid[6:],
        valid + valid,
        valid[:-1],
        b"MAQA1\0\2" + struct.pack(">I", 2**31),
    ):
        with pytest.raises(ValueError):
            parse_frame(raw)
    with pytest.raises(ValueError):
        frame(4, b"x")
