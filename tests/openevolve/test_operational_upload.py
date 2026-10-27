"""Actual upload orchestration/codecs with explicit DB/authority/publisher doubles.

No PostgreSQL or physical publication acceptance is claimed by these tests.
"""

import base64
import hashlib
from contextlib import contextmanager
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import openevolve_upload_quota as quota
from backend.app import openevolve_upload_services as s
from backend.app.openevolve_operational_models import (
    SearchCheckpointChunk,
    SearchCheckpointUploadCompletion,
)
from packages.openevolve_adapter.object_identity import parse_document
from packages.openevolve_adapter.upload_contracts import Begin, parse
from runtimes.openevolve_operational.checkpoint_transfer import transfer_checkpoint


def setup(monkeypatch, tmp_path):
    fence = NS(owner_id=uuid4(), run_id=uuid4(), attempt_id=uuid4(), generation=1)
    epoch, token, control = uuid4(), uuid4(), uuid4()
    intent = NS(id=token, epoch_id=epoch, control_transition_id=control)
    records = []
    publications = {}
    calls = []

    class Session:
        def add(self, row):
            records.append(row)

        def flush(self):
            calls.append("flush")

        def scalar(self, query):
            entity = query.column_descriptions[0]["entity"]
            return next((r for r in records if type(r) is entity), None)

        def scalars(self, query):
            entity = query.column_descriptions[0]["entity"]
            return NS(
                all=lambda: sorted(
                    (r for r in records if type(r) is entity), key=lambda r: r.sequence
                )
            )

    session = Session()

    @contextmanager
    def transaction(*args, **kwargs):
        calls.append("txn")
        yield session
        calls.append("commit")

    def authority(*args, **kwargs):
        calls.append("authority")

    def owned(*args):
        return intent

    def budget(*args, **kwargs):
        calls.append(("hold", kwargs["amounts"].storage_bytes))
        return NS(id=uuid4())

    def publish(factory, fence, *, kind, content, logical_key, settings):
        parse_document(kind, content)
        sha = hashlib.sha256(content).hexdigest()
        if logical_key in publications:
            obj = publications[logical_key]
            assert obj.sha256 == sha
            return obj
        obj = NS(id=uuid4(), object_kind=kind, sha256=sha)
        publications[logical_key] = obj
        (tmp_path / str(obj.id)).write_bytes(content)
        calls.append(("write", kind))
        return obj

    def read(session, fence, oid, kind, sha, settings):
        raw = (tmp_path / str(oid)).read_bytes()
        if hashlib.sha256(raw).hexdigest() != sha:
            raise ValueError("retained hash drift")
        parse_document(kind, raw)
        return raw

    monkeypatch.setattr(s, "neutral_transaction", transaction)
    monkeypatch.setattr(s, "require_search_controller", authority)
    monkeypatch.setattr(s, "_owned_row", owned)
    monkeypatch.setattr(s, "_latest_control", lambda *_: NS(id=control))
    monkeypatch.setattr(s, "reserve_budget", budget)
    monkeypatch.setattr(
        quota, "require_upload_capacity", lambda *_: calls.append("capacity")
    )
    monkeypatch.setattr(s, "publish_search_object", publish)
    monkeypatch.setattr(s, "read_research_object", read)

    def send(kind, payload):
        fn = {
            "checkpoint_upload_begin": s.begin_upload,
            "checkpoint_upload_chunk": s.accept_chunk,
            "checkpoint_upload_finish": s.finish_upload,
        }[kind]
        return fn(None, fence, object(), epoch_id=epoch, payload=payload, settings=None)

    files = tuple(
        sorted(
            {
                "metadata.json": b"{}",
                "best_program.py": b"print(1)",
                "best_program_info.json": b"{}",
                f"programs/{uuid4()}.json": b"X" * 65537,
            }.items()
        )
    )
    begin = {
        "token": str(token),
        "files": [
            {
                "name": n,
                "size_bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
            for n, raw in files
        ],
    }
    return NS(
        fence=fence,
        epoch=epoch,
        token=token,
        files=files,
        begin=begin,
        send=send,
        records=records,
        publications=publications,
        calls=calls,
        root=tmp_path,
    )


def test_complete_real_transfer_and_exact_replays(monkeypatch, tmp_path):
    c = setup(monkeypatch, tmp_path)
    receipt = transfer_checkpoint(c.send, token=c.token, files=c.files, deadline=10**20)
    assert len([r for r in c.records if type(r) is SearchCheckpointChunk]) == 5
    assert (
        len([r for r in c.records if type(r) is SearchCheckpointUploadCompletion]) == 1
    )
    assert next(
        i for i, x in enumerate(c.calls) if type(x) is tuple and x[0] == "hold"
    ) < next(i for i, x in enumerate(c.calls) if type(x) is tuple and x[0] == "write")
    writes = sum(type(x) is tuple and x[0] == "write" for x in c.calls)
    assert (
        transfer_checkpoint(c.send, token=c.token, files=c.files, deadline=10**20)
        == receipt
    )
    assert sum(type(x) is tuple and x[0] == "write" for x in c.calls) == writes
    completion = next(
        r for r in c.records if type(r) is SearchCheckpointUploadCompletion
    )
    document = parse_document(
        "search_checkpoint_inventory",
        (tmp_path / str(completion.inventory_object_id)).read_bytes(),
    )
    assert [(x.name, x.file_sha256, x.size_bytes) for x in document.files] == [
        (n, hashlib.sha256(raw).hexdigest(), len(raw)) for n, raw in c.files
    ]


def chunk(c, seq=0):
    b = parse(Begin, c.begin)
    name, offset, size = b.chunks[seq]
    raw = dict(c.files)[name][offset : offset + size]
    return {
        "token": str(c.token),
        "sequence": seq,
        "name": name,
        "offset": offset,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "data_base64": base64.b64encode(raw).decode(),
    }


@pytest.mark.parametrize(
    "mode",
    [
        "no_begin",
        "gap",
        "wrong_name",
        "wrong_size",
        "wrong_hash",
        "bool_sequence",
        "wrong_offset",
        "wrong_token",
    ],
)
def test_invalid_chunk_has_no_publication(monkeypatch, tmp_path, mode):
    c = setup(monkeypatch, tmp_path)
    payload = chunk(c)
    if mode != "no_begin":
        c.send("checkpoint_upload_begin", c.begin)
    if mode == "gap":
        payload = chunk(c, 1)
    if mode == "wrong_name":
        payload["name"] = "other.json"
    if mode == "wrong_size":
        payload["data_base64"] = "YQ=="
        payload["sha256"] = hashlib.sha256(b"a").hexdigest()
    if mode == "wrong_hash":
        payload["sha256"] = "0" * 64
    if mode == "bool_sequence":
        payload["sequence"] = False
    if mode == "wrong_offset":
        payload["offset"] = 1
    if mode == "wrong_token":
        payload["token"] = str(uuid4())
    with pytest.raises(ValueError):
        c.send("checkpoint_upload_chunk", payload)
    assert not c.publications


def test_missing_chunks_cannot_finish(monkeypatch, tmp_path):
    c = setup(monkeypatch, tmp_path)
    c.send("checkpoint_upload_begin", c.begin)
    with pytest.raises(ValueError, match="missing chunks"):
        c.send("checkpoint_upload_finish", {"token": str(c.token), "chunks": 5})
    assert not c.publications
    assert len(c.records) == 1  # hold retained


def test_conflicting_completed_chunk_has_no_new_effect(monkeypatch, tmp_path):
    c = setup(monkeypatch, tmp_path)
    transfer_checkpoint(c.send, token=c.token, files=c.files, deadline=10**20)
    p = chunk(c)
    raw = b"X" * len(base64.b64decode(p["data_base64"]))
    p.update(
        data_base64=base64.b64encode(raw).decode(),
        sha256=hashlib.sha256(raw).hexdigest(),
    )
    before = len(c.publications)
    with pytest.raises(ValueError, match="replay drift"):
        c.send("checkpoint_upload_chunk", p)
    assert len(c.publications) == before


def test_corrupt_retained_chunk_stops_before_assembly(monkeypatch, tmp_path):
    c = setup(monkeypatch, tmp_path)
    c.send("checkpoint_upload_begin", c.begin)
    for seq in range(5):
        c.send("checkpoint_upload_chunk", chunk(c, seq))
    row = next(r for r in c.records if type(r) is SearchCheckpointChunk)
    (tmp_path / str(row.object_id)).write_bytes(b"{}")
    with pytest.raises(ValueError, match="hash drift"):
        c.send("checkpoint_upload_finish", {"token": str(c.token), "chunks": 5})
    assert all(
        o.object_kind == "search_checkpoint_chunk" for o in c.publications.values()
    )


def test_begin_conflict_rejects_before_budget(monkeypatch, tmp_path):
    c = setup(monkeypatch, tmp_path)
    c.send("checkpoint_upload_begin", c.begin)
    c.begin["files"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="replay drift"):
        c.send("checkpoint_upload_begin", c.begin)
    assert len(c.records) == 1


def test_original_admission_lost_no_payload_write(monkeypatch, tmp_path):
    c = setup(monkeypatch, tmp_path)
    c.send("checkpoint_upload_begin", c.begin)

    def deny(*args, **kwargs):
        raise ValueError("lost live peer")

    monkeypatch.setattr(s, "require_search_controller", deny)
    with pytest.raises(ValueError, match="live peer"):
        c.send("checkpoint_upload_chunk", chunk(c))
    assert not c.publications
    assert len(c.records) == 1
