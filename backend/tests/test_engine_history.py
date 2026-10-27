"""Real projection/routes with inert persistence, not PostgreSQL acceptance."""

from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from backend.app import engine_run_routes as routes
from backend.app import engine_run_views as views
from backend.app.services import DomainError
from packages.rdagent_adapter.contracts import FactorResearchConfig
from packages.rdagent_adapter.persisted_identity import LEGACY_ONLY_FIELDS
from runtimes.rdagent.probe_fixture import research_dict


def run_row():
    config = FactorResearchConfig.model_validate(research_dict())
    return NS(
        **dict.fromkeys(LEGACY_ONLY_FIELDS),
        id=uuid4(),
        owner_id=uuid4(),
        purpose="factor_research",
        engine="rdagent",
        config=config.model_dump(mode="json"),
        request_sha256=config.sha256,
        research_image_digest=None,
        dataset_version_id=config.input.dataset.dataset_version_id,
        status="running",
        generation=1,
        error=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def test_metadata_projection_does_not_mutate_or_infer_seed_success():
    run = run_row()
    before = deepcopy(vars(run))
    result = views.detail(run)
    assert vars(run) == before
    assert result.strategy_version_id is None
    assert result.engine == "rdagent"
    assert "execution_mode" not in result.model_dump()
    assert "result" not in result.model_dump()
    assert not result.actions.resume
    assert result.evidence_scope == "persisted_metadata_not_execution_acceptance"


@pytest.mark.parametrize(
    "changes",
    [
        {"purpose": "strategy_search", "engine": "openevolve"},
        {"purpose": "unknown", "engine": "unknown"},
        {"purpose": "legacy_strategy", "engine": "legacy"},
        {"strategy_version_id": uuid4()},
        {"request_sha256": "0" * 64},
    ],
)
def test_wrong_family_or_drift_cannot_coerce_to_rd(changes):
    run = run_row()
    vars(run).update(changes)
    with pytest.raises(DomainError) as error:
        views.summary(run)
    assert error.value.code == "not_found"


def test_owner_check_precedes_identity_projection(monkeypatch):
    calls = []

    def denied(session, run_id, owner_id):
        calls.append((run_id, owner_id))
        raise DomainError("not_found", "Run not found", 404)

    monkeypatch.setattr(views, "owned_run", denied)
    monkeypatch.setattr(
        views, "rd_config", lambda run: pytest.fail("must not project foreign run")
    )
    run_id, owner_id = uuid4(), uuid4()
    with pytest.raises(DomainError):
        views.owned_engine_run(object(), run_id, owner_id)
    assert calls == [(run_id, owner_id)]


def test_default_capabilities_and_submission_remain_closed():
    from backend.app.rdagent_submission_services import resolve_submission_runtime

    settings = NS(rdagent_research_enabled=False)
    session = NS(scalars=lambda query: NS(all=lambda: ["023_rd_submissions"]))
    request = NS(app=NS(state=NS(settings=settings)))
    caps = routes.capabilities(NS(id=uuid4()), session, request)
    assert [(x.engine, x.can_read_history, x.can_create) for x in caps.engines] == [
        ("rdagent", True, False),
        ("openevolve", False, False),
    ]
    with pytest.raises(DomainError) as error:
        resolve_submission_runtime(settings)
    assert error.value.code == "engine_submission_not_enabled"


def test_history_preserves_missing_outcomes_and_snapshot_identity():
    start = NS(
        id=uuid4(),
        attempt_id=uuid4(),
        generation=1,
        logical_step=0,
        created_at=datetime.now(UTC),
    )
    source = NS(id=uuid4(), source_sha256="a" * 64, factor_version_id=uuid4())
    evaluation = NS(id=uuid4())
    item = views.project_history(
        (start, source, None, evaluation, None, uuid4(), None, None, None, None)
    )
    assert item.source_id == source.id
    assert item.evaluation_id == evaluation.id
    assert item.generation_outcome is None
    assert item.evaluation_status is None
    assert item.snapshot_id is None
    result = NS(
        id=uuid4(),
        status="valid",
        snapshot_id=uuid4(),
        snapshot_input_sha256="b" * 64,
        snapshot_output_sha256="c" * 64,
    )
    completed = views.project_history(
        (
            start,
            source,
            None,
            evaluation,
            result,
            uuid4(),
            NS(consumer_kind="legacy_result", consumer_id=result.id),
            None,
            None,
            None,
        )
    )
    assert completed.snapshot_id == result.snapshot_id
    assert completed.snapshot_output_sha256 == "c" * 64


def test_history_joins_are_owner_run_attempt_scoped():
    sql = str(views.history_query(run_row()).compile(dialect=postgresql.dialect()))
    for table in [
        "factor_generation_sources",
        "factor_generation_outcomes",
        "factor_evaluation_starts",
        "factor_evaluation_outcomes",
        "factor_evaluation_result_consumers",
        "factor_execution_snapshot_publications",
        "factor_execution_snapshot_outcomes",
        "factor_execution_evaluation_outcomes",
    ]:
        for column in ["owner_id", "run_id", "attempt_id", "generation"]:
            assert f"{table}.{column} = factor_generation_starts.{column}" in sql
    assert "factor_versions.owner_id = factor_generation_starts.owner_id" in sql
    assert "factor_generation_starts.owner_id =" in sql.split("WHERE")[1]


def execution_metadata():
    identity = uuid4()
    publication = NS(
        id=identity,
        manifest={"schema_version": "mineagent.factor-execution-snapshot.v1"},
        manifest_sha256="a" * 64,
    )
    completed = NS(
        publication_id=identity,
        manifest_sha256="a" * 64,
        input_sha256="b" * 64,
        output_sha256="c" * 64,
    )
    execution = NS(
        id=uuid4(),
        snapshot_id=identity,
        status="valid",
        snapshot_input_sha256="b" * 64,
        snapshot_output_sha256="c" * 64,
        snapshot_manifest_sha256="a" * 64,
    )
    claim = NS(consumer_kind="execution_result", consumer_id=execution.id)
    return claim, publication, completed, execution


def test_execution_snapshot_is_never_aliased_to_legacy_feature_snapshot():
    claim, publication, completed, execution = execution_metadata()
    start = NS(
        id=uuid4(),
        attempt_id=uuid4(),
        generation=1,
        logical_step=0,
        created_at=datetime.now(UTC),
    )
    row = views.project_history(
        (
            start,
            None,
            None,
            NS(id=uuid4()),
            None,
            None,
            claim,
            publication,
            completed,
            execution,
        )
    )
    assert row.snapshot_id is None
    assert row.snapshot_input_sha256 is None
    assert row.evaluation_status == "valid"
    assert row.evaluation_protocol == "execution_result"
    assert row.execution_snapshot_id == completed.publication_id
    assert row.execution_snapshot_state == "completed"
    assert row.execution_snapshot_output_sha256 == completed.output_sha256


def test_pending_publication_and_completed_snapshot_do_not_invent_evaluation():
    _, publication, completed, _ = execution_metadata()
    pending = views.snapshot_projection(None, None, publication, None, None)
    assert pending["execution_snapshot_id"] is None
    assert pending["execution_snapshot_state"] == "publication_pending"
    assert pending["evaluation_protocol"] is None
    published = views.snapshot_projection(None, None, publication, completed, None)
    assert published["execution_snapshot_state"] == "completed"
    assert published["evaluation_protocol"] is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("snapshot_id", uuid4()),
        ("snapshot_output_sha256", "f" * 64),
        ("snapshot_input_sha256", "f" * 64),
        ("snapshot_manifest_sha256", "f" * 64),
    ],
)
def test_execution_projection_rejects_cross_snapshot_hash_drift(field, value):
    claim, publication, completed, execution = execution_metadata()
    setattr(execution, field, value)
    with pytest.raises(DomainError):
        views.snapshot_projection(None, claim, publication, completed, execution)


def test_execution_projection_rejects_missing_or_wrong_result_consumer():
    claim, publication, completed, execution = execution_metadata()
    for bad_claim in [
        None,
        NS(consumer_kind="legacy_result", consumer_id=execution.id),
        NS(consumer_kind="execution_result", consumer_id=uuid4()),
    ]:
        with pytest.raises(DomainError):
            views.snapshot_projection(
                None, bad_claim, publication, completed, execution
            )
    with pytest.raises(DomainError):
        views.snapshot_projection(
            NS(id=uuid4()), claim, publication, completed, execution
        )
