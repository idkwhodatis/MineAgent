"""Historical selection survives later edit/disable; no active-run authorization."""

from copy import deepcopy
from hashlib import sha256
from uuid import uuid4

import pytest

from backend.app import rdagent_steer_backup as backup
from packages.factors.contracts import canonical_bytes
from packages.rdagent_adapter.steer import SteerSnapshot, SteerVersion


@pytest.fixture
def graph():
    owner, steer = uuid4(), uuid4()
    docs = [
        SteerVersion(
            steer_id=steer,
            version_id=uuid4(),
            ordinal=ordinal,
            kind="hypothesis",
            text=value,
            text_sha256=sha256(value.encode()).hexdigest(),
        )
        for ordinal, value in enumerate(
            ["保留的假设\nα", "Revised future-run hypothesis"], 1
        )
    ]
    stamp = "2026-10-26T00:00:00+00:00"
    snapshot = SteerSnapshot(owner_id=owner, versions=(docs[0],))
    closure = {
        "schema_version": backup.SCHEMA,
        "owners": [{"id": str(owner)}],
        "tables": {
            "rd_steers": [
                {
                    "id": str(steer),
                    "owner_id": str(owner),
                    "current_version_id": str(docs[1].version_id),
                    "disabled": True,
                    "revision": 3,
                    "created_at": stamp,
                    "updated_at": stamp,
                }
            ],
            "rd_steer_versions": [
                {
                    "id": str(doc.version_id),
                    "owner_id": str(owner),
                    "steer_id": str(steer),
                    "ordinal": doc.ordinal,
                    "kind": doc.kind,
                    "text": doc.text,
                    "text_sha256": doc.text_sha256,
                    "content_canonical": canonical_bytes(doc).decode(),
                    "content_sha256": doc.sha256,
                    "created_at": stamp,
                }
                for doc in docs
            ],
        },
    }
    rd = {
        "schema_version": "mineagent.rd-source-restore-closure.v5",
        "external_bindings": {
            "runs": [
                {
                    "id": str(uuid4()),
                    "owner_id": str(owner),
                    "config": {
                        "schema_version": "mineagent.factor-research.v2",
                        "steer_snapshot": snapshot.model_dump(mode="json"),
                        "steer_snapshot_sha256": snapshot.sha256,
                    },
                }
            ]
        },
    }
    return closure, rd


def test_old_run_keeps_original_unicode_text_after_edit_and_disable(graph):
    closure, rd = graph
    backup.validate_closure(closure, rd_source_closure=rd, maximum_rows=100)
    assert (
        rd["external_bindings"]["runs"][0]["config"]["steer_snapshot"]["versions"][0][
            "text"
        ]
        == "保留的假设\nα"
    )
    closure["tables"]["rd_steers"][0]["disabled"] = False
    backup.validate_closure(closure, rd_source_closure=rd, maximum_rows=100)


@pytest.mark.parametrize(
    "mutation",
    [
        "owner",
        "text",
        "canonical",
        "hash",
        "missing",
        "head",
        "ordinal",
        "snapshot_hash",
        "snapshot_owner",
        "duplicate",
        "future",
    ],
)
def test_forged_steer_history_is_not_a_restore_grant(graph, mutation):
    closure, rd = deepcopy(graph)
    root = closure["tables"]["rd_steers"][0]
    versions = closure["tables"]["rd_steer_versions"]
    config = rd["external_bindings"]["runs"][0]["config"]
    if mutation == "owner":
        versions[0]["owner_id"] = str(uuid4())
    elif mutation == "text":
        versions[0]["text"] = "substituted"
    elif mutation == "canonical":
        versions[0]["content_canonical"] += " "
    elif mutation == "hash":
        versions[0]["content_sha256"] = "f" * 64
    elif mutation == "missing":
        versions.pop(0)
    elif mutation == "head":
        root["current_version_id"] = versions[0]["id"]
    elif mutation == "ordinal":
        versions[0]["ordinal"] = True
    elif mutation == "snapshot_hash":
        config["steer_snapshot_sha256"] = "f" * 64
    elif mutation == "snapshot_owner":
        rd["external_bindings"]["runs"][0]["owner_id"] = str(uuid4())
    elif mutation == "duplicate":
        versions.append(deepcopy(versions[0]))
    else:
        rd["schema_version"] = "mineagent.rd-source-restore-closure.v7"
    with pytest.raises(ValueError):
        backup.validate_closure(closure, rd_source_closure=rd, maximum_rows=100)


@pytest.mark.parametrize("profile", [None, "mineagent.rdagent-orchestrator.v3"])
def test_new_source_preserves_retained_steer_selection(graph, profile):
    closure, rd = graph
    rd["schema_version"] = "mineagent.rd-source-restore-closure.v6"
    if profile is not None:
        rd["external_bindings"]["runs"][0]["config"]["controller_profile"] = profile
    backup.validate_closure(closure, rd_source_closure=rd, maximum_rows=100)


def test_old_source_rejects_new_profile_even_with_valid_steer(graph):
    closure, rd = graph
    rd["external_bindings"]["runs"][0]["config"]["controller_profile"] = (
        "mineagent.rdagent-orchestrator.v3"
    )
    with pytest.raises(ValueError, match="Controller profile"):
        backup.validate_closure(closure, rd_source_closure=rd, maximum_rows=100)
