# ruff: noqa: F811
import json
from types import SimpleNamespace
from uuid import UUID

import pytest
from test_rdagent_source_read_witness import setup as retained_files  # noqa: F401

from packages.rdagent_adapter import object_identity as new
from packages.research_runtime import object_identity as old


def test_explicit_assembly_codec_does_not_change_old_kind_grammar(retained_files):
    raw = json.dumps(
        retained_files.document.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    identity = new.identity_for_content(UUID(int=1), new.KIND, raw)
    assert new.parse_storage_key(identity.storage_key)[2] == new.KIND
    with pytest.raises(ValueError):
        old.identity_for_content(UUID(int=1), new.KIND, raw)
    with pytest.raises(ValueError):
        old.parse_storage_key(identity.storage_key)
    publication = SimpleNamespace(
        id=UUID(int=8),
        owner_id=UUID(int=1),
        run_id=UUID(int=2),
        attempt_id=UUID(int=3),
        budget_reservation_id=UUID(int=4),
        generation=1,
        object_kind=new.KIND,
        sha256=identity.sha256,
        size_bytes=len(raw),
        storage_key=identity.storage_key,
    )
    journal = new.publication_journal(publication)
    assert new.validate_publication_journal(journal)["schema_version"] == new.SCHEMA
    with pytest.raises(ValueError):
        old.validate_publication_journal(journal)
    publication.object_kind = "model_response"
    publication.storage_key = old.object_identity(
        publication.owner_id, publication.object_kind, publication.sha256
    ).storage_key
    assert new.publication_journal(publication) == old.publication_journal(publication)


@pytest.mark.parametrize("raw", [b"{}", b'{"extra":1}', b'{"x":1,"x":2}', b"[]"])
def test_assembly_kind_is_not_arbitrary_json(raw):
    with pytest.raises(ValueError):
        new.identity_for_content(UUID(int=1), new.KIND, raw)
