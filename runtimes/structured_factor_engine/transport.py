"""Strict bounded MAFS1 attached stream; no result authority without whole-unit stop."""

import struct

from runtimes.structured_factor_engine.protected_output import (
    MAGIC,
    MAX_FRAME,
    parse_frame,
)


def read_structured_frame(reader, deadline):
    data = bytearray()
    while True:
        received = reader.read_frame(deadline=deadline)
        if received is None:
            raise ValueError("structured output missing or truncated")
        stream, chunk = received
        if stream != 1 or len(data) + len(chunk) > MAX_FRAME + 11:
            raise ValueError("structured output channel violation")
        data.extend(chunk)
        if len(data) >= 6 and data[:6] != MAGIC:
            raise ValueError("wrong structured export version")
        if len(data) >= 11:
            expected = 11 + struct.unpack(">I", data[7:11])[0]
            if expected > MAX_FRAME + 11 or len(data) > expected:
                raise ValueError("structured export bound/duplicate violation")
            if len(data) == expected:
                return parse_frame(bytes(data))


def require_live_structured_frame(document, now, original_deadline):
    if document.get("State", {}).get("Running") is not True or now >= original_deadline:
        raise AssertionError(
            "structured frame did not arrive while original unit alive"
        )


def require_complete_structured_stream(reader, deadline):
    channels = reader.collect_until_eof(deadline=deadline)
    if channels != {1: b"", 2: b""}:
        raise AssertionError("duplicate or trailing structured output bytes")
