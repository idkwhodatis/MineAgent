"""No Docker doubles may count as actual numeric fixture acceptance."""

import hashlib
import json
import struct

import pytest

from runtimes.factor_execution.acceptance import frozen_inputs, read_numeric_frame
from runtimes.factor_execution.protected_output import MAGIC, MAX_FRAME, frame


class Reader:
    def __init__(self, entries):
        self.entries = iter(entries)
        self.deadlines = []

    def read_frame(self, *, deadline):
        self.deadlines.append(deadline)
        return next(self.entries, None)


@pytest.mark.parametrize("split", [1, 6, 7, 10, 11, 15])
def test_numeric_live_frame_keeps_original_deadline_and_needs_no_eof(split):
    raw = frame(2, b"opaque fixed bytes")
    reader = Reader([(1, raw[:split]), (1, raw[split:])])
    assert read_numeric_frame(reader, 123.5) == (2, b"opaque fixed bytes")
    assert reader.deadlines == [123.5, 123.5]


@pytest.mark.parametrize(
    "entries",
    [
        [],
        [(2, b"error")],
        [(1, b"wrong-version")],
        [(1, frame(2, b"x")[:-1])],
        [(1, frame(2, b"x") + b"extra")],
        [(1, MAGIC + b"\x02" + struct.pack(">I", MAX_FRAME + 1))],
    ],
)
def test_numeric_live_frame_rejects_missing_corrupt_or_extra(entries):
    with pytest.raises(ValueError):
        read_numeric_frame(Reader(entries), 123.5)


def test_frozen_fixture_input_inventory_binds_actual_image_pins():
    candidate, normalizer = "sha256:" + "1" * 64, "sha256:" + "2" * 64
    prefix, inputs, ns = frozen_inputs(candidate, normalizer)
    assert set(inputs) == {
        "/input/source.py",
        "/input/manifest.json",
        "/input/listings.json",
        "/input/observations.json",
    }
    assert prefix.candidate_image == candidate and prefix.normalizer_image == normalizer
    assert len(prefix.listings) == 3 and type(ns) is int
    assert prefix.max_output_rows == 3 and prefix.max_output_bytes == 10000
    manifest = json.loads(inputs["/input/manifest.json"])
    assert (
        hashlib.sha256(inputs["/input/source.py"]).hexdigest()
        == "85e5531ec17a1e3423a30864f72dcf711207cf55ed0763a549984d7355f552e7"
    )
    assert (
        manifest["candidate_image"] == candidate
        and manifest["normalizer_image"] == normalizer
    )
    assert (
        'calculate(Path("/input"), Path("/output/result.h5"))'
        in inputs["/input/source.py"].decode()
    )


@pytest.mark.parametrize(
    "running,now",
    [(False, 100.0), (None, 100.0), (1, 100.0), (True, 108.0), (True, 109.0)],
)
def test_frame_requires_running_original_unit(running, now):
    from runtimes.factor_execution.acceptance import require_live_numeric_frame

    with pytest.raises(AssertionError):
        require_live_numeric_frame({"State": {"Running": running}}, now, 108.0)


def test_frame_running_before_original_deadline():
    from runtimes.factor_execution.acceptance import require_live_numeric_frame

    require_live_numeric_frame({"State": {"Running": True}}, 107.0, 108.0)


@pytest.mark.parametrize(
    "extra",
    [
        [(1, frame(2, b"duplicate"))],
        [(1, b"trailing")],
        [(2, b"stderr")],
        [(1, b"split"), (1, b"tail")],
    ],
)
def test_later_docker_frames_after_complete_frame_are_rejected(extra):
    from runtimes.factor_execution.acceptance import require_complete_numeric_stream

    class CompleteReader(Reader):
        def collect_until_eof(self, *, deadline):
            channels = {1: b"", 2: b""}
            while (item := self.read_frame(deadline=deadline)) is not None:
                channels[item[0]] += item[1]
            return channels

    raw = frame(2, b"opaque")
    reader = CompleteReader([(1, raw[:7]), (1, raw[7:]), *extra])
    assert read_numeric_frame(reader, 108.0) == (2, b"opaque")
    with pytest.raises(AssertionError, match="trailing"):
        require_complete_numeric_stream(reader, 109.0)


def test_complete_stream_requires_eof_without_other_bytes():
    from runtimes.factor_execution.acceptance import require_complete_numeric_stream

    class Empty:
        def collect_until_eof(self, *, deadline):
            assert deadline == 109.0
            return {1: b"", 2: b""}

    require_complete_numeric_stream(Empty(), 109.0)
