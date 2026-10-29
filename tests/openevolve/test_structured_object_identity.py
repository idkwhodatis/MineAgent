"""Exact new typed object bytes/addresses and old-version closed dispatch."""

import hashlib
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import research_object_services as service
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes
from packages.openevolve_structured import object_identity as codec
from packages.openevolve_structured.provider_contracts import OEChatResponse
from packages.openevolve_structured.provider_response import build_request
from tests.openevolve.test_structured_provider_contracts import declared


def request():
    _, binding, domain, _, parent, rendered, context, _ = declared()
    return build_request(
        binding=binding,
        context=context,
        domain=domain,
        parent_candidate=parent,
        rendered=rendered,
    )


@pytest.mark.parametrize(
    "kind", ["oe_structured_provider_request", "oe_structured_provider_response"]
)
def test_actual_typed_provider_bytes_have_distinct_owner_addresses(kind):
    req = request()
    value = (
        req
        if kind.endswith("request")
        else OEChatResponse(
            request_sha256=req.sha256,
            content="original provider text",
            finish_reason="stop",
            usage_source="provider",
        )
    )
    raw, owner = canonical_bytes(value), uuid4()
    identity = codec.identity_for_content(owner, kind, raw)
    assert identity.sha256 == hashlib.sha256(raw).hexdigest()
    assert codec.parse_document(kind, raw) == value
    assert codec.parse_storage_key(identity.storage_key) == (
        "object",
        owner,
        kind,
        identity.sha256,
        False,
    )
    assert (
        codec.identity_for_content(uuid4(), kind, raw).storage_key
        != identity.storage_key
    )
    with pytest.raises(ValueError):
        codec.identity_for_content(owner, kind, b" " + raw)


@pytest.mark.parametrize(
    "revision", ["026_rd_seeded_submissions", "028_workflow", "unknown"]
)
def test_new_kinds_not_admitted_by_other_schema_versions(revision):
    session = NS(scalars=lambda q: NS(all=lambda: [revision]))
    with pytest.raises(DomainError):
        service._versioned_identity(
            session,
            uuid4(),
            "oe_structured_provider_request",
            canonical_bytes(request()),
        )


def test_exact027_dispatch_and_actual_file_inventory(tmp_path):
    owner, kind = uuid4(), "oe_structured_provider_request"
    raw = canonical_bytes(request())
    session = NS(scalars=lambda q: NS(all=lambda: ["028_oe_structured_search"]))
    identity = service._versioned_identity(session, owner, kind, raw)
    path = tmp_path / identity.storage_key
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    records = service.physical_inventory(tmp_path)
    assert len(records) == 1 and records[0].storage_key == identity.storage_key
    assert records[0].owner_id == owner and records[0].size_bytes == len(raw)


def test_new_journal_is_canonical_bounded_and_not_a_legacy_tag():
    owner, kind = uuid4(), "oe_structured_provider_request"
    raw = canonical_bytes(request())
    identity = codec.identity_for_content(owner, kind, raw)
    row = NS(
        id=uuid4(),
        owner_id=owner,
        run_id=uuid4(),
        attempt_id=uuid4(),
        budget_reservation_id=uuid4(),
        generation=1,
        object_kind=kind,
        sha256=identity.sha256,
        size_bytes=len(raw),
        storage_key=identity.storage_key,
    )
    journal = codec.publication_journal(row)
    value = codec.validate_publication_journal(journal)
    assert value["schema_version"] == codec.SCHEMA
    for field, bad in (
        ("size_bytes", True),
        ("size_bytes", 262145),
        ("storage_key", "wrong"),
    ):
        with pytest.raises(ValueError):
            codec.validate_publication_journal(canonical_bytes({**value, field: bad}))


def test_old_object_address_delegates_unchanged():
    from packages.rdagent_provider.object_identity import object_identity as previous

    owner = uuid4()
    assert codec.object_identity(owner, "model_response", "a" * 64) == previous(
        owner, "model_response", "a" * 64
    )


@pytest.mark.parametrize("wrong", [False, True])
def test_request_publication_checks_actual_run_model_and_execution_binding(
    monkeypatch, wrong
):
    from backend.app import oe_structured_object_scope as scope
    from packages.openevolve_structured.provider_contracts import OEChatBinding

    _, binding, domain, _, parent, rendered, context, _ = declared()
    req = build_request(
        binding=binding,
        context=context,
        domain=domain,
        parent_candidate=parent,
        rendered=rendered,
    )
    config = NS(
        model_binding=binding,
        search=NS(
            execution_binding_sha256="f" * 64 if wrong else req.execution_binding_sha256
        ),
    )
    session = NS(scalars=lambda q: NS(all=lambda: ["028_oe_structured_search"]))
    monkeypatch.setattr(
        scope, "require_neutral_transaction", lambda *a, **kw: (NS(), NS())
    )
    monkeypatch.setattr(scope, "decode_spec", lambda *a: (NS(), config))
    assert isinstance(binding, OEChatBinding)
    if wrong:
        with pytest.raises(DomainError):
            scope.require_object_scope(
                session, NS(), "oe_structured_provider_request", canonical_bytes(req)
            )
    else:
        assert (
            scope.require_object_scope(
                session, NS(), "oe_structured_provider_request", canonical_bytes(req)
            )
            == req
        )
