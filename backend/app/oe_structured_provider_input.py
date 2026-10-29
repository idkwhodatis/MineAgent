"""Normal027 fresh pre-admission data, with an already-existing explicit release.

Caller owns shared lifecycle and a transaction. Owner submission lock precedes
native FD guard and is retained through declaration commit. No fake attempt or
fence, detached preflight grant, label inputs, or implicit result release.
"""

from contextlib import contextmanager

from sqlalchemy import select

from backend.app.engine_result_release import release_bindings, source_key
from backend.app.execution_snapshot_history import read_snapshot_in_session
from backend.app.execution_snapshot_models import ExecutionSnapshotPublication
from backend.app.experiment_services import record_exposure
from backend.app.factor_native_io import native_storage_guard
from backend.app.models import DataUsageExposure
from backend.app.oe_structured_preflight_data import _resolve_retained_data, fail
from backend.app.oe_structured_provider_submission import (
    ProviderStructuredSearchRequest,
)
from backend.app.oe_structured_source_qualification import require_source_family
from backend.app.research_lifecycle import _try_neutral_lock
from packages.factors.contracts import DateWindow
from packages.openevolve_structured.candidate import _raw_fields


@contextmanager
def retained_submission_data(session, owner_id, request, settings):
    if type(request) is not ProviderStructuredSearchRequest:
        fail()
    request = ProviderStructuredSearchRequest.model_validate(_raw_fields(request))
    _try_neutral_lock(session, f"submit:{owner_id}")
    publication = session.scalar(
        select(ExecutionSnapshotPublication).filter_by(
            id=request.snapshot_id,
            owner_id=owner_id,
        )
    )
    if publication is None:
        fail()
    key = source_key("execution_snapshot", publication.id, "factor")
    existing = session.scalar(
        select(DataUsageExposure).filter_by(owner_id=owner_id, source_key=key)
    )
    if existing is None:
        fail("structured_snapshot_release_required")
    require_source_family(session, owner_id, publication.run_id)
    with native_storage_guard(settings.storage_root):
        historical = read_snapshot_in_session(
            session, owner_id, publication.id, settings
        )
        run, bindings = release_bindings(
            session,
            owner_id,
            publication.run_id,
            publication.id,
            "execution_snapshot",
            historical,
        )
        if len(bindings) != 1:
            fail()
        role, dataset, evidence = bindings[0]
        # Existing immutable row was found first; this validates exact evidence
        # and cannot silently create a missing release on the user's behalf.
        receipt = record_exposure(
            session,
            owner_id=owner_id,
            dataset=dataset,
            run=run,
            window=DateWindow(start=dataset.start_date, end=dataset.end_date),
            purpose="engine_execution_snapshot_release",
            source_key=key,
            provenance={
                **evidence,
                "role": role,
                "dataset_version_id": str(dataset.id),
                "dataset_content_sha256": dataset.content_sha256,
            },
        )
        if (
            role != "factor"
            or receipt.id != existing.id
            or (
                receipt.owner_id,
                receipt.run_id,
                receipt.experiment_id,
                receipt.split_version_id,
            )
            != (owner_id, run.id, run.experiment_id, run.split_version_id)
        ):
            fail()
        yield _resolve_retained_data(
            session,
            owner_id,
            request,
            settings,
            historical,
            (receipt.id,),
            run.id,
        )


def resolve_submission_data(session, owner_id, request, settings):
    """Preflight-only buffer; actual submit retains the context through commit."""
    with retained_submission_data(session, owner_id, request, settings) as data:
        return data
