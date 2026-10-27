"""Strict bounded MAOE1 attached stream; no result authority without whole-unit stop."""

import struct

from runtimes.openevolve_evaluator.protected_output import MAGIC, MAX_FRAME, parse_frame


def read_native_frame(reader, deadline):
    data = bytearray()
    while True:
        received = reader.read_frame(deadline=deadline)
        if received is None:
            raise ValueError("native output missing or truncated")
        stream, chunk = received
        if stream != 1 or len(data) + len(chunk) > MAX_FRAME + 11:
            raise ValueError("native output channel violation")
        data.extend(chunk)
        if len(data) >= 6 and data[:6] != MAGIC:
            raise ValueError("wrong native export version")
        if len(data) >= 11:
            expected = 11 + struct.unpack(">I", data[7:11])[0]
            if expected > MAX_FRAME + 11 or len(data) > expected:
                raise ValueError("native export bound/duplicate violation")
            if len(data) == expected:
                return parse_frame(bytes(data))


def require_live_native_frame(document, now, original_deadline):
    if document.get("State", {}).get("Running") is not True or now >= original_deadline:
        raise AssertionError("native frame did not arrive while original unit alive")


def require_complete_native_stream(reader, deadline):
    channels = reader.collect_until_eof(deadline=deadline)
    if channels != {1: b"", 2: b""}:
        raise AssertionError("duplicate or trailing native output bytes")
