"""Exposure admission/history with actual ORM evidence, explicit DB/fence doubles."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_rdagent_configured_source import retained  # noqa:F401
from test_rdagent_configured_source_service import setup_source  # noqa:F401

from backend.app import rdagent_configured_exposure as service
from backend.app.experiment_services import dataset_identities
from backend.app.models import DatasetVersion
from backend.app.services import DomainError
from packages.factors.contracts import ExposureHistory


@pytest.fixture
def case(setup_source, monkeypatch):  # noqa:F811
    s = setup_source
    s.exposures = []
    s.flushes = []
    s.scope_calls = []
    run = SimpleNamespace(
        id=s.fence.run_id,
        owner_id=s.fence.owner_id,
        experiment_id=None,
        split_version_id=None,
    )
    s.session.get = lambda cls, key: s.dataset if cls is DatasetVersion else run
    s.session.scalar = lambda query: s.exposures[0] if s.exposures else None
    s.session.scalars = lambda query: iter(s.exposures)
    s.session.add = s.exposures.append

    def flush():
        s.flushes.append(True)
        for row in s.exposures:
            row.created_at = datetime(2024, 3, 20, tzinfo=UTC)

    s.session.flush = flush
    monkeypatch.setattr(service, "_plan", lambda *args: (s.plan, s.args["policy"]))
    monkeypatch.setattr(
        service,
        "require_research_spec",
        lambda *args: (SimpleNamespace(id=s.plan.spec_id), s.config),
    )
    monkeypatch.setattr(
        service,
        "require_neutral_transaction",
        lambda *args, **kw: s.scope_calls.append(kw),
    )
    monkeypatch.setattr(
        service,
        "_identities",
        lambda session, dataset: dataset_identities(dataset),
    )
    monkeypatch.setattr(
        service, "local_exposure_history", lambda *args: ExposureHistory()
    )
    return s


def record(s):
    return service.record_configured_source_exposure(s.session, s.fence, s.settings)


def test_fenced_pre_generation_extent_includes_history_and_forward_label(case):
    row = record(case)
    assert row.start_date == case.dataset.start_date
    assert row.end_date == case.dataset.end_date
    assert row.start_date < case.config.input.window.start
    assert row.end_date > case.config.input.window.end
    assert row.run_id == case.fence.run_id and row.purpose == service.PURPOSE
    assert row.provenance["objective_sha256"] == case.plan.objective_sha256
    assert row.provenance["source_policy_sha256"] == case.plan.source_policy_sha256
    assert case.scope_calls == [{"resource_scope": "factor_objects"}]
    assert record(case) is row and len(case.flushes) == 1


@pytest.mark.parametrize(
    "field", ["purpose", "source_key", "provenance", "identities", "run_id"]
)
def test_exact_replay_drift(case, field):
    row = record(case)
    setattr(
        row,
        field,
        uuid4()
        if field == "run_id"
        else {}
        if field == "provenance"
        else []
        if field == "identities"
        else "changed",
    )
    with pytest.raises(DomainError, match="Exposure admission identity changed"):
        record(case)


def test_known_claims_retained_without_external_cleanliness_promotion(case):
    row = record(case)
    history = service.configured_exposure_history(case.session, case.fence, uuid4())
    assert len(history.claims) == 1 and history.claims[0].evidence_id == str(row.id)
    assert history.claims[0].window.end == case.dataset.end_date
    assert history.completeness == "partial"
    assert "source_history_incomplete" in history.unresolved_reasons
    assert history.claims[0].identity_mapping == "unresolved"


def test_dataset_owner_drift_rejected(case):
    case.dataset.owner_id = uuid4()
    with pytest.raises(DomainError, match="Exact configured source"):
        record(case)
    assert case.exposures == []


def test_cold_admission_and_frozen_claim_hash(case):
    from fastapi.encoders import jsonable_encoder

    from backend.app.factor_services import public
    from backend.app.rdagent_configured_exposure_backup import (
        validate_configured_exposure_links,
    )
    from packages.rdagent_adapter.host_evidence import research_snapshot_input

    row = record(case)
    history = service.configured_exposure_history(case.session, case.fence, uuid4())
    snapshot = research_snapshot_input(
        case.config,
        factor_version_id=uuid4(),
        code_sha256="a" * 64,
        exposure_history=history,
    )
    plans = [jsonable_encoder(public(case.plan))]
    datasets = [jsonable_encoder(public(case.dataset))]
    exposures = [jsonable_encoder(public(row))]
    evaluations = [
        {
            "owner_id": str(case.fence.owner_id),
            "run_id": str(case.fence.run_id),
            "snapshot_input": snapshot.model_dump(mode="json"),
        }
    ]
    validate_configured_exposure_links(
        plans, datasets, exposures, evaluations, maximum_rows=100
    )
    exposures[0]["provenance"]["objective_sha256"] = "e" * 64
    with pytest.raises(ValueError):
        validate_configured_exposure_links(
            plans, datasets, exposures, evaluations, maximum_rows=100
        )


def test_sql_rejects_orphan_exposure_in_both_directions():
    from backend.app.rdagent_configured_exposure_sql import render_upgrade_sql

    sql = render_upgrade_sql()
    assert "NEW.source_key='rd-configured-source:'||p.id::text" in sql
    assert "Configured exposure cannot outlive or invent its plan" in sql
    assert "FOR UPDATE NOWAIT" in sql
    assert "factor_generation_starts WHERE run_id=NEW.run_id" in sql
