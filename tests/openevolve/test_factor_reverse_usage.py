"""Portable metadata-boundary checks; not a real PostgreSQL execution claim."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from backend.app import oe_factor_usage as usage
from backend.app.factor_models import Factor
from backend.app.services import DomainError


def fixture(monkeypatch, mismatch=None):
    owner, factor, version, run, execution, snapshot, program, candidate = (
        uuid4() for _ in range(8)
    )
    row = NS(
        id=execution,
        owner_id=owner,
        run_id=run,
        snapshot_id=snapshot,
        program_version_id=program,
        candidate_id=candidate,
    )
    monkeypatch.setattr(
        usage,
        "owned",
        lambda s, model, o, key: (
            NS(id=factor)
            if model is Factor
            else NS(
                id=version,
                factor_id=uuid4() if mismatch == "factor" else factor,
                ordinal=2,
                parent_version_id=None,
            )
        ),
    )
    monkeypatch.setattr(
        usage,
        "owned_execution",
        lambda *a: (
            None,
            row,
            NS(
                fixed_inputs=NS(
                    factor_version_id=uuid4() if mismatch == "version" else version,
                    snapshot_id=snapshot,
                )
            ),
        ),
    )
    monkeypatch.setattr(
        usage,
        "list_records",
        lambda *a: (
            NS(
                execution_id=execution,
                candidate_id=candidate,
                snapshot_id=uuid4() if mismatch == "snapshot" else snapshot,
                program_version_id=program,
            ),
        ),
    )
    values = iter((["028_oe_structured_search"], [row]))
    session = NS(scalars=lambda _: NS(all=lambda: next(values)))
    return session, owner, factor, version, row


def test_only_identity_metadata_no_disclosure(monkeypatch):
    session, owner, factor, version, row = fixture(monkeypatch)
    result = usage.factor_usage(session, owner, factor, version)
    record = result.data[0].model_dump()
    assert record["execution_id"] == row.id
    assert record["factor_version_id"] == version
    assert record["structured_strategy_version_id"] == row.program_version_id
    assert set(record) == {
        "execution_id",
        "run_id",
        "factor_id",
        "factor_version_id",
        "factor_version_ordinal",
        "parent_factor_version_id",
        "execution_snapshot_id",
        "structured_strategy_version_id",
        "qualification",
    }
    assert record["qualification"] == "identity_metadata_not_result_release"


@pytest.mark.parametrize("mismatch", ["factor", "version", "snapshot"])
def test_cross_binding_fails_closed(monkeypatch, mismatch):
    session, owner, factor, version, _ = fixture(monkeypatch, mismatch)
    with pytest.raises(DomainError):
        usage.factor_usage(session, owner, factor, version)


def test_missing_owner_fails_before_any_inventory(monkeypatch):
    def missing(*args):
        raise DomainError("not_found", "Not found", 404)

    monkeypatch.setattr(usage, "owned", missing)
    with pytest.raises(DomainError) as caught:
        usage.factor_usage(object(), uuid4(), uuid4(), uuid4())
    assert caught.value.status_code == 404


def test_query_joins_actual_owner_run_attempt_candidate_snapshot_version():
    owner, version = uuid4(), uuid4()
    query = usage.usage_query(owner, version, 50)
    sql = str(query.compile(dialect=postgresql.dialect()))
    for fragment in (
        "factor_execution_snapshot_publications.owner_id = oe_structured_executions.owner_id",
        "research_runs.owner_id = oe_structured_executions.owner_id",
        "oe_structured_engine_outcomes.owner_id = oe_structured_executions.owner_id",
        "oe_structured_engine_outcomes.run_id = oe_structured_executions.run_id",
        "oe_structured_engine_outcomes.attempt_id = oe_structured_executions.attempt_id",
        "oe_structured_engine_outcomes.generation = oe_structured_executions.generation",
        "oe_structured_engine_outcomes.candidate_id = oe_structured_executions.candidate_id",
        "factor_execution_snapshot_publications.factor_version_id =",
        "oe_structured_executions.owner_id =",
        "LIMIT",
        "OFFSET",
    ):
        assert fragment in sql
    assert query.compile().params["owner_id_1"] == owner
    assert query.compile().params["factor_version_id_1"] == version


def test_unsupported_schema_does_not_read_normal_tables(monkeypatch):
    session, owner, factor, version, _ = fixture(monkeypatch)
    calls = []
    session.scalars = lambda query: (
        calls.append(str(query)) or NS(all=lambda: ["025_structured_factor_strategy"])
    )
    with pytest.raises(DomainError) as caught:
        usage.factor_usage(session, owner, factor, version)
    assert caught.value.code == "oe_structured_history_schema_unavailable"
    assert len(calls) == 1


from tests.openevolve.test_structured_routes import api as api  # noqa: PLC0414


def test_http_requires_bearer_and_passes_only_owned_read_scope(api, monkeypatch):
    from backend.app.structured_result_routes import StructuredExecutionRecords, router

    client, app, user, headers, _ = api
    app.include_router(router, prefix="/api/v1")
    factor, version = uuid4(), uuid4()
    calls = []
    monkeypatch.setattr(
        usage,
        "factor_usage",
        lambda session, owner, f, v, offset: (
            calls.append((owner, f, v, offset)) or StructuredExecutionRecords(data=())
        ),
    )
    url = f"/api/v1/factors/{factor}/versions/{version}/normal-oe-executions?offset=50"
    assert client.get(url).status_code == 401
    assert not calls
    response = client.get(url, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"data": []}
    assert calls == [(user.id, factor, version, 50)]
    assert client.post(url, headers=headers).status_code == 405
