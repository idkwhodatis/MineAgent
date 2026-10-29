"""027-only checkpoint object grammar; no execution or publication authority.

The assembler must explicitly dispatch these kinds only for the installed027
schema. Old search/Python object kinds are never accepted by this module.
"""

import hashlib
import json
import re
from uuid import UUID

from packages.openevolve_structured.checkpoint import canonical
from packages.openevolve_structured.upload_contracts import (
    ChunkDocument,
    FileEnvelope,
    Inventory,
)
from packages.research_runtime import object_identity as neutral

MODELS = {
    "oe_structured_checkpoint_chunk": ChunkDocument,
    "oe_structured_checkpoint_file": FileEnvelope,
    "oe_structured_checkpoint_inventory": Inventory,
}
KINDS = frozenset(MODELS)
SCHEMA = "mineagent.oe-structured-checkpoint-publication.v1"


def parse_document(kind, content):
    neutral.canonical_object(content)
    if kind not in MODELS:
        raise ValueError("Exact structured checkpoint object kind required")
    document = MODELS[kind].model_validate_json(content)
    if canonical(document.model_dump(mode="json")) != content:
        raise ValueError("Canonical structured checkpoint envelope required")
    return document


def object_identity(owner, kind, sha):
    if (
        type(owner) is not UUID
        or kind not in KINDS
        or type(sha) is not str
        or neutral.SHA.fullmatch(sha) is None
    ):
        raise ValueError("Exact structured checkpoint identity required")
    return neutral.ObjectIdentity(
        owner, kind, sha, f"research-objects/{owner}/by-kind/{kind}/{sha}.json"
    )


def identity_for_content(owner, kind, content):
    document = parse_document(kind, content)
    if hasattr(document, "owner_id") and document.owner_id != owner:
        raise ValueError("Checkpoint artifact owner differs")
    return object_identity(owner, kind, hashlib.sha256(content).hexdigest())


def parse_storage_key(key):
    if type(key) is not str:
        raise ValueError("Exact storage key required")
    pattern = (
        rf"research-objects/({neutral.UUID_TEXT})/by-kind/({'|'.join(sorted(KINDS))})/(\.?)"
        + r"([0-9a-f]{64})\.json(?:\.([0-9a-f]{32})\.tmp)?"
    )
    match = re.fullmatch(pattern, key)
    if match is None:
        raise ValueError("Not a structured checkpoint storage key")
    owner, kind, dot, sha, temporary = match.groups()
    if bool(dot) != bool(temporary):
        raise ValueError("Invalid checkpoint temporary identity")
    return "object", UUID(owner), kind, sha, bool(temporary)


def publication_journal(publication):
    value = {
        "schema_version": SCHEMA,
        **{
            k: str(getattr(publication, k))
            for k in ("id", "owner_id", "run_id", "attempt_id", "budget_reservation_id")
        },
        "generation": publication.generation,
        "object_kind": publication.object_kind,
        "sha256": publication.sha256,
        "size_bytes": publication.size_bytes,
        "storage_key": publication.storage_key,
    }
    raw = canonical(value)
    validate_publication_journal(raw)
    return raw


def validate_publication_journal(raw):
    value = json.loads(neutral.canonical_object(raw))
    if (
        value.get("schema_version") != SCHEMA
        or len(raw) > neutral.MAX_JOURNAL_BYTES
        or value.get("object_kind") not in KINDS
        or type(value.get("size_bytes")) is not int
        or not 1 <= value["size_bytes"] <= neutral.MAX_OBJECT_BYTES
    ):
        raise ValueError("Invalid structured checkpoint journal")
    projected = dict(
        value, schema_version=neutral.PUBLICATION_SCHEMA, object_kind="model_response"
    )
    projected["storage_key"] = (
        f"research-objects/{value['owner_id']}/by-kind/model_response/{value['sha256']}.json"
    )
    neutral.validate_publication_journal(canonical(projected))
    if (
        value["storage_key"]
        != object_identity(
            UUID(value["owner_id"]), value["object_kind"], value["sha256"]
        ).storage_key
    ):
        raise ValueError("Checkpoint journal address differs")
    return value
