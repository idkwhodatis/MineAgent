import hashlib
from types import SimpleNamespace
from uuid import uuid4

import pytest

from packages.research_runtime.object_identity import (
    MAX_OBJECT_BYTES,
    canonical_object,
    identity_for_content,
    object_identity,
    parse_storage_key,
    publication_journal,
    validate_publication_journal,
)


def test_equal_bytes_are_kind_qualified_and_owner_qualified():
    owner = uuid4()
    content = b'{"value":1}'
    request = identity_for_content(owner, "model_request", content)
    response = identity_for_content(owner, "model_response", content)
    assert request.sha256 == response.sha256
    assert request.storage_key != response.storage_key
    assert (
        identity_for_content(uuid4(), "model_request", content).storage_key
        != request.storage_key
    )
    assert parse_storage_key(request.storage_key) == (
        "object",
        owner,
        "model_request",
        request.sha256,
        False,
    )


@pytest.mark.parametrize(
    "content",
    [
        b"{}\n",
        b'{"b":1,"a":2}',
        b'{"a":1,"a":1}',
        b"[]",
        b"null",
        b'{"x":NaN}',
        b'{"x":1e400}',
        b"\xff",
        b"{}" + b" " * MAX_OBJECT_BYTES,
    ],
    ids=[
        "trailing-newline",
        "unordered-keys",
        "duplicate-key",
        "array",
        "null",
        "nan",
        "overflow-number",
        "invalid-utf8",
        "oversized",
    ],
)
def test_noncanonical_or_unsafe_bytes_rejected(content):
    with pytest.raises(ValueError):
        canonical_object(content)


@pytest.mark.parametrize(
    "kind", ["code", "strategy_source", "../model_request", "factor_hdf", None]
)
def test_unactivated_media_rejected(kind):
    with pytest.raises(ValueError):
        object_identity(uuid4(), kind, "a" * 64)


@pytest.mark.parametrize("digest", ["A" * 64, "a" * 63, "../x", None])
def test_invalid_digest_rejected(digest):
    with pytest.raises(ValueError):
        object_identity(uuid4(), "model_request", digest)


def test_journal_is_exact_and_never_exports_execution_credential():
    owner = uuid4()
    identity = identity_for_content(owner, "model_request", b"{}")
    publication = SimpleNamespace(
        id=uuid4(),
        owner_id=owner,
        run_id=uuid4(),
        attempt_id=uuid4(),
        generation=1,
        budget_reservation_id=uuid4(),
        object_kind=identity.object_kind,
        sha256=identity.sha256,
        size_bytes=2,
        storage_key=identity.storage_key,
        lease_token=uuid4(),
    )
    data = publication_journal(publication)
    assert str(publication.lease_token).encode() not in data
    parsed = validate_publication_journal(data)
    assert parsed["id"] == str(publication.id)
    assert parsed["sha256"] == hashlib.sha256(b"{}").hexdigest()
    assert parse_storage_key(
        f"research-objects/{owner}/journals/{publication.id}.json"
    ) == ("journal", owner, None, str(publication.id), False)
    parsed["lease_token"] = str(publication.lease_token)
    import json

    with pytest.raises(ValueError):
        validate_publication_journal(
            json.dumps(parsed, sort_keys=True, separators=(",", ":")).encode()
        )


def test_only_exact_atomic_temporary_grammar_is_allowed():
    owner = uuid4()
    identity = identity_for_content(owner, "model_request", b"{}")
    prefix, name = identity.storage_key.rsplit("/", 1)
    assert parse_storage_key(f"{prefix}/.{name}.{'a' * 32}.tmp")[-1]
    for key in (
        f"{prefix}/{name}.{'a' * 32}.tmp",
        f"{prefix}/.{name}",
        f"{prefix}/../{name}",
        f"research-objects/{owner}/arbitrary/x",
        f"{prefix}/{name}/x",
    ):
        with pytest.raises(ValueError):
            parse_storage_key(key)
