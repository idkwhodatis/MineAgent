"""DDL/service boundary contracts, not actual PostgreSQL activation acceptance."""

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import CreateTable

from backend.app import openevolve_models as models
from backend.app.openevolve_services import _current_search
from backend.app.services import DomainError


@pytest.mark.parametrize(
    "name",
    [
        "StrategySearchSpec",
        "StrategySearchEpoch",
        "SearchCandidate",
        "SearchIterationIntent",
        "SearchEvaluation",
        "SearchObjectiveReceipt",
        "SearchObjectiveEvaluation",
    ],
)
def test_draft_domain_tables_have_exact_scope_and_compile(name):
    table = getattr(models, name).__table__
    sql = str(CreateTable(table).compile(dialect=dialect()))
    assert "owner_id UUID NOT NULL" in sql and "run_id UUID" in sql
    assert "FOREIGN KEY" in sql
    if name != "StrategySearchSpec":
        assert "generation INTEGER NOT NULL" in sql
        assert "execution_attempts (run_id, generation, id)" in sql


def test_candidate_source_and_parent_bind_full_tuple():
    sql = str(CreateTable(models.SearchCandidate.__table__).compile(dialect=dialect()))
    assert "research_objects (owner_id, id, object_kind, sha256)" in sql
    assert (
        "search_candidates (owner_id, run_id, attempt_id, generation, epoch_id, id, source_sha256)"
        in sql
    )
    assert "parent_candidate_id <> id" in sql


def test_objective_window_association_cannot_substitute_other_candidate():
    sql = str(
        CreateTable(models.SearchObjectiveEvaluation.__table__).compile(
            dialect=dialect()
        )
    )
    assert (
        "search_evaluations (owner_id, run_id, attempt_id, generation, epoch_id, candidate_id, source_sha256, id, window_id, execution_sha256)"
        in sql
    )
    assert "UNIQUE (objective_receipt_id, window_id)" in sql


def test_preparation_sources_are_not_invented_candidates():
    sql = str(CreateTable(models.SearchEvaluation.__table__).compile(dialect=dialect()))
    assert "seed_original_parity" in sql and "seed_prepared_parity" in sql
    assert (
        "original_strategy_id IS NOT NULL AND prepared_strategy_id IS NULL AND epoch_id IS NULL AND candidate_id IS NULL"
        in sql
    )


def test_legacy_run_cannot_enter_search_spec_service_even_after_fence_check():
    fence = SimpleNamespace(owner_id=uuid4(), run_id=uuid4())
    run = SimpleNamespace(
        owner_id=fence.owner_id, purpose="legacy_strategy", engine="legacy"
    )
    session = SimpleNamespace(get=lambda *_: run)
    with (
        patch("backend.app.openevolve_services.require_neutral_transaction"),
        pytest.raises(DomainError, match="OpenEvolve"),
    ):
        _current_search(session, fence)


def test_session_without_authoritative_neutral_transaction_rejected_first():
    with pytest.raises(DomainError):
        _current_search(SimpleNamespace(info={}), SimpleNamespace())


@pytest.mark.parametrize("tamper", ["config", "manifest", "source_bytes", "dataset"])
def test_spec_binding_rejects_same_owner_content_substitution(tamper):
    import hashlib

    from backend.app.models import DatasetVersion, StrategyVersion
    from backend.app.openevolve_services import bind_search_spec
    from packages.openevolve_adapter.contracts import SearchSpec
    from packages.research_runtime.broker_contracts import digest
    from tests.openevolve.test_contracts import complete_spec

    data = complete_spec().model_dump()
    code = "x=1\n"
    for name in ("original", "prepared"):
        data["preparation"][name]["source_sha256"] = hashlib.sha256(
            code.encode()
        ).hexdigest()
        data["preparation"][name]["manifest_sha256"] = digest(None)
    spec = SearchSpec.model_validate(data)
    fence = SimpleNamespace(owner_id=spec.owner_id, run_id=spec.run_id)
    run = SimpleNamespace(
        config=spec.model_dump(mode="json"),
        strategy_version_id=spec.preparation.prepared.strategy_version_id,
        dataset_version_id=spec.execution.dataset_version_id,
    )
    source = SimpleNamespace(
        owner_id=spec.owner_id,
        content_sha256=spec.preparation.original.source_sha256,
        code=code,
        manifest=None,
    )
    dataset = SimpleNamespace(
        owner_id=spec.owner_id, content_sha256=spec.execution.dataset_sha256
    )
    if tamper == "config":
        run.config = {"schema_version": "mineagent.strategy-search.v1"}
    elif tamper == "manifest":
        source.manifest = {"changed": True}
    elif tamper == "source_bytes":
        source.code = "x=2\n"
    else:
        dataset.content_sha256 = "f" * 64

    def get(model, identifier):
        if model is StrategyVersion:
            return source
        if model is DatasetVersion:
            return dataset
        raise AssertionError("Unexpected model")

    session = SimpleNamespace(get=get)
    with (
        patch("backend.app.openevolve_services._current_search", return_value=run),
        pytest.raises(DomainError),
    ):
        bind_search_spec(session, fence, spec)
