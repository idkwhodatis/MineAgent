"""Additive upload holds participate in the same physical object quota family.

No credit for already-written chunks: conservative double counting is explicit.
The hold is removed only by a separately verified conservative settlement after
containment. Every object writer includes these holds, not only upload begins.
"""

import threading
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from pathlib import Path

from sqlalchemy import select, text

from backend.app.research_broker_models import ResearchBudgetReservation
from backend.app.research_lifecycle import neutral_transaction
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.fences import AttemptFence

_admission = ContextVar("oe_upload_capacity_admission", default=None)


@contextmanager
def upload_transaction(factory, fence, settings):
    """Only acquisition may wait; one fresh transaction stays under the FS fence."""
    from backend.app.openevolve_lock_admission import (
        acquisition_deadline,
        acquisition_window,
        publication_storage_admission,
    )

    root = Path(settings.storage_root).absolute()
    if type(fence) is not AttemptFence or _admission.get() is not None:
        raise ValueError("Exact non-nested upload admission required")
    deadline = acquisition_deadline(fence)
    with (
        acquisition_window(fence, deadline) if deadline is not None else nullcontext(),
        publication_storage_admission(root, fence) as descriptors,
        neutral_transaction(factory, fence, resource_scope="objects") as session,
    ):
        transaction = session.get_transaction()
        token = _admission.set(
            (root, fence, threading.get_ident(), session, transaction, descriptors)
        )
        try:
            _require_held(session, root, fence)
            yield session
        finally:
            _admission.reset(token)


def _require_held(session, root, fence):
    import os

    from backend.app.core.atomic_storage import _open_directory

    held = _admission.get()
    if held is None or (
        held[:3] != (Path(root).absolute(), fence, threading.get_ident())
        or held[3] is not session
        or held[4] is not session.get_transaction()
        or not held[4].is_active
    ):
        raise ValueError("Upload capacity admission scope differs")
    for path, fd in zip(
        (Path(root).absolute(), Path(root).absolute() / "research-objects"),
        held[5],
        strict=True,
    ):
        pinned = os.fstat(fd)
        current_fd = _open_directory(path)
        try:
            current = os.fstat(current_fd)
            if (current.st_dev, current.st_ino) != (pinned.st_dev, pinned.st_ino):
                raise ValueError("Upload capacity locked directory changed")
        finally:
            os.close(current_fd)


def _capacity_guard(session, root, fence):
    from backend.app.research_object_services import object_storage_guard

    if _admission.get() is None:
        return object_storage_guard(root)
    _require_held(session, root, fence)
    return nullcontext()


def upload_holds(session):
    revisions = session.scalars(text("SELECT version_num FROM alembic_version")).all()
    if revisions not in (
        ["024_openevolve_initial"],
        ["025_structured_factor_strategy"],
        ["026_rd_seeded_submissions"],
        ["027_rd_controller_v3"],
        ["027a_contract_repairs"],
        ["027b_provider_material"],
        ["027c_seeded_controller_v4"],
        ["028_oe_structured_search"],
        ["029_research_workflow"],
        ["030_declared_upload_basket"],
        ["031_workflow_structured_stage"],
        ["032_normal_oe_note_pins"],
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
    if revisions in (
        ["028_oe_structured_search"],
        ["029_research_workflow"],
        ["030_declared_upload_basket"],
        ["031_workflow_structured_stage"],
        ["032_normal_oe_note_pins"],
    ):
        from backend.app.oe_structured_quota import checkpoint_upload_holds

        holds.extend(checkpoint_upload_holds(session))
    return tuple(holds)


def require_upload_capacity(session, fence, begin, settings):
    from backend.app.research_object_models import (
        ResearchObjectOutcome,
        ResearchObjectPublication,
    )
    from backend.app.research_object_services import (
        physical_inventory,
    )

    with _capacity_guard(session, settings.storage_root, fence):
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
