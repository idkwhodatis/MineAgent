"""Additive upload holds participate in the same physical object quota family.

No credit for already-written chunks: conservative double counting is explicit.
The hold is removed only by a separately verified conservative settlement after
containment. Every object writer includes these holds, not only upload begins.
"""

from sqlalchemy import select, text

from backend.app.research_broker_models import ResearchBudgetReservation
from packages.research_runtime.budget_contracts import BudgetVector


def upload_holds(session):
    if session.scalars(text("SELECT version_num FROM alembic_version")).all() not in (
        ["024_openevolve_initial"],
        ["025_structured_factor_strategy"],
        ["026_rd_seeded_submissions"],
        ["027_rd_controller_v3"],
    ):
        return ()
    from backend.app.openevolve_operational_models import SearchCheckpointUpload

    rows = session.execute(
        select(SearchCheckpointUpload, ResearchBudgetReservation)
        .join(
            ResearchBudgetReservation,
            SearchCheckpointUpload.reservation_id == ResearchBudgetReservation.id,
        )
        .where(ResearchBudgetReservation.state != "settled")
        .limit(10001)
    ).all()
    if len(rows) > 10000:
        raise ValueError("bounded upload hold inventory")
    holds = []
    for upload, reservation in rows:
        amounts = BudgetVector.model_validate(reservation.amounts)
        if reservation.purpose != "object_storage" or any(
            getattr(upload, k) != getattr(reservation, k)
            for k in ("owner_id", "run_id", "attempt_id", "generation")
        ):
            raise ValueError("upload quota scope")
        holds.append(
            (
                upload.owner_id,
                amounts.storage_bytes,
                4 * (upload.chunk_count + upload.file_count + 1),
            )
        )
    return tuple(holds)


def require_upload_capacity(session, fence, begin, settings):
    from backend.app.research_object_models import (
        ResearchObjectOutcome,
        ResearchObjectPublication,
    )
    from backend.app.research_object_services import (
        object_storage_guard,
        physical_inventory,
    )

    with object_storage_guard(settings.storage_root):
        inventory = physical_inventory(
            settings.storage_root, max_files=settings.neutral_object_max_files
        )
        publications = session.scalars(
            select(ResearchObjectPublication)
            .outerjoin(
                ResearchObjectOutcome,
                ResearchObjectOutcome.publication_id == ResearchObjectPublication.id,
            )
            .where(ResearchObjectOutcome.publication_id.is_(None))
            .limit(settings.neutral_object_max_files + 1)
        ).all()
        if len(publications) > settings.neutral_object_max_files:
            raise ValueError("upload publication quota inventory")
        from backend.app.openevolve_process_quota import combined_holds

        holds = combined_holds(session, settings)
        total = (
            sum(x.size_bytes for x in inventory)
            + sum(x.peak_bytes for x in publications)
            + sum(x[1] for x in holds)
        )
        owner = (
            sum(x.size_bytes for x in inventory if x.owner_id == fence.owner_id)
            + sum(x.peak_bytes for x in publications if x.owner_id == fence.owner_id)
            + sum(x[1] for x in holds if x[0] == fence.owner_id)
        )
        files = (
            len(inventory)
            + 4 * len(publications)
            + sum(x[2] for x in holds)
            + 4 * (len(begin.chunks) + len(begin.files) + 1)
        )
        if (
            total + begin.transfer_peak_bytes
            > settings.neutral_object_installation_max_bytes
            or owner + begin.transfer_peak_bytes
            > settings.neutral_object_owner_max_bytes
            or files > settings.neutral_object_max_files
        ):
            raise ValueError("upload physical quota exhausted")
