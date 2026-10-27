"""Explicit historical disclosure on the existing ledger, never execution authority.

Phase one privately buffers native-verified immutable evidence under its own
storage guards. Phase two rechecks DB commitments under owner-first transaction,
records/validates exposure, and commits before the caller may return that buffer.
No nested independently owned guards and no post-guard filesystem reread.
"""

import json

from sqlalchemy import select, text

from backend.app.core.maintenance import shared_lifecycle
from backend.app.engine_run_views import owned_engine_run
from backend.app.execution_snapshot_history import (
    read_historical_execution_snapshot,
    same,
    terminal_owner,
)
from backend.app.execution_snapshot_models import (
    ExecutionSnapshotOutcome,
    ExecutionSnapshotPublication,
)
from backend.app.experiment_services import record_exposure
from backend.app.factor_analysis_history import read_historical_direct_analysis
from backend.app.factor_analysis_models import (
    FactorAnalysisPlan,
    FactorDirectAnalysisIntent,
    FactorDirectAnalysisOutcome,
)
from backend.app.models import DatasetVersion, DataUsageExposure
from backend.app.services import DomainError, advisory_lock
from packages.factors.contracts import DateWindow, digest
from packages.factors.execution_snapshot import ExecutionSnapshotManifest


def unavailable():
    raise DomainError(
        "engine_result_inconsistent", "Owned immutable historical result differs", 409
    )


def source_key(kind, resource_id, role):
    return f"engine-result:{kind}:{resource_id}:{role}:v1"


def owned_resource(session, owner_id, run_id, resource_id, kind):
    run = owned_engine_run(session, run_id, owner_id)
    model = (
        ExecutionSnapshotPublication
        if kind == "execution_snapshot"
        else FactorDirectAnalysisIntent
    )
    resource = session.scalar(
        select(model).filter_by(id=resource_id, owner_id=owner_id, run_id=run_id)
    )
    if resource is None:
        raise DomainError("not_found", "Engine result not found", 404)
    return run, resource


def snapshot_bindings(session, owner_id, run, publication, verified=None):
    terminal_owner(session, owner_id, run.id)
    outcome = session.get(ExecutionSnapshotOutcome, publication.id)
    if outcome is None:
        unavailable()
    same(publication, outcome)
    session.execute(
        text("SELECT mineagent_execution_snapshot_check(:id)"), {"id": publication.id}
    )
    manifest = ExecutionSnapshotManifest.model_validate_json(
        json.dumps(publication.manifest, allow_nan=False)
    )
    if (
        digest(manifest) != publication.manifest_sha256
        or manifest.owner_id != owner_id
        or manifest.run_id != run.id
        or manifest.snapshot_id != publication.id
        or manifest.input.dataset.dataset_version_id != run.dataset_version_id
        or (outcome.manifest_sha256, outcome.input_sha256, outcome.output_sha256)
        != (
            publication.manifest_sha256,
            manifest.input_sha256,
            manifest.output_rows_sha256,
        )
    ):
        unavailable()
    if verified is not None and (
        verified.snapshot_id,
        verified.source_id,
        verified.evaluation_id,
        verified.factor_version_id,
        verified.manifest_sha256,
        verified.input_sha256,
        verified.output_sha256,
    ) != (
        publication.id,
        publication.source_id,
        publication.evaluation_id,
        publication.factor_version_id,
        publication.manifest_sha256,
        outcome.input_sha256,
        outcome.output_sha256,
    ):
        unavailable()
    dataset = session.get(DatasetVersion, run.dataset_version_id)
    if (
        dataset is None
        or dataset.owner_id != owner_id
        or dataset.content_sha256 != manifest.input.dataset.content_sha256
    ):
        unavailable()
    evidence = {
        "run_id": str(run.id),
        "execution_snapshot_id": str(publication.id),
        "manifest_sha256": publication.manifest_sha256,
        "input_sha256": outcome.input_sha256,
        "output_sha256": outcome.output_sha256,
        "extent": "conservative_full_retained_dataset",
        "qualification": "historical_disclosure_not_live_execution_authority",
    }
    return dataset, evidence


