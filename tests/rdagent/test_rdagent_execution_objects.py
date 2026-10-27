"""Typed021 object identities and immediate revision/scope guards, no PG claim."""

import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_rdagent_execution_outcomes import document

from packages.factors.contracts import canonical_bytes
from packages.rdagent_adapter import execution_object_identity as identity
from packages.rdagent_adapter.execution_outcomes import DOCUMENT_MODELS


@pytest.mark.parametrize("kind", sorted(identity.KINDS))
def test_typed_identity_journal_and_temporary(kind):
    doc = DOCUMENT_MODELS[kind].model_validate(document(DOCUMENT_MODELS[kind]))
    raw = canonical_bytes(doc)
    obj = identity.identity_for_content(doc.owner_id, kind, raw)
    assert obj.sha256 == hashlib.sha256(raw).hexdigest()
    assert identity.parse_storage_key(obj.storage_key) == (
        "object",
        doc.owner_id,
        kind,
        obj.sha256,
        False,
    )
    temp = (
        obj.storage_key.rsplit("/", 1)[0]
        + "/."
        + obj.storage_key.rsplit("/", 1)[1]
        + "."
        + "a" * 32
        + ".tmp"
    )
    assert identity.parse_storage_key(temp)[-1] is True
    pub = SimpleNamespace(
        id=uuid4(),
        owner_id=doc.owner_id,
        run_id=doc.run_id,
        attempt_id=doc.attempt_id,
        generation=doc.generation,
        budget_reservation_id=uuid4(),
        object_kind=kind,
        sha256=obj.sha256,
        size_bytes=len(raw),
        storage_key=obj.storage_key,
    )
    journal = identity.publication_journal(pub)
    assert identity.validate_publication_journal(journal)["object_kind"] == kind
    with pytest.raises(ValueError):
        identity.identity_for_content(uuid4(), kind, raw)
    with pytest.raises(ValueError):
        identity.identity_for_content(doc.owner_id, kind, raw + b" ")
    changed = json.loads(journal)
    changed["size_bytes"] = 65537
    with pytest.raises(ValueError):
        identity.validate_publication_journal(canonical_bytes(changed))


@pytest.mark.parametrize(
    "revision", ["020_rd_controller", "019_native_hdf_storage", None]
)
def test_new_objects_never_reserve_under_old_revision(monkeypatch, revision):
    from backend.app import research_object_services as services
    from backend.app.services import DomainError

    kind = "factor_generation_proposal"
    doc = DOCUMENT_MODELS[kind].model_validate(document(DOCUMENT_MODELS[kind]))
    fence = SimpleNamespace(
        **{
            k: getattr(doc, k)
            for k in ("owner_id", "run_id", "attempt_id", "generation")
        }
    )
    monkeypatch.setattr(services, "require_neutral_transaction", lambda *a, **k: None)
    session = SimpleNamespace(
        scalars=lambda _: SimpleNamespace(
            all=lambda: [] if revision is None else [revision]
        )
    )
    with pytest.raises(DomainError, match="Neutral object publication"):
        services.reserve_object(
            session, fence, kind, canonical_bytes(doc), uuid4(), None
        )


def test_new_object_scope_rejected_before_budget_or_filesystem(monkeypatch):
    from backend.app import rdagent_services
    from backend.app import research_object_services as services
    from backend.app.services import DomainError

    doc = DOCUMENT_MODELS["factor_generation_proposal"].model_validate(
        document(DOCUMENT_MODELS["factor_generation_proposal"])
    )
    fence = SimpleNamespace(
        owner_id=doc.owner_id, run_id=uuid4(), attempt_id=doc.attempt_id, generation=1
    )
    monkeypatch.setattr(services, "require_neutral_transaction", lambda *a, **k: None)
    monkeypatch.setattr(rdagent_services, "require_research_spec", lambda *a: None)
    session = SimpleNamespace(
        scalars=lambda _: SimpleNamespace(all=lambda: ["021_execution_snapshots"])
    )
    with pytest.raises(DomainError):
        services.reserve_object(
            session,
            fence,
            "factor_generation_proposal",
            canonical_bytes(doc),
            uuid4(),
            None,
        )
