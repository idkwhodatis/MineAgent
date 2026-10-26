from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.encoders import jsonable_encoder

import backend.app.rdagent_evaluation_services as service
from backend.app.factor_services import public
from backend.app.models import DatasetVersion, DataUsageExposure
from backend.app.services import DomainError
from packages.factors.contracts import digest


def fixture(monkeypatch):
    owner, dataset_id = UUID(int=1), UUID(int=2)
    rows = [
        DataUsageExposure(
            id=UUID(int=3),
            owner_id=owner,
            dataset_version_id=dataset_id,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 2, 1),
            purpose="factor_prefix_provided",
            source_key="fixture:prefix",
            identities=[],
            provenance={"provided": True, "computation_proven": False},
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
    ]
    dataset = SimpleNamespace(id=dataset_id, owner_id=owner, content_sha256="a" * 64)
    raw = []

    def envelope(row):
        evidence = jsonable_encoder(public(row))
        return {
            "source": "locally_recorded_claim",
            "evidence_sha256": digest(evidence),
            "dataset_content_sha256": dataset.content_sha256,
            "evidence": evidence,
        }

    raw.append(envelope(rows[0]))
    monkeypatch.setattr(service, "inherited_exposure_claims", lambda *args: raw)

    def get(model, identity):
        if model is DatasetVersion:
            return dataset if identity == dataset_id else None
        return next((row for row in rows if row.id == identity), None)

    return owner, rows, dataset, raw, envelope, SimpleNamespace(get=get)


def test_step2_keeps_prefix_and_computation_exposures_distinct(monkeypatch):
    owner, rows, _, raw, envelope, session = fixture(monkeypatch)
    first = service.local_exposure_history(session, owner, UUID(int=9))
    rows.append(
        DataUsageExposure(
            id=UUID(int=4),
            owner_id=owner,
            dataset_version_id=UUID(int=2),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 2, 1),
            purpose="factor_computation",
            source_key="fixture:compute",
            identities=[],
            provenance={"computed": True},
            created_at=datetime(2024, 1, 2, tzinfo=UTC),
        )
    )
    raw.append(envelope(rows[-1]))
    second = service.local_exposure_history(session, owner, UUID(int=10))
    assert first.claims[0] in second.claims
    assert [claim.purpose for claim in second.claims] == [
        "factor_prefix_provided",
        "factor_computation",
    ]
    assert [claim.evidence_id for claim in second.claims] == [
        str(row.id) for row in rows
    ]
    assert all(claim.identity_mapping == "unresolved" for claim in second.claims)
    assert second.completeness == "partial" and second.has_unknown_history


@pytest.mark.parametrize(
    "bad", ["owner", "hash", "window", "import", "dataset", "duplicate"]
)
def test_exposure_mapping_rejects_untrusted_or_changed_evidence(monkeypatch, bad):
    owner, rows, dataset, raw, _, session = fixture(monkeypatch)
    if bad == "owner":
        rows[0].owner_id = UUID(int=99)
    elif bad == "hash":
        raw[0]["evidence_sha256"] = "0" * 64
    elif bad == "window":
        raw[0]["evidence"]["end_date"] = "2025-01-01"
        raw[0]["evidence_sha256"] = digest(raw[0]["evidence"])
    elif bad == "import":
        raw[0]["source"] = "unverified_import"
    elif bad == "dataset":
        dataset.owner_id = UUID(int=99)
    else:
        raw.append(raw[0])
    with pytest.raises(DomainError):
        service.local_exposure_history(session, owner, UUID(int=9))


def test_absent_exposure_history_is_never_clean(monkeypatch):
    owner, _, _, raw, _, session = fixture(monkeypatch)
    raw.clear()
    history = service.local_exposure_history(session, owner, UUID(int=9))
    assert history.has_unknown_history and history.completeness == "unknown"
