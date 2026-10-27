"""Retained actual chunk bytes from upload orchestration; DB authority is doubled."""

import hashlib
from uuid import UUID

import pytest

from backend.app.openevolve_backup_upload import validate_uploads
from packages.openevolve_adapter.upload_contracts import Begin, parse
from runtimes.openevolve_operational.checkpoint_transfer import transfer_checkpoint
from tests.openevolve.test_operational_upload import setup


def case(monkeypatch, tmp_path, *, complete=True):
    c = setup(monkeypatch, tmp_path)
    if complete:
        transfer_checkpoint(c.send, token=c.token, files=c.files, deadline=10**20)
    else:
        c.send("checkpoint_upload_begin", c.begin)
    tables = {
        n: []
        for n in (
            "search_checkpoint_uploads",
            "search_checkpoint_chunks",
            "search_checkpoint_upload_completions",
            "search_checkpoint_intents",
        )
    }
    for value in c.records:
        row = {col.name: getattr(value, col.name) for col in value.__table__.columns}
        row = {k: str(v) if isinstance(v, UUID) else v for k, v in row.items()}
        tables[value.__table__.name].append(row)
    upload = tables["search_checkpoint_uploads"][0]
    scope = {
        k: upload[k]
        for k in ("owner_id", "run_id", "attempt_id", "generation", "epoch_id")
    }
    tables["search_checkpoint_intents"] = [{**scope, "id": str(c.token)}]
    begin = parse(Begin, c.begin)
    budget = {
        **scope,
        "id": upload["reservation_id"],
        "purpose": "object_storage",
        "logical_key": "oe-upload:" + str(c.token),
        "state": "reserved",
        "amounts": {
            "calls": 0,
            "cost_microunits": 0,
            "tokens": 0,
            "wall_milliseconds": 0,
            "storage_bytes": begin.transfer_peak_bytes,
            "mutation_slots": 0,
            "evaluator_executions": 0,
            "process_slots": 0,
        },
    }
    research = {"tables": {"research_budget_reservations": [budget]}}
    objects = {str(o.id): o for o in c.publications.values()}

    def read(owner, oid, kind, sha):
        obj = objects[oid]
        assert (
            owner == str(c.fence.owner_id)
            and obj.object_kind == kind
            and obj.sha256 == sha
        )
        raw = (tmp_path / oid).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == sha
        return raw

    return tables, research, read


@pytest.mark.parametrize("complete", [False, True])
def test_pending_and_complete_keep_exact_transfer_capacity(
    monkeypatch, tmp_path, complete
):
    validate_uploads(*case(monkeypatch, tmp_path, complete=complete))


@pytest.mark.parametrize(
    "damage",
    [
        "capacity",
        "pending_release",
        "count",
        "hash",
        "sequence",
        "missing_chunk",
        "completion_scope",
    ],
)
def test_transfer_cannot_be_rebound_or_undercharged(monkeypatch, tmp_path, damage):
    tables, research, read = case(
        monkeypatch, tmp_path, complete=damage != "pending_release"
    )
    upload = tables["search_checkpoint_uploads"][0]
    if damage == "capacity":
        research["tables"]["research_budget_reservations"][0]["amounts"][
            "storage_bytes"
        ] -= 1
    elif damage == "pending_release":
        research["tables"]["research_budget_reservations"][0]["state"] = "settled"
    elif damage == "count":
        upload["chunk_count"] -= 1
    elif damage == "hash":
        upload["inventory_sha256"] = "0" * 64
    elif damage == "sequence":
        tables["search_checkpoint_chunks"][0]["sequence"] = 40
    elif damage == "missing_chunk":
        tables["search_checkpoint_chunks"].pop()
    else:
        tables["search_checkpoint_upload_completions"][0]["generation"] += 1
    with pytest.raises(ValueError):
        validate_uploads(tables, research, read)
