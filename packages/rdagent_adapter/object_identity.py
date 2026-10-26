"""Revision018 explicit assembly identity; historical neutral codecs stay intact."""

import hashlib
import json
import re
from uuid import UUID

from packages.rdagent_adapter.host_evidence import ResponseAssemblyDocument
from packages.research_runtime import object_identity as old

KIND = "factor_response_assembly"
SCHEMA = "mineagent.rd-assembly-publication.v1"


def object_identity(owner, kind, sha):
    if kind != KIND:
        return old.object_identity(owner, kind, sha)
    if (
        type(owner) is not UUID
        or type(sha) is not str
        or old.SHA.fullmatch(sha) is None
    ):
        raise ValueError("Exact assembly identity required")
    return old.ObjectIdentity(
        owner, KIND, sha, f"research-objects/{owner}/by-kind/{KIND}/{sha}.json"
    )


def identity_for_content(owner, kind, content):
    if kind != KIND:
        return old.identity_for_content(owner, kind, content)
    if type(owner) is not UUID:
        raise ValueError("Exact assembly owner required")
    old.canonical_object(content)
    document = ResponseAssemblyDocument.model_validate_json(content)
    canonical = json.dumps(
        document.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    if content != canonical:
        raise ValueError("Exact full canonical assembly document required")
    sha = hashlib.sha256(content).hexdigest()
    return old.ObjectIdentity(
        owner, KIND, sha, f"research-objects/{owner}/by-kind/{KIND}/{sha}.json"
    )


def parse_storage_key(key):
    if type(key) is not str:
        raise ValueError("Exact storage key required")
    pattern = (
        rf"research-objects/({old.UUID_TEXT})/by-kind/{KIND}/(\.?)"
        + r"([0-9a-f]{64})\.json(?:\.([0-9a-f]{32})\.tmp)?"
    )
    match = re.fullmatch(pattern, key)
    if match:
        owner, dot, sha, temporary = match.groups()
        if bool(dot) != bool(temporary):
            raise ValueError("Invalid assembly temporary identity")
        return "object", UUID(owner), KIND, sha, bool(temporary)
    return old.parse_storage_key(key)


def publication_journal(publication):
    if publication.object_kind != KIND:
        return old.publication_journal(publication)
    value = {
        "schema_version": SCHEMA,
        **{
            key: str(getattr(publication, key))
            for key in (
                "id",
                "owner_id",
                "run_id",
                "attempt_id",
                "budget_reservation_id",
            )
        },
        "generation": publication.generation,
        "object_kind": KIND,
        "sha256": publication.sha256,
        "size_bytes": publication.size_bytes,
        "storage_key": publication.storage_key,
    }
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    validate_publication_journal(raw)
    return raw


def validate_publication_journal(raw):
    value = json.loads(old.canonical_object(raw))
    if value.get("schema_version") != SCHEMA:
        return old.validate_publication_journal(raw)
    if len(raw) > old.MAX_JOURNAL_BYTES or value.get("object_kind") != KIND:
        raise ValueError("Invalid assembly journal")
    # Validate the unchanged common grammar through the historical validator with
    # an explicit projected neutral identity; the original new envelope is retained.
    projected = dict(
        value, schema_version=old.PUBLICATION_SCHEMA, object_kind="model_response"
    )
    original_key = value["storage_key"]
    projected["storage_key"] = (
        f"research-objects/{value['owner_id']}/by-kind/model_response/{value['sha256']}.json"
    )
    old.validate_publication_journal(
        json.dumps(projected, sort_keys=True, separators=(",", ":")).encode()
    )
    expected = (
        f"research-objects/{value['owner_id']}/by-kind/{KIND}/{value['sha256']}.json"
    )
    if original_key != expected:
        raise ValueError("Assembly journal address differs")
    return value
