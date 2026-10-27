"""Explicit021 typed execution artifacts; all historical object grammars unchanged."""

import hashlib
import json
import re
from uuid import UUID

from packages.rdagent_adapter import object_identity as prior
from packages.research_runtime import object_identity as neutral

KINDS = frozenset(
    {
        "factor_generation_proposal",
        "factor_generation_feedback",
        "factor_adoption_receipt",
    }
)
SCHEMA = "mineagent.rd-execution-publication.v1"


def object_identity(owner, kind, sha):
    if kind not in KINDS:
        return prior.object_identity(owner, kind, sha)
    if (
        type(owner) is not UUID
        or type(sha) is not str
        or neutral.SHA.fullmatch(sha) is None
    ):
        raise ValueError("Exact typed execution identity required")
    return neutral.ObjectIdentity(
        owner, kind, sha, f"research-objects/{owner}/by-kind/{kind}/{sha}.json"
    )


def identity_for_content(owner, kind, content):
    if kind not in KINDS:
        return prior.identity_for_content(owner, kind, content)
    from packages.rdagent_adapter.execution_outcomes import parse_document

    document = parse_document(kind, content)
    if document.owner_id != owner:
        raise ValueError("Execution artifact owner differs")
    return object_identity(owner, kind, hashlib.sha256(content).hexdigest())


def parse_storage_key(key):
    if type(key) is not str:
        raise ValueError("Exact storage key required")
    pattern = (
        rf"research-objects/({neutral.UUID_TEXT})/by-kind/({'|'.join(sorted(KINDS))})/(\.?)"
        + r"([0-9a-f]{64})\.json(?:\.([0-9a-f]{32})\.tmp)?"
    )
    match = re.fullmatch(pattern, key)
    if match:
        owner, kind, dot, sha, temporary = match.groups()
        if bool(dot) != bool(temporary):
            raise ValueError("Invalid execution temporary identity")
        return "object", UUID(owner), kind, sha, bool(temporary)
    return prior.parse_storage_key(key)


def publication_journal(publication):
    if publication.object_kind not in KINDS:
        return prior.publication_journal(publication)
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
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    validate_publication_journal(raw)
    return raw


def validate_publication_journal(raw):
    value = json.loads(neutral.canonical_object(raw))
    if value.get("schema_version") != SCHEMA:
        return prior.validate_publication_journal(raw)
    if (
        len(raw) > neutral.MAX_JOURNAL_BYTES
        or value.get("object_kind") not in KINDS
        or not 1 <= value.get("size_bytes", 0) <= 65536
    ):
        raise ValueError("Invalid typed execution journal")
    projected = dict(
        value, schema_version=neutral.PUBLICATION_SCHEMA, object_kind="model_response"
    )
    projected["storage_key"] = (
        f"research-objects/{value['owner_id']}/by-kind/model_response/{value['sha256']}.json"
    )
    neutral.validate_publication_journal(
        json.dumps(projected, sort_keys=True, separators=(",", ":")).encode()
    )
    expected = object_identity(
        UUID(value["owner_id"]), value["object_kind"], value["sha256"]
    )
    if value["storage_key"] != expected.storage_key:
        raise ValueError("Execution journal address differs")
    return value
