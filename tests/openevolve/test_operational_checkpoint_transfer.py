"""Actual local checkpoint files; transport doubles, never publication authority."""

import hashlib
import os
import time
from uuid import uuid4

import pytest

from runtimes.openevolve_operational import checkpoint_transfer as module


@pytest.fixture
def checkpoint(tmp_path):
    token = uuid4()
    root = tmp_path / "checkpoints"
    directory = root / str(token)
    (directory / "programs").mkdir(parents=True)
    files = {name: b"{}" for name in module.FIXED}
    files[f"programs/{uuid4()}.json"] = b"x" * 100000
    for name, raw in files.items():
        (directory / name).write_bytes(raw)
    return root, token, directory, tuple(sorted(files.items()))


def read(c):
    return module.read_checkpoint(c[0], c[1], deadline=time.monotonic() + 10)


def test_real_stable_closed_inventory(checkpoint):
    assert read(checkpoint) == checkpoint[3]


@pytest.mark.parametrize(
    "mode", ["symlink", "hardlink", "fifo", "empty", "oversize", "directory"]
)
def test_unsafe_native_file(checkpoint, mode):
    root, _token, directory, _files = checkpoint
    target = directory / "metadata.json"
    target.unlink()
    other = root / "other"
    other.write_bytes(b"{}")
    if mode == "symlink":
        target.symlink_to(other)
    elif mode == "hardlink":
        os.link(other, target)
    elif mode == "fifo":
        os.mkfifo(target)
    elif mode == "empty":
        target.touch()
    elif mode == "directory":
        target.mkdir()
    else:
        target.write_bytes(b"x" * (module.MAX_FILE_BYTES + 1))
    with pytest.raises((ValueError, OSError)):
        read(checkpoint)


@pytest.mark.parametrize("where", ["root", "token", "programs"])
def test_no_directory_symlink(checkpoint, where):
    root, _token, directory, _ = checkpoint
    target = {"root": root, "token": directory, "programs": directory / "programs"}[
        where
    ]
    moved = target.with_name(target.name + "-moved")
    target.rename(moved)
    target.symlink_to(moved, target_is_directory=True)
    with pytest.raises((OSError, ValueError)):
        read(checkpoint)


@pytest.mark.parametrize("name", ["extra", "programs/not-uuid.json", "programs/a.py"])
def test_extra_inventory_rejected(checkpoint, name):
    (checkpoint[2] / name).write_bytes(b"{}")
    with pytest.raises(ValueError):
        read(checkpoint)


def test_postread_rename_same_bytes_rejected(checkpoint, monkeypatch):
    original = module.os.read
    path = checkpoint[2] / "best_program.py"
    changed = False

    def changing(fd, count):
        nonlocal changed
        raw = original(fd, count)
        if not changed:
            changed = True
            temp = path.with_name("replacement")
            temp.write_bytes(path.read_bytes())
            temp.replace(path)
        return raw

    monkeypatch.setattr(module.os, "read", changing)
    with pytest.raises(ValueError):
        read(checkpoint)


def test_deadline_no_payload_after_expiry(checkpoint):
    with pytest.raises(ValueError):
        module.read_checkpoint(
            checkpoint[0], checkpoint[1], deadline=time.monotonic() - 1
        )


def sender(calls, refuse=None):
    receipt = str(uuid4())

    def send(method, body):
        calls.append((method, body))
        if method == refuse:
            raise OSError("uncertain ACK")
        if method == "checkpoint_upload_begin":
            return {"token": body["token"], "reserved": True}
        if method == "checkpoint_upload_chunk":
            return {key: body[key] for key in ("token", "sequence", "sha256")}
        return {"token": body["token"], "receipt_id": receipt}

    return send, receipt


def test_transfer_declares_every_byte_before_chunks(checkpoint):
    calls = []
    send, receipt = sender(calls)
    assert (
        module.transfer_checkpoint(
            send,
            token=checkpoint[1],
            files=read(checkpoint),
            deadline=time.monotonic() + 10,
        )
        == receipt
    )
    assert (
        calls[0][0] == "checkpoint_upload_begin"
        and calls[-1][0] == "checkpoint_upload_finish"
    )
    assert calls[0][1]["files"] == [
        {
            "name": name,
            "size_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        for name, raw in checkpoint[3]
    ]
    chunks = [body for kind, body in calls if kind == "checkpoint_upload_chunk"]
    assert [x["sequence"] for x in chunks] == list(range(len(chunks)))
    assert calls[-1][1]["chunks"] == len(chunks)
    assert max(len(x["data_base64"]) for x in chunks) <= 87384


@pytest.mark.parametrize(
    "phase",
    ["checkpoint_upload_begin", "checkpoint_upload_chunk", "checkpoint_upload_finish"],
)
def test_uncertain_transfer_never_retries(checkpoint, phase):
    calls = []
    send, _ = sender(calls, phase)
    with pytest.raises(OSError):
        module.transfer_checkpoint(
            send,
            token=checkpoint[1],
            files=read(checkpoint),
            deadline=time.monotonic() + 10,
        )
    assert calls[-1][0] == phase
    assert sum(kind == phase for kind, _ in calls) == 1


def test_false_reservation_ack_sends_no_payload(checkpoint):
    calls = []

    def reject(kind, body):
        calls.append(kind)
        return {"token": body["token"], "reserved": False}

    with pytest.raises(ValueError):
        module.transfer_checkpoint(
            reject,
            token=checkpoint[1],
            files=read(checkpoint),
            deadline=time.monotonic() + 10,
        )
    assert calls == ["checkpoint_upload_begin"]


def test_expiry_after_reservation_keeps_no_more_io(checkpoint, monkeypatch):
    calls = []
    send, _ = sender(calls)
    times = iter((1, 100))
    monkeypatch.setattr(module.time, "monotonic", lambda: next(times))
    with pytest.raises(ValueError):
        module.transfer_checkpoint(
            send, token=checkpoint[1], files=checkpoint[3], deadline=50
        )
    assert [kind for kind, _ in calls] == ["checkpoint_upload_begin"]


@pytest.mark.parametrize("kind", ["duplicate", "order", "extra", "wrongtype"])
def test_transfer_cannot_redeclare_shape(checkpoint, kind):
    files = checkpoint[3]
    if kind == "duplicate":
        files = files + (files[0],)
    elif kind == "order":
        files = tuple(reversed(files))
    elif kind == "extra":
        files = files + (("unexpected", b"x"),)
    else:
        files = list(files)
    calls = []
    send, _ = sender(calls)
    with pytest.raises(ValueError):
        module.transfer_checkpoint(
            send, token=checkpoint[1], files=files, deadline=time.monotonic() + 10
        )
    assert not calls
