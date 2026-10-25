"""Pure output framing and harmless fixture reads; never execute hostile code."""

import hashlib
import json
import os
import stat
import struct
from types import SimpleNamespace

import pytest

from runtimes.hostile_deadline import protected_output as out


@pytest.fixture
def output_dir(tmp_path, monkeypatch):
    tmp_path.chmod(0o733)
    original_open, original_stat = os.open, os.fstat

    def open_(path, *args, **kwargs):
        return original_open(tmp_path if path == "/output" else path, *args, **kwargs)

    def stat_(fd):
        item = original_stat(fd)
        fields = {
            name: getattr(item, name) for name in dir(item) if name.startswith("st_")
        }
        fields["st_uid"] = 0 if stat.S_ISDIR(item.st_mode) else 65533
        return SimpleNamespace(**fields)

    monkeypatch.setattr(out.os, "open", open_)
    monkeypatch.setattr(out.os, "fstat", stat_)
    return tmp_path


def test_exact_regular_bytes_and_export(output_dir, monkeypatch):
    (output_dir / "result.bin").write_bytes(b"opaque\x00data")
    (output_dir / "result.bin").chmod(0o644)
    assert out.read_regular("result.bin", 100) == b"opaque\x00data"
    written = []
    monkeypatch.setattr(
        out, "write_frame", lambda kind, data: written.append((kind, data))
    )
    out.export_output(0)
    assert written[0][0] == 2
    assert out.validate_export(written[0][1]) == b"opaque\x00data"


@pytest.mark.parametrize(
    "case", ["symlink", "fifo", "unreadable", "oversized", "hardlink", "directory"]
)
def test_unsafe_output_is_rejected_without_native_parse(output_dir, case):
    path = output_dir / "result.bin"
    if case == "symlink":
        path.symlink_to("/etc/passwd")
    elif case == "fifo":
        os.mkfifo(path, 0o644)
    elif case == "directory":
        path.mkdir()
    else:
        path.write_bytes(b"xxxx")
        path.chmod(0o600 if case == "unreadable" else 0o644)
        if case == "hardlink":
            os.link(path, output_dir / "alias")
    with pytest.raises((ValueError, OSError)):
        out.read_regular("result.bin", 3 if case == "oversized" else 100)


@pytest.mark.parametrize(
    "name,cap",
    [
        ("../result.bin", 10),
        ("result.bin", True),
        ("result.bin", 0),
        ("result.bin", out.MAX_OUTPUT + 1),
        ("other", 10),
    ],
)
def test_output_abi_is_closed(name, cap):
    with pytest.raises(ValueError):
        out.read_regular(name, cap)


@pytest.mark.parametrize("kind,payload", [(1, out.MARKER), (2, b"opaque"), (3, b"")])
def test_complete_frame_roundtrip(kind, payload):
    assert out.parse_frame(out.frame(kind, payload)) == (kind, payload)


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"wrong",
        out.frame(1, b"x")[:-1],
        out.frame(1, b"x") + b"x",
        out.MAGIC + b"\x01" + struct.pack(">I", out.MAX_FRAME + 1),
        "text",
    ],
)
def test_truncated_oversized_missing_duplicate_frames_reject(raw):
    with pytest.raises(ValueError):
        out.parse_frame(raw)


@pytest.mark.parametrize(
    "kind,payload",
    [(True, b"x"), (0, b"x"), (4, b"x"), (1, "text"), (1, b"x" * (out.MAX_FRAME + 1))],
)
def test_frame_inputs_are_typed_and_bounded(kind, payload):
    with pytest.raises(ValueError):
        out.frame(kind, payload)


