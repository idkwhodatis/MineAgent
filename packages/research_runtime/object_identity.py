"""Closed, kind-qualified identities for bounded neutral evidence only."""

import hashlib
import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from uuid import UUID

OBJECT_KINDS = MappingProxyType(
    {
        "model_request": ("json", "application/json"),
        "model_response": ("json", "application/json"),
        "process_receipt": ("json", "application/json"),
    }
)
MAX_OBJECT_BYTES = 1024 * 1024
MAX_JOURNAL_BYTES = 4096
PUBLICATION_SCHEMA = "mineagent.research-object-publication.v1"
SHA = re.compile(r"[0-9a-f]{64}\Z")
UUID_TEXT = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


@dataclass(frozen=True)
class ObjectIdentity:
    owner_id: UUID
    object_kind: str
    sha256: str
    storage_key: str
    media_type: str = "application/json"


def object_identity(owner: UUID, kind: str, sha256: str) -> ObjectIdentity:
    if type(owner) is not UUID or type(kind) is not str or kind not in OBJECT_KINDS:
        raise ValueError("Unsupported neutral object identity")
    if type(sha256) is not str or SHA.fullmatch(sha256) is None:
        raise ValueError("Invalid neutral object digest")
    return ObjectIdentity(
        owner, kind, sha256, f"research-objects/{owner}/by-kind/{kind}/{sha256}.json"
    )


def canonical_object(content: bytes) -> bytes:
    """Require exact bounded canonical JSON object; never parse executable media."""
    if type(content) is not bytes or not 1 <= len(content) <= MAX_OBJECT_BYTES:
        raise ValueError("Bounded neutral JSON object required")

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate object member")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("Non-finite object number")

    try:
        value = json.loads(
            content, object_pairs_hook=unique_pairs, parse_constant=invalid_constant
        )
        if type(value) is not dict:
            raise ValueError("Neutral evidence must be a JSON object")
        canonical = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (UnicodeError, RecursionError, OverflowError) as exc:
        raise ValueError("Invalid neutral JSON object") from exc
    if canonical != content:
        raise ValueError("Neutral evidence must use canonical JSON bytes")
    return content


def identity_for_content(owner: UUID, kind: str, content: bytes) -> ObjectIdentity:
    return object_identity(
        owner, kind, hashlib.sha256(canonical_object(content)).hexdigest()
    )


def parse_storage_key(key: str) -> tuple[str, UUID, str | None, str, bool]:
    """Return domain, owner, kind, digest/publication ID, temporary flag."""
    if type(key) is not str:
        raise ValueError("Invalid neutral storage key")
    match = re.fullmatch(
        rf"research-objects/({UUID_TEXT})/by-kind/(model_request|model_response|process_receipt)/(\.?)"
        r"([0-9a-f]{64})\.json(?:\.([0-9a-f]{32})\.tmp)?",
        key,
    )
    if match:
        owner, kind, dot, digest, temporary = match.groups()
        if bool(dot) != bool(temporary):
            raise ValueError("Invalid neutral atomic temporary")
        return "object", UUID(owner), kind, digest, bool(temporary)
    match = re.fullmatch(
        rf"research-objects/({UUID_TEXT})/journals/(\.?)({UUID_TEXT})\.json"
        r"(?:\.([0-9a-f]{32})\.tmp)?",
        key,
    )
    if match:
        owner, dot, publication, temporary = match.groups()
        if bool(dot) != bool(temporary):
            raise ValueError("Invalid neutral journal temporary")
        return "journal", UUID(owner), None, publication, bool(temporary)
    raise ValueError("Unknown neutral storage grammar")


def publication_journal(publication) -> bytes:
    value = {
        "schema_version": PUBLICATION_SCHEMA,
        **{
            field: str(getattr(publication, field))
            for field in (
                "id",
                "owner_id",
                "run_id",
                "attempt_id",
                "budget_reservation_id",
            )
        },
        "generation": publication.generation,
        "object_kind": publication.object_kind,
        "sha256": publication.sha256,
        "size_bytes": publication.size_bytes,
        "storage_key": publication.storage_key,
    }
    content = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    validate_publication_journal(content)
    return content


def validate_publication_journal(content: bytes) -> dict:
    if len(content) > MAX_JOURNAL_BYTES:
        raise ValueError("Oversized neutral publication journal")
    value = json.loads(canonical_object(content))
    expected = {
        "schema_version",
        "id",
        "owner_id",
        "run_id",
        "attempt_id",
        "budget_reservation_id",
        "generation",
        "object_kind",
        "sha256",
        "size_bytes",
        "storage_key",
    }
    if set(value) != expected or value["schema_version"] != PUBLICATION_SCHEMA:
        raise ValueError("Unsupported neutral publication journal")
    for field in ("id", "owner_id", "run_id", "attempt_id", "budget_reservation_id"):
        if type(value[field]) is not str or str(UUID(value[field])) != value[field]:
            raise ValueError("Invalid journal identity")
    if type(value["generation"]) is not int or value["generation"] < 1:
        raise ValueError("Invalid journal generation")
    if (
        type(value["size_bytes"]) is not int
        or not 1 <= value["size_bytes"] <= MAX_OBJECT_BYTES
    ):
        raise ValueError("Invalid journal byte count")
    identity = object_identity(
        UUID(value["owner_id"]), value["object_kind"], value["sha256"]
    )
    if identity.storage_key != value["storage_key"]:
        raise ValueError("Journal object address mismatch")
    return value
