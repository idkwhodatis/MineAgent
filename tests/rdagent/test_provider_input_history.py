"""Actual immutable provider receipt decoding, transaction doubles, not PG."""

# ruff: noqa: F811
from datetime import UTC, datetime
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from test_engine_run_preflight import request_body  # noqa:F401
from test_rdagent_configured_source import retained  # noqa:F401
from test_rdagent_configured_source_service import setup_source  # noqa:F401
from test_rdagent_provider_saved_binding import provider_saved  # noqa:F401
from test_rdagent_submission_receipts import replay  # noqa:F401

from backend.app import engine_input_routes as routes
from backend.app.factor_analysis_models import FactorAnalysisPlan
from backend.app.rdagent_steer_models import RDSteerVersion
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes


@pytest.fixture
def accepted_input(provider_saved):
    p = provider_saved
    p.saved.row.id = uuid4()
    p.saved.row.idempotency_key = p.saved.run.idempotency_key
    p.saved.row.created_at = datetime.now(UTC)
    v = p.snapshot.versions[0]
    version = NS(
        id=v.version_id,
        owner_id=p.saved.owner,
        steer_id=v.steer_id,
        ordinal=v.ordinal,
        kind=v.kind,
        text=v.text,
        text_sha256=v.text_sha256,
        content_canonical=canonical_bytes(v).decode(),
        content_sha256=v.sha256,
    )
    reads = []

    def get(model, identity):
        reads.append(model)
        if model is FactorAnalysisPlan:
            assert identity == p.saved.plan.id
            return p.saved.plan
        assert model is RDSteerVersion, (
            "Current active/head/profile must not gate accepted history"
        )
        assert identity == version.id
        return version

    return NS(p=p, version=version, session=NS(get=get), reads=reads)


def test_real_provider_receipt_projects_original_text_after_future_head_changes(
    accepted_input,
):
    a = accepted_input
    result = routes.submission_item(a.session, a.p.saved.run, a.p.saved.row)
    assert result.origin == "user_provided" and result.default_pinned
    assert (
        result.submission.schema_version == "mineagent.rdagent-provider-submission.v1"
    )
    assert result.steer_snapshot == a.p.snapshot
    assert result.steer_snapshot_sha256 == a.p.snapshot.sha256
    assert result.model_binding_sha256 == a.p.config.model_binding.sha256
    assert (
        result.steer_evidence == "accepted_input_snapshot_not_execution_or_compliance"
    )
    assert a.reads == [FactorAnalysisPlan, RDSteerVersion]
    assert "idempotency_key" not in result.model_dump_json()


@pytest.mark.parametrize("field", ["text", "owner_id", "content_sha256"])
def test_frozen_snapshot_row_drift_withholds_user_input(accepted_input, field):
    a = accepted_input
    setattr(a.version, field, uuid4() if field == "owner_id" else "changed")
    with pytest.raises(DomainError):
        routes.submission_item(a.session, a.p.saved.run, a.p.saved.row)