def release_bindings(session, owner_id, run_id, resource_id, kind, verified):
    run, resource = owned_resource(session, owner_id, run_id, resource_id, kind)
    if kind == "execution_snapshot":
        dataset, evidence = snapshot_bindings(
            session, owner_id, run, resource, verified
        )
        return run, [("factor", dataset, evidence)]
    outcome = session.get(FactorDirectAnalysisOutcome, resource_id)
    plan = session.get(FactorAnalysisPlan, resource.plan_id)
    publication = session.get(ExecutionSnapshotPublication, resource.snapshot_id)
    if outcome is None or plan is None or publication is None:
        unavailable()
    same(resource, outcome)
    same(resource, publication)
    if (plan.owner_id, plan.run_id) != (owner_id, run.id) or (
        verified.analysis_id,
        verified.snapshot_id,
        verified.admission_id,
        verified.result_sha256,
        verified.measurement_sha256,
    ) != (
        resource.id,
        resource.snapshot_id,
        outcome.admission_id,
        outcome.result_sha256,
        outcome.measurement_sha256,
    ):
        unavailable()
    session.execute(
        text("SELECT mineagent_direct_result_check(:id)"), {"id": resource.id}
    )
    factor_dataset, evidence = snapshot_bindings(session, owner_id, run, publication)
    label_dataset = session.get(DatasetVersion, resource.label_source_id)
    if (
        label_dataset is None
        or label_dataset.owner_id != owner_id
        or label_dataset.id != plan.label_source_dataset_id
        or label_dataset.content_sha256 != plan.label_source_manifest_sha256
    ):
        unavailable()
    evidence.update(
        {
            "analysis_id": str(resource.id),
            "plan_id": str(plan.id),
            "plan_config_sha256": plan.config_sha256,
            "plan_input_sha256": plan.input_sha256,
            "objective_sha256": plan.objective_sha256,
            "source_policy_sha256": plan.source_policy_sha256,
            "label_manifest_sha256": resource.label_manifest_sha256,
            "label_recipe_sha256": resource.label_recipe_sha256,
            "protocol_sha256": resource.protocol_sha256,
            "analysis_input_sha256": resource.analysis_input_sha256,
            "result_sha256": outcome.result_sha256,
            "measurement_sha256": outcome.measurement_sha256,
        }
    )
    return run, [
        ("factor", factor_dataset, evidence),
        ("label", label_dataset, evidence),
    ]


def disclose(factory, settings, *, owner_id, run_id, resource_id, kind, create_release):
    if kind not in {"execution_snapshot", "direct_analysis"}:
        unavailable()
    # Owner/version lookup first prevents expensive verification of another run.
    with factory() as session:
        owned_resource(session, owner_id, run_id, resource_id, kind)
        if not create_release:
            for role in (
                ("factor",) if kind == "execution_snapshot" else ("factor", "label")
            ):
                if (
                    session.scalar(
                        select(DataUsageExposure.id).filter_by(
                            owner_id=owner_id,
                            source_key=source_key(kind, resource_id, role),
                        )
                    )
                    is None
                ):
                    raise DomainError(
                        "engine_result_release_required",
                        "Explicit historical result release is required",
                        409,
                    )
    reader = (
        read_historical_execution_snapshot
        if kind == "execution_snapshot"
        else read_historical_direct_analysis
    )
    verified = reader(factory, owner_id, resource_id, settings)
    ids = []
    # Independent phase: only DB commitments; never reopen files after verification.
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        factory.begin() as session,
    ):
        advisory_lock(session, f"submit:{owner_id}")
        run, bindings = release_bindings(
            session, owner_id, run_id, resource_id, kind, verified
        )
        for role, dataset, evidence in bindings:
            key = source_key(kind, resource_id, role)
            if (
                not create_release
                and session.scalar(
                    select(DataUsageExposure.id).filter_by(
                        owner_id=owner_id, source_key=key
                    )
                )
                is None
            ):
                raise DomainError(
                    "engine_result_release_required",
                    "Explicit historical result release is required",
                    409,
                )
            row = record_exposure(
                session,
                owner_id=owner_id,
                dataset=dataset,
                run=run,
                window=DateWindow(start=dataset.start_date, end=dataset.end_date),
                purpose=f"engine_{kind}_release",
                source_key=key,
                provenance={
                    **evidence,
                    "role": role,
                    "dataset_version_id": str(dataset.id),
                    "dataset_content_sha256": dataset.content_sha256,
                },
            )
            if (row.owner_id, row.run_id, row.experiment_id, row.split_version_id) != (
                owner_id,
                run.id,
                run.experiment_id,
                run.split_version_id,
            ):
                unavailable()
            ids.append(row.id)
    # Context manager committed successfully; only now can this buffer escape.
    return verified, tuple(ids)
