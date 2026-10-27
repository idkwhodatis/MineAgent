"""Central cold reader/terminal barriers; no complete physical acceptance claim."""

import hashlib
from uuid import uuid4

import pytest

from backend.app.openevolve_backup import registered_reader, validate_terminal_closure


def test_registered_reader_uses_exact_retained_bytes_and_uuid_normalization():
    owner, identity = uuid4(), uuid4()
    raw = b'{"sample":1}'
    sha = hashlib.sha256(raw).hexdigest()
    row = {
        "id": str(identity),
        "owner_id": str(owner),
        "object_kind": "search_candidate",
        "sha256": sha,
        "size_bytes": len(raw),
        "storage_key": "registered-only",
    }
    calls = []

    def read(key, digest, cap):
        calls.append((key, digest, cap))
        return raw

    reader = registered_reader({"tables": {"research_objects": [row]}}, read)
    assert reader(owner, identity, row["object_kind"], sha) == raw
    assert calls == [(row["storage_key"], sha, len(raw))]
    with pytest.raises(ValueError):
        reader(owner, uuid4(), row["object_kind"], sha)
    assert len(calls) == 1


@pytest.mark.parametrize("returned", [b"changed", b"", '{"sample":1}'])
def test_retained_reader_never_trusts_metadata_hash_without_bytes(returned):
    owner, identity = str(uuid4()), str(uuid4())
    raw = b'{"sample":1}'
    sha = hashlib.sha256(raw).hexdigest()
    row = {
        "id": identity,
        "owner_id": owner,
        "object_kind": "search_candidate",
        "sha256": sha,
        "size_bytes": len(raw),
        "storage_key": "registered",
    }
    reader = registered_reader(
        {"tables": {"research_objects": [row]}}, lambda *a: returned
    )
    with pytest.raises(ValueError, match="bytes"):
        reader(owner, identity, row["object_kind"], sha)


def test_success_cannot_use_only_historical_checkpoint_qualification():
    run, epoch = str(uuid4()), str(uuid4())
    closure = {
        "external_bindings": {"runs": [{"id": run, "status": "succeeded"}]},
        "tables": {
            "search_iteration_intents": [],
            "strategy_search_epochs": [{"id": epoch, "run_id": run}],
        },
    }
    research = {
        "tables": {
            "research_budget_reservations": [],
            "research_budget_settlements": [],
        }
    }
    with pytest.raises(ValueError, match="highest actual save"):
        validate_terminal_closure(closure, research, frozenset())
