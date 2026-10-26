# ruff: noqa: F811
"""Resolver storage reads are real; transaction/ledger authority doubles explicit."""

from uuid import UUID

import pytest
from test_rdagent_source_read_witness import setup as retained_files  # noqa: F401

from backend.app import rdagent_source_resolution as resolver
from backend.app.services import DomainError


def resolve(retained_files, monkeypatch, **changes):
    monkeypatch.setattr(resolver, "require_neutral_transaction", lambda *a, **k: None)
    values = {
        "source_id": retained_files.source.id,
        "expected_factor_version_id": retained_files.source.factor_version_id,
        "expected_code_object_id": retained_files.obj.id,
        "expected_sha256": retained_files.obj.sha256,
        "settings": retained_files.settings,
    }
    return resolver.require_source_code(
        retained_files.session, retained_files.fence, **(values | changes)
    )


def test_source_resolver_rereads_existing_witness_without_writing(
    retained_files, monkeypatch
):
    witness = retained_files.invoke()
    result = resolve(retained_files, monkeypatch)
    assert result.read_witness_id == witness.id
    assert result.code_object_id == retained_files.obj.id
    assert result.source_id == retained_files.source.id
    assert len(retained_files.reads) == 6 and retained_files.session.flushes == 2


@pytest.mark.parametrize(
    "field",
    ["expected_factor_version_id", "expected_code_object_id", "expected_sha256"],
)
def test_source_resolver_rejects_same_owner_reference_drift(
    retained_files, monkeypatch, field
):
    retained_files.invoke()
    with pytest.raises(DomainError):
        resolve(
            retained_files,
            monkeypatch,
            **{field: "0" * 64 if field.endswith("sha256") else UUID(int=999)},
        )
    assert retained_files.session.flushes == 2


def test_source_resolver_never_creates_missing_witness(retained_files, monkeypatch):
    with pytest.raises(DomainError):
        resolve(retained_files, monkeypatch)
    assert retained_files.session.flushes == 0
