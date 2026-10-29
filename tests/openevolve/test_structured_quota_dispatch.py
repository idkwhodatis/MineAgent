"""Global writer dispatch includes both SDK families only at explicit027."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import (
    oe_structured_quota,
    openevolve_process_quota,
    openevolve_upload_quota,
    rdagent_provider_quota,
)


def session(revision):
    return NS(
        scalars=lambda q: NS(all=lambda: [revision]), execute=lambda q: NS(all=list)
    )


@pytest.mark.parametrize(
    "revision", ["028_oe_structured_search", "029_research_workflow"]
)
def test_rd_shared_entry_uses_one_unified_sdk_inventory(monkeypatch, revision):
    calls = []
    holds = ((uuid4(), 123, 8),)
    monkeypatch.setattr(
        oe_structured_quota, "sdk_process_holds", lambda s: calls.append(s) or holds
    )
    value = session(revision)
    assert rdagent_provider_quota.provider_process_holds(value) == holds
    assert calls == [value]
    assert (
        rdagent_provider_quota.provider_process_holds(
            session("026_rd_seeded_submissions")
        )
        == ()
    )
    assert calls == [value]


@pytest.mark.parametrize(
    "revision", ["028_oe_structured_search", "029_research_workflow"]
)
def test_every_object_writer_combines_structured_transfer_and_sdk_peaks(
    monkeypatch, revision
):
    owner = uuid4()
    monkeypatch.setattr(
        oe_structured_quota, "sdk_process_holds", lambda s: ((owner, 123, 8),)
    )
    monkeypatch.setattr(
        oe_structured_quota, "checkpoint_upload_holds", lambda s: ((owner, 456, 400),)
    )
    monkeypatch.setattr(openevolve_process_quota, "process_holds", lambda s: ())
    monkeypatch.setattr(openevolve_process_quota, "process_bytes", lambda *a: ())
    result = openevolve_process_quota.combined_holds(
        session(revision),
        NS(storage_root="/unused", neutral_object_max_files=1000),
    )
    assert result == ((owner, 456, 400), (owner, 123, 8))


@pytest.mark.parametrize(
    "revision", ["026_rd_seeded_submissions", "028_unknown", "future"]
)
def test_new_upload_table_never_read_at_other_revisions(monkeypatch, revision):
    def forbidden(s):
        raise AssertionError("new table queried")

    monkeypatch.setattr(oe_structured_quota, "checkpoint_upload_holds", forbidden)
    assert openevolve_upload_quota.upload_holds(session(revision)) == ()


def test_normal_and_rd_sdk_share_exact_concurrency_ceiling(monkeypatch):
    from backend.app import research_lifecycle

    monkeypatch.setattr(
        research_lifecycle, "require_neutral_transaction", lambda *a, **kw: None
    )
    monkeypatch.setattr(
        rdagent_provider_quota,
        "provider_process_holds",
        lambda s: ((uuid4(), 1, 8),) * 4,
    )

    def forbidden(*a, **kw):
        raise AssertionError("capacity granted after SDK limit")

    monkeypatch.setattr(openevolve_process_quota, "require_process_capacity", forbidden)
    with pytest.raises(ValueError, match="concurrency"):
        rdagent_provider_quota.admit_provider_capacity(None, None, None, None)
