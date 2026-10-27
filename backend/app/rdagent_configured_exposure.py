"""Flush-only configured source admission and honest local dataset exposure map.

Whole retained dataset extent conservatively includes lookback and future labels;
this is host research admission evidence, not proof candidate code read each row.
It never turns unverified external/source history into clean or out-of-sample data.
"""

from uuid import uuid4

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from backend.app.experiment_services import _identities
from backend.app.factor_services import public
from backend.app.models import DatasetVersion, DataUsageExposure, ResearchRun
from backend.app.rdagent_configured_source import _plan
from backend.app.rdagent_evaluation_services import local_exposure_history
from backend.app.rdagent_services import require_research_spec
from backend.app.research_lifecycle import require_neutral_transaction
from backend.app.services import DomainError
from packages.factors.contracts import (
    DateWindow,
    ExposureClaim,
    ExposureHistory,
    canonical_bytes,
    digest,
)

PURPOSE = "factor_research_configured_source"


def exposure_provenance(plan):
    return {
        "schema_version": "mineagent.configured-source-exposure.v1",
        "plan_id": str(plan.id),
        "config_sha256": plan.config_sha256,
        "input_sha256": plan.input_sha256,
        "objective_sha256": plan.objective_sha256,
        "source_policy_sha256": plan.source_policy_sha256,
        "dataset_manifest_sha256": plan.label_source_manifest_sha256,
        "objective_window": plan.objective["window"],
        "label_horizon_sessions": plan.objective["label_horizon_sessions"],
        "extent": "entire_retained_dataset_including_history_and_labels",
        "availability": "declared_not_independently_verified_pit",
        "interpretation": "host_admission_not_proven_candidate_computation",
    }


def record_configured_source_exposure(session, fence, settings):
    require_neutral_transaction(session, fence, resource_scope="factor_objects")
    plan, _ = _plan(session, fence)
    spec, config = require_research_spec(session, fence)
    dataset = session.get(DatasetVersion, plan.label_source_dataset_id)
    run = session.get(ResearchRun, fence.run_id)
    if (
        dataset is None
        or run is None
        or (
            dataset.owner_id,
            run.owner_id,
            plan.spec_id,
            plan.config_sha256,
            plan.input_sha256,
            plan.label_source_manifest_sha256,
        )
        != (
            fence.owner_id,
            fence.owner_id,
            spec.id,
            config.sha256,
            config.input.sha256,
            dataset.content_sha256,
        )
    ):
        raise DomainError(
            "configured_exposure_scope",
            "Exact configured source admission required",
            409,
        )
    values = {
        "owner_id": fence.owner_id,
        "dataset_version_id": dataset.id,
        "experiment_id": run.experiment_id,
        "split_version_id": run.split_version_id,
        "run_id": run.id,
        "source_key": "rd-configured-source:" + str(plan.id),
        "identities": _identities(session, dataset),
        "start_date": dataset.start_date,
        "end_date": dataset.end_date,
        "purpose": PURPOSE,
        "provenance": exposure_provenance(plan),
    }
    previous = session.scalar(
        select(DataUsageExposure).filter_by(
            owner_id=fence.owner_id, source_key=values["source_key"]
        )
    )
    if previous is not None:
        if any(getattr(previous, key) != value for key, value in values.items()):
            raise DomainError(
                "configured_exposure_drift", "Exposure admission identity changed", 409
            )
        return previous
    row = DataUsageExposure(id=uuid4(), **values)
    session.add(row)
    session.flush()
    return row


def configured_exposure_history(session, fence, version_id):
    require_neutral_transaction(session, fence, resource_scope="factor_objects")
    _, config = require_research_spec(session, fence)
    inherited = local_exposure_history(session, fence.owner_id, version_id)
    claims = {item.evidence_id: item for item in inherited.claims}
    rows = list(
        session.scalars(
            select(DataUsageExposure)
            .filter_by(
                owner_id=fence.owner_id,
                dataset_version_id=config.input.dataset.dataset_version_id,
            )
            .order_by(DataUsageExposure.created_at, DataUsageExposure.id)
            .limit(257)
        )
    )
    if len(rows) > 256:
        raise DomainError(
            "configured_exposure_capacity",
            "Local dataset exposure history exceeds bounded profile",
            409,
        )
    for row in rows:
        evidence = jsonable_encoder(public(row))
        claim = ExposureClaim(
            origin_namespace="mineagent.local.exposure",
            evidence_id=str(row.id),
            evidence_sha256=digest(evidence),
            window=DateWindow(start=row.start_date, end=row.end_date),
            purpose=row.purpose,
            identity_mapping="unresolved",
            mapping_sha256=None,
        )
        previous = claims.get(str(row.id))
        if previous is not None and previous != claim:
            raise DomainError(
                "configured_exposure_drift", "Inherited local claim differs", 409
            )
        claims[str(row.id)] = claim
    result = ExposureHistory(
        claims=tuple(claims[key] for key in sorted(claims)),
        completeness="partial" if claims else "unknown",
        unresolved_reasons=(
            "source_history_incomplete",
            "local_identity_mapping_unverified",
        )
        if claims
        else ("source_history_incomplete",),
    )
    if len(result.claims) > 256 or len(canonical_bytes(result)) > 262144:
        raise DomainError(
            "configured_exposure_capacity",
            "Merged exposure history exceeds profile",
            409,
        )
    return result


def authenticated_open_configured_evaluation(session, fence, authorization, **request):
    from backend.app.rdagent_controller_services import require_rd_controller
    from backend.app.rdagent_evaluation_services import _open_generation_evaluation

    require_rd_controller(session, fence, authorization)
    return _open_generation_evaluation(
        session, fence, **request, exposure_reader=configured_exposure_history
    )
