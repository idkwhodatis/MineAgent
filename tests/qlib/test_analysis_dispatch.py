"""Exact installed-version dispatch doubles; no actual DB authority claim."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app import factor_analysis_dispatch as dispatch
from backend.app.services import DomainError


@pytest.mark.parametrize(
    "version",
    [
        "019_native_hdf_storage",
        "020_rd_controller",
        "021_execution_snapshots",
        "023_unregistered",
    ],
)
def test_other_versions_never_probe_plan_or_enable_successor(monkeypatch, version):
    scopes = []
    monkeypatch.setattr(
        dispatch, "require_neutral_transaction", lambda *a, **kw: scopes.append(kw)
    )
    session = SimpleNamespace(scalars=lambda *a: SimpleNamespace(all=lambda: [version]))
    assert dispatch.configured_analysis_run(session, object()) is False
    assert scopes == [{"resource_scope": "factor_objects"}]


@pytest.mark.parametrize("exists", [False, True])
def test_exact022_selects_only_persisted_owned_run_plan(monkeypatch, exists):
    monkeypatch.setattr(dispatch, "require_neutral_transaction", lambda *a, **kw: None)
    fence = SimpleNamespace(owner_id=uuid4(), run_id=uuid4())
    queries = []

    def scalar(query):
        queries.append(query)
        return uuid4() if exists else None

    session = SimpleNamespace(
        scalars=lambda *a: SimpleNamespace(all=lambda: ["022_factor_analysis"]),
        scalar=scalar,
    )
    assert dispatch.configured_analysis_run(session, fence) is exists
    params = queries[0].compile().params
    assert set(params.values()) == {fence.owner_id, fence.run_id}


def test_ambiguous_revision_rejected(monkeypatch):
    monkeypatch.setattr(dispatch, "require_neutral_transaction", lambda *a, **kw: None)
    session = SimpleNamespace(scalars=lambda *a: SimpleNamespace(all=list))
    with pytest.raises(DomainError):
        dispatch.configured_analysis_run(session, object())
