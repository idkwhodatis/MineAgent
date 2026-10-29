"""Actual bounded JSON checkpoint files; transport ACKs remain explicit doubles."""

import time
from uuid import uuid4

import pytest

from runtimes.openevolve_structured.checkpoint_transfer import (
    read_checkpoint,
    transfer_checkpoint,
)


def files(tmp_path):
    token = uuid4()
    root = tmp_path / str(token)
    (root / "programs").mkdir(parents=True)
    contents = {
        name: b"{}"
        for name in ("metadata.json", "best_program.json", "best_program_info.json")
    }
    contents["programs/" + str(uuid4()) + ".json"] = b"{}"
    for name, raw in contents.items():
        (root / name).write_bytes(raw)
    return token, root, tuple(sorted(contents.items()))


def test_actual_json_checkpoint_inventory_not_python(tmp_path):
    token, root, expected = files(tmp_path)
    assert read_checkpoint(tmp_path, token, deadline=time.monotonic() + 5) == expected
    (root / "best_program.json").rename(root / "best_program.py")
    with pytest.raises(ValueError):
        read_checkpoint(tmp_path, token, deadline=time.monotonic() + 5)


def test_transfer_keeps_uncertain_ack_no_retry(tmp_path):
    token, _root, expected = files(tmp_path)
    calls = []

    def send(kind, body):
        calls.append(kind)
        raise ConnectionError("uncertain delivery")

    with pytest.raises(ConnectionError):
        transfer_checkpoint(
            send, token=token, files=expected, deadline=time.monotonic() + 5
        )
    assert calls == ["checkpoint_upload_begin"]


def test_expired_original_deadline_never_reads(tmp_path):
    token, _root, _expected = files(tmp_path)
    with pytest.raises(ValueError):
        read_checkpoint(tmp_path, token, deadline=time.monotonic() - 1)
