"""Actual retained byte grammar + transaction doubles; not PostgreSQL/Docker."""

import base64
import hashlib
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import oe_structured_checkpoint_services as service
from packages.openevolve_structured.checkpoint import canonical, validate_directory
from packages.openevolve_structured.checkpoint_object_identity import (
    identity_for_content,
    parse_document,
)
from packages.openevolve_structured.upload_contracts import (
    Begin,
    Chunk,
    ChunkDocument,
    FileEnvelope,
    parse,
)
from packages.research_runtime.process_contracts import ProcessError
from tests.openevolve.test_structured_checkpoint import domain, native  # noqa: F401


@pytest.fixture
def files(native):  # noqa: F811
    return {
        p.relative_to(native).as_posix(): p.read_bytes()
        for p in native.rglob("*")
        if p.is_file()
    }


def test_actual_json_checkpoint_bytes_not_python_profile(files, domain, native):  # noqa: F811
    assert service.native_files_state(files, domain) == validate_directory(
        native, domain
    )
    wrong = dict(files)
    wrong["best_program.py"] = wrong.pop("best_program.json")
    with pytest.raises((ValueError, ProcessError)):
        service.native_files_state(wrong, domain)


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "extra",
        "best",
        "saved_nan",
        "saved_bool",
        "score",
        "code",
        "duplicate",
    ],
)
def test_native_checkpoint_substitutions(files, domain, change):  # noqa: F811
    if change == "missing":
        del files["metadata.json"]
    elif change == "extra":
        files["sidecar"] = b"x"
    elif change == "best":
        files["best_program.json"] += b" "
    elif change in {"saved_nan", "saved_bool"}:
        value = __import__("json").loads(files["best_program_info.json"])
        value["saved_at"] = True if change == "saved_bool" else float("inf")
        files["best_program_info.json"] = __import__("json").dumps(value).encode()
    elif change == "duplicate":
        files["metadata.json"] = b'{"islands":[],"islands":[]}'
    else:
        name = next(n for n in files if n.startswith("programs/"))
        value = __import__("json").loads(files[name])
        if change == "score":
            value["metrics"]["combined_score"] = 999
        else:
            value["code"] = "print(1)"
        files[name] = canonical(value)
    with pytest.raises((ValueError, ProcessError)):
        service.native_files_state(files, domain)


def test_opaque_native_spacing_preserved_in_envelope(files):
    raw = files["metadata.json"]
    envelope = FileEnvelope(
        name="metadata.json",
        size_bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        data_base64=base64.b64encode(raw).decode(),
    )
    encoded = canonical(envelope.model_dump(mode="json"))
    assert parse_document("oe_structured_checkpoint_file", encoded).bytes() == raw
    owner = uuid4()
    identity = identity_for_content(owner, "oe_structured_checkpoint_file", encoded)
    assert str(owner) in identity.storage_key
    with pytest.raises(ValueError):
        parse_document("search_checkpoint_file", encoded)


def test_chunk_exact_order_and_original_bytes(files):
    token = uuid4()
    begin = parse(
        Begin,
        {
            "token": str(token),
            "files": [
                {
                    "name": name,
                    "size_bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
                for name, raw in sorted(files.items())
            ],
        },
    )
    name, offset, size = begin.chunks[0]
    raw = files[name][offset : offset + size]
    chunk = Chunk(
        token=token,
        sequence=0,
        name=name,
        offset=offset,
        sha256=hashlib.sha256(raw).hexdigest(),
        data_base64=base64.b64encode(raw).decode(),
    )
    chunk.require_inventory(begin)
    document = ChunkDocument(
        owner_id=uuid4(),
        run_id=uuid4(),
        attempt_id=uuid4(),
        generation=1,
        epoch_id=uuid4(),
        upload_id=uuid4(),
        chunk=chunk,
    )
    encoded = canonical(document.model_dump(mode="json"))
    assert (
        parse_document("oe_structured_checkpoint_chunk", encoded).chunk.bytes() == raw
    )
    with pytest.raises(ValueError):
        identity_for_content(uuid4(), "oe_structured_checkpoint_chunk", encoded)
    with pytest.raises(ValueError):
        chunk.model_copy(update={"sequence": 1}).require_inventory(begin)


@pytest.mark.parametrize(
    "changes",
    [
        {"sequence": True},
        {"data_base64": "!!!!"},
        {"sha256": "0" * 64},
        {"name": "../metadata.json"},
    ],
)
def test_transfer_rejects_forged_chunk(changes):
    value = {
        "token": str(uuid4()),
        "sequence": 0,
        "name": "metadata.json",
        "offset": 0,
        "sha256": hashlib.sha256(b"x").hexdigest(),
        "data_base64": "eA==",
    }
    value.update(changes)
    with pytest.raises(ValueError):
        parse(Chunk, value)


@pytest.fixture
def prepare_case(monkeypatch, files, domain):  # noqa: F811

    scope = {
        "owner_id": uuid4(),
        "run_id": uuid4(),
        "attempt_id": uuid4(),
        "generation": 1,
    }
    fence = NS(**scope)
    epoch = uuid4()
    state = service.native_files_state(files, domain)
    cursor = NS(
        **scope,
        id=uuid4(),
        epoch_id=epoch,
        frontier=0,
        state_sha256=service.state_sha256(state),
    )
    rows, published, calls = [], [], []
    session = NS(
        scalars=lambda _: rows[:],
        scalar=lambda _: published[-1] if published else None,
        add=rows.append,
        flush=lambda: None,
    )
    context = NS(config=NS(search=NS(domain=domain)))
    monkeypatch.setattr(service, "controller", lambda *a: context)
    monkeypatch.setattr(
        service, "current_state", lambda *a: calls.append("physical") or (cursor, state)
    )
    payload = {"frontier": 0, "state_sha256": cursor.state_sha256, "save_ordinal": 1}
    return NS(**locals())


def test_distinct_real_saves_same_cursor_and_exact_retry(prepare_case):
    c = prepare_case
    one = service.prepare_checkpoint(
        c.session, c.fence, c.epoch, canonical(c.payload), None, peer=None
    )
    assert (
        service.prepare_checkpoint(
            c.session, c.fence, c.epoch, canonical(c.payload), None, peer=None
        ).id
        == one.id
    )
    c.published.append(
        NS(
            id=uuid4(),
            **c.scope,
            epoch_id=c.epoch,
            intent_id=one.id,
            control_state_id=one.control_state_id,
            state_sha256=one.state_sha256,
        )
    )
    two = service.prepare_checkpoint(
        c.session,
        c.fence,
        c.epoch,
        canonical(dict(c.payload, save_ordinal=2)),
        None,
        peer=None,
    )
    assert two.id != one.id and two.control_state_id == one.control_state_id
    assert c.calls == ["physical", "physical"]


@pytest.mark.parametrize("case", ["gap", "conflict", "unpublished_previous"])
def test_save_sequence_conflicts_rejected(prepare_case, case):
    c = prepare_case
    service.prepare_checkpoint(
        c.session, c.fence, c.epoch, canonical(c.payload), None, peer=None
    )
    change = (
        {"save_ordinal": 3}
        if case == "gap"
        else ({"state_sha256": "f" * 64} if case == "conflict" else {"save_ordinal": 2})
    )
    with pytest.raises(ProcessError):
        service.prepare_checkpoint(
            c.session,
            c.fence,
            c.epoch,
            canonical(dict(c.payload, **change)),
            None,
            peer=None,
        )
