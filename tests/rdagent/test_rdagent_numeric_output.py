"""Pure output framing and harmless fixture reads; never execute hostile code."""

import hashlib
import json
import os
import stat
import struct
from types import SimpleNamespace

import pytest

from runtimes.factor_execution import protected_output as out


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
    (output_dir / "result.h5").write_bytes(b"opaque\x00data")
    (output_dir / "result.h5").chmod(0o644)
    assert out.read_regular("result.h5", 100) == b"opaque\x00data"
    written = []
    monkeypatch.setattr(
        out, "write_frame", lambda kind, data: written.append((kind, data))
    )
    out.export_output("result.h5", 0)
    assert written[0][0] == 2
    assert (
        out.validate_export(written[0][1], expected_basename="result.h5")
        == b"opaque\x00data"
    )


@pytest.mark.parametrize(
    "case",
    ["symlink", "fifo", "unreadable", "oversized", "hardlink", "directory", "empty"],
)
def test_unsafe_output_is_rejected_without_native_parse(output_dir, case):
    path = output_dir / "result.h5"
    if case == "symlink":
        path.symlink_to("/etc/passwd")
    elif case == "fifo":
        os.mkfifo(path, 0o644)
    elif case == "directory":
        path.mkdir()
    else:
        path.write_bytes(b"" if case == "empty" else b"xxxx")
        path.chmod(0o600 if case == "unreadable" else 0o644)
        if case == "hardlink":
            os.link(path, output_dir / "alias")
    with pytest.raises((ValueError, OSError)):
        out.read_regular("result.h5", 3 if case == "oversized" else 100)


@pytest.mark.parametrize(
    "name,cap",
    [
        ("../result.h5", 10),
        ("result.h5", True),
        ("result.h5", 0),
        ("result.h5", out.MAX_OUTPUT + 1),
        ("other", 10),
    ],
)
def test_output_abi_is_closed(name, cap):
    with pytest.raises(ValueError):
        out.read_regular(name, cap)


@pytest.mark.parametrize("kind,payload", [(2, b"opaque"), (3, b"")])
def test_complete_frame_roundtrip(kind, payload):
    assert out.parse_frame(out.frame(kind, payload)) == (kind, payload)


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"wrong",
        out.frame(2, b"x")[:-1],
        out.frame(2, b"x") + b"x",
        out.MAGIC + b"\x01" + struct.pack(">I", out.MAX_FRAME + 1),
        "text",
    ],
)
def test_truncated_oversized_missing_duplicate_frames_reject(raw):
    with pytest.raises(ValueError):
        out.parse_frame(raw)


@pytest.mark.parametrize(
    "kind,payload",
    [(True, b"x"), (0, b"x"), (5, b"x"), (1, "text"), (1, b"x" * (out.MAX_FRAME + 1))],
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
        "schema": "mineagent.numeric-quiescence.v2",
        "basename": "result.h5",
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
        out.validate_export(payload, expected_basename="result.h5")


@pytest.mark.parametrize("name", out.OUTPUTS)
def test_export_binds_exact_numeric_role_basename(output_dir, monkeypatch, name):
    path = output_dir / name
    path.write_bytes(b"opaque")
    path.chmod(0o644)
    written = []
    monkeypatch.setattr(
        out, "write_frame", lambda kind, payload: written.append((kind, payload))
    )
    out.export_output(name, 0)
    kind, payload = written[0]
    assert kind == 2
    assert out.validate_export(payload, expected_basename=name) == b"opaque"
    other = next(item for item in out.OUTPUTS if item != name)
    with pytest.raises(ValueError):
        out.validate_export(payload, expected_basename=other)


def test_proof_profile_frame_cannot_substitute_numeric_frame():
    from runtimes.hostile_deadline import protected_output as proof

    with pytest.raises(ValueError):
        out.parse_frame(proof.frame(2, b"opaque"))
    assert proof.MAX_OUTPUT == 4 * 1024 * 1024
    assert out.MAX_OUTPUT == 16 * 1024 * 1024


def test_failed_native_child_cannot_publish_success(output_dir, monkeypatch):
    path = output_dir / "result.h5"
    path.write_bytes(b"retained-before-failure")
    path.chmod(0o644)
    written = []
    monkeypatch.setattr(
        out, "write_frame", lambda kind, value: written.append((kind, value))
    )
    out.export_output("result.h5", 2)
    assert written[0][0] == 2  # Retains bounded bytes, not an accepted success receipt.
    with pytest.raises(ValueError):
        out.validate_export(written[0][1], expected_basename="result.h5")


def test_missing_native_output_emits_only_fixed_failure(output_dir, monkeypatch):
    written = []
    monkeypatch.setattr(
        out, "write_frame", lambda kind, value: written.append((kind, value))
    )
    out.export_output("values.parquet", 0)
    assert written == [(3, out.rejection_metadata("values.parquet", 0))]


def test_numeric_inode_mutation_is_rejected(output_dir, monkeypatch):
    path = output_dir / "result.h5"
    path.write_bytes(b"fixed")
    path.chmod(0o644)
    original = out.os.read

    def read_(fd, size):
        data = original(fd, size)
        path.write_bytes(b"changed")
        return data

    monkeypatch.setattr(out.os, "read", read_)
    with pytest.raises(ValueError):
        out.read_regular("result.h5", 100)