@pytest.mark.parametrize(
    "mutator",
    [
        "bool_code",
        "bool_size",
        "false_reaped",
        "wrong_hash",
        "extra",
        "duplicate",
        "noncanonical",
        "truncated",
    ],
)
def test_export_receipt_requires_exact_canonical_metadata(mutator):
    content = b"x"
    metadata = {
        "schema": "mineagent.hostile-quiescence.v2",
        "child_exit_code": 0,
        "descendants_reaped": True,
        "size": 1,
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    if mutator == "bool_code":
        metadata["child_exit_code"] = False
    if mutator == "bool_size":
        metadata["size"] = True
    if mutator == "false_reaped":
        metadata["descendants_reaped"] = False
    if mutator == "wrong_hash":
        metadata["sha256"] = "a" * 64
    if mutator == "extra":
        metadata["extra"] = 1
    raw = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
    if mutator == "duplicate":
        raw = raw[:-1] + b',"size":1}'
    if mutator == "noncanonical":
        raw = b" " + raw
    payload = struct.pack(">I", len(raw)) + raw + content
    if mutator == "truncated":
        payload = payload[:-1]
    with pytest.raises(ValueError):
        out.validate_export(payload)


def test_observer_emits_fixed_marker_not_arbitrary_file_bytes(monkeypatch):
    monkeypatch.setattr(out.time, "monotonic", lambda: 1)
    monkeypatch.setattr(out, "read_regular", lambda *_: out.MARKER)
    frames = []
    monkeypatch.setattr(out, "write_frame", lambda *args: frames.append(args))
    out.observe_marker(2)
    assert frames == [(1, out.MARKER)]


def test_observer_never_accepts_wrong_marker(monkeypatch):
    monkeypatch.setattr(out.time, "monotonic", lambda: 1)
    monkeypatch.setattr(out, "read_regular", lambda *_: b"wrong")
    monkeypatch.setattr(out, "write_frame", lambda *_: pytest.fail("forged marker"))
    with pytest.raises(ValueError):
        out.observe_marker(2)


def test_export_rejection_is_fixed_empty_frame(monkeypatch):
    def reject(*_):
        raise ValueError("private path")

    monkeypatch.setattr(out, "read_regular", reject)
    frames = []
    monkeypatch.setattr(out, "write_frame", lambda *args: frames.append(args))
    out.export_output(0)
    assert frames == [(3, b"")]


def test_changed_inode_rejected(output_dir, monkeypatch):
    (output_dir / "result.bin").write_bytes(b"fixed")
    (output_dir / "result.bin").chmod(0o644)
    original = out.os.read

    def read_(fd, size):
        data = original(fd, size)
        (output_dir / "result.bin").write_bytes(b"changed")
        return data

    monkeypatch.setattr(out.os, "read", read_)
    with pytest.raises(ValueError):
        out.read_regular("result.bin", 100)


@pytest.mark.parametrize(
    "chunks",
    [
        [(1, out.frame(1, out.MARKER))],
        [(1, out.frame(1, out.MARKER)[:7]), (1, out.frame(1, out.MARKER)[7:])],
    ],
)
def test_live_frame_validates_without_waiting_for_eof(chunks):
    from runtimes.hostile_deadline.acceptance import read_live_frame

    class Reader:
        def read_frame(self, *, deadline):
            assert deadline == 100
            assert chunks, "must return before EOF"
            return chunks.pop(0)

    assert read_live_frame(Reader(), 100) == (1, out.MARKER)


@pytest.mark.parametrize(
    "chunk",
    [
        None,
        (1, b"wrongmagic!"),
        (1, out.frame(1, b"x") + out.frame(1, b"x")),
        (2, b"private secret"),
    ],
)
def test_live_frame_rejects_missing_bad_duplicate_or_stderr(chunk):
    from runtimes.hostile_deadline.acceptance import read_live_frame

    class Reader:
        def read_frame(self, *, deadline):
            return chunk

    with pytest.raises((AssertionError, ValueError)) as error:
        read_live_frame(Reader(), 100)
    assert "private secret" not in str(error.value)


@pytest.mark.parametrize(
    "state,expected",
    [("S", True), ("R", True), ("Z", False), ("X", False), ("x", False)],
)
def test_process_liveness_excludes_exporter_zombie(monkeypatch, state, expected):
    from pathlib import Path

    from runtimes.hostile_deadline.acceptance import live_process

    fields = [state] + ["0"] * 18 + ["123"]
    monkeypatch.setattr(
        Path, "read_text", lambda _: "100 (ma-export-v2) " + " ".join(fields)
    )
    assert live_process(100, "123") is expected
    assert not live_process(100, "999")


@pytest.mark.parametrize(
    "row",
    [
        "101 65533 100 99 ma-export-v2",
        "101 0 999 99 ma-export-v2",
        "101 0 100 88 ma-export-v2",
        "101 0 100 99 spoof",
        "101 0 100 - ma-export-v2",
    ],
)
def test_same_helper_name_never_substitutes_uid_parent_namespace(row):
    from runtimes.hostile_deadline.acceptance import require_exporter_observation

    with pytest.raises(AssertionError):
        require_exporter_observation(["100 0 1 99 python", row], 100)


def test_exact_exporter_binding_checks_cgroup_and_live_start(monkeypatch):
    import io

    from runtimes.hostile_deadline import acceptance

    monkeypatch.setattr(acceptance, "process_identity", lambda _: "123")
    monkeypatch.setattr(acceptance, "live_process", lambda *_: True)
    monkeypatch.setattr(
        "builtins.open", lambda *_, **__: io.BytesIO(b"0::/fixed-unit\n")
    )
    rows = ["100 0 1 99 python", "101 0 100 99 ma-export-v2"]
    assert acceptance.require_exporter_observation(rows, 100) == (101, "123")
    monkeypatch.setattr(
        "builtins.open", lambda path, *_, **__: io.BytesIO(path.encode())
    )
    with pytest.raises(AssertionError):
        acceptance.require_exporter_observation(rows, 100)


@pytest.mark.parametrize(
    "payload", ["text", bytearray(b"abcd"), None, b"x" * (out.MAX_FRAME + 1)]
)
def test_export_entry_requires_bounded_exact_bytes(payload):
    with pytest.raises(ValueError):
        out.validate_export(payload)


def test_image_copy_inventory_exactly_matches_closed_dockerignore():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    runtime = root / "runtimes/hostile_deadline"
    expected = {
        f"runtimes/hostile_deadline/{name}"
        for name in ("contract.py", "deadline.py", "protected_output.py")
    }
    copies = [
        line.split()[1:]
        for line in (runtime / "Dockerfile").read_text().splitlines()
        if line.startswith("COPY ")
    ]
    assert len(copies) == 1
    assert (
        copies[0][-1]
        == "/usr/local/lib/python3.12/site-packages/runtimes/hostile_deadline/"
    )
    assert set(copies[0][:-1]) == expected
    assert len(copies[0][:-1]) == len(expected)
    allowed = (runtime / "Dockerfile.dockerignore").read_text().splitlines()
    assert allowed[:3] == ["**", "!runtimes/", "!runtimes/hostile_deadline/"]
    assert set(allowed[3:]) == {"!" + path for path in expected}
    assert len(allowed[3:]) == len(expected)
    assert all((root / path).is_file() for path in expected)
