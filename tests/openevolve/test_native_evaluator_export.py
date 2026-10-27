"""Pure protected envelope validation, never native/financial execution proof."""

import pytest

from runtimes.openevolve_evaluator.protected_output import (
    export_payload,
    frame,
    parse_frame,
    validate_export,
)


def files():
    return {
        "metrics.json": b'{"metrics":{}}',
        "stdout.bin": b"observations\n",
        "stderr.bin": b"",
    }


def test_fixed_qualified_export_roundtrip():
    value = files()
    raw = export_payload(value, 0)
    assert validate_export(raw) == value
    assert parse_frame(frame(2, raw)) == (2, raw)
    assert b"native_observation_not_producer_proof" in raw


@pytest.mark.parametrize("change", ["empty", "extra", "metrics", "transcript", "exit"])
def test_closed_bounds_and_exit(change):
    value = files()
    code = 0
    if change == "empty":
        value["stdout.bin"] = b""
    elif change == "extra":
        value["source.py"] = b"x"
    elif change == "metrics":
        value["metrics.json"] = b"x" * (1024**2 + 1)
    elif change == "transcript":
        value["stderr.bin"] = b"x" * (8 * 1024**2)
    else:
        code = 1
    with pytest.raises(ValueError):
        export_payload(value, code)


def test_tamper_and_duplicate_rejected():
    value = export_payload(files(), 0)
    for bad in (value[:-1], value + b"x", value[:-1] + b"x"):
        with pytest.raises(ValueError):
            validate_export(bad)
    wire = frame(2, value)
    for bad in (wire + wire, b"MANU2\0" + wire[6:], wire[:-1]):
        with pytest.raises(ValueError):
            parse_frame(bad)
