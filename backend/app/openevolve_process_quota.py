"""024 conservative installation capacity before any process staging byte.

Unsettled or uncontained process reservations keep their full peak hold. Only
exact positive-stop + completed conservative settlement releases that peak; all
retained physical files remain counted. Run-budget charges are never refunded.
"""

import os
import stat
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from backend.app.research_broker_models import (
    ResearchBudgetReservation,
    ResearchBudgetSettlement,
)
from backend.app.research_lifecycle import require_neutral_transaction
from backend.app.research_object_models import (
    ResearchObjectOutcome,
    ResearchObjectPublication,
)
from backend.app.research_object_services import (
    object_storage_guard,
    physical_inventory,
)
from packages.research_runtime.budget_contracts import BudgetVector
from runtimes.research_supervisor.supervisor import _directory


def process_bytes(root, maximum_files):
    root = Path(root).absolute() / "research-processes"
    if not os.path.lexists(root):
        return ()
    result = []
    count = 0

    def walk(fd, owner=None, depth=0, parts=()):
        nonlocal count
        if depth > 5:
            raise ValueError("Process storage depth")
        before = os.fstat(fd)
        for name in sorted(os.listdir(fd)):
            count += 1
            if count > maximum_files:
                raise ValueError("Process storage file count")
            actual_owner = owner
            if depth == 0:
                actual_owner = UUID(name)
                if str(actual_owner) != name:
                    raise ValueError("Process storage owner")
            entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISSOCK(entry.st_mode):
                # Only the fixed host-created IPC listeners are accountable.
                # O_PATH pins their inode without opening/connecting the socket.
                if (
                    len(parts) != 3
                    or parts[2] != "ipc"
                    or name not in {"peer.sock", "events.sock", "broker.sock"}
                    or str(UUID(parts[1])) != parts[1]
                    or entry.st_nlink != 1
                    or entry.st_uid != os.getuid()
                ):
                    raise ValueError("Unaccountable process socket entry")
                child = os.open(name, os.O_PATH | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    info = os.fstat(child)
                    final = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    fields = (
                        "st_dev",
                        "st_ino",
                        "st_mode",
                        "st_nlink",
                        "st_uid",
                        "st_gid",
                        "st_size",
                        "st_mtime_ns",
                        "st_ctime_ns",
                    )
                    signature = lambda item, fields=fields: tuple(
                        getattr(item, key) for key in fields
                    )
                    if signature(entry) != signature(info) or signature(
                        info
                    ) != signature(final):
                        raise ValueError("Concurrent process socket change")
                    result.append((actual_owner, info.st_size))
                finally:
                    os.close(child)
                continue
            child = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd
            )
            try:
                info = os.fstat(child)
                if stat.S_ISDIR(info.st_mode):
                    result.append((actual_owner, 0))
                    walk(child, actual_owner, depth + 1, (*parts, name))
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    result.append((actual_owner, info.st_size))
                else:
                    raise ValueError("Unaccountable process filesystem entry")
            finally:
                os.close(child)
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError("Concurrent process inventory change")

    fd = _directory(root)
    try:
        walk(fd)
    finally:
        os.close(fd)
    return tuple(result)


def process_holds(session):
    from backend.app.research_process_models import (
        ResearchProcessAdmission,
        ResearchProcessReceipt,
        ResearchProcessState,
    )

    rows = list(
        session.execute(
            select(
                ResearchBudgetReservation,
                ResearchProcessAdmission,
                ResearchProcessState,
                ResearchProcessReceipt,
                ResearchBudgetSettlement,
            )
            .select_from(ResearchBudgetReservation)
            .outerjoin(
                ResearchProcessAdmission,
                ResearchProcessAdmission.reservation_id == ResearchBudgetReservation.id,
            )
            .outerjoin(
                ResearchProcessState,
                ResearchProcessState.admission_id == ResearchProcessAdmission.id,
            )
            .outerjoin(
                ResearchProcessReceipt,
                (ResearchProcessReceipt.admission_id == ResearchProcessAdmission.id)
                & (ResearchProcessReceipt.kind == "stop"),
            )
            .outerjoin(
                ResearchBudgetSettlement,
                ResearchBudgetSettlement.reservation_id == ResearchBudgetReservation.id,
            )
            .where(ResearchBudgetReservation.purpose == "process_unit")
            .limit(10001)
        ).all()
    )
    if len(rows) > 10000:
        raise ValueError("Process reservation inventory bound")
    result, seen = [], set()
    for reservation, admission, state, stop, settlement in rows:
        if reservation.id in seen:
            raise ValueError("Process reservation has multiple consumers")
        seen.add(reservation.id)
        amounts = BudgetVector.model_validate(reservation.amounts)
        for row in (admission, state, stop, settlement):
            if row is not None and any(
                getattr(row, k) != getattr(reservation, k)
                for k in ("owner_id", "run_id", "attempt_id", "generation")
            ):
                raise ValueError("Process capacity scope differs")
        if (
            reservation.state == "settled"
            and admission is not None
            and state is not None
            and state.status == "stopped"
            and stop is not None
            and stop.kind == "stop"
            and settlement is not None
            and settlement.reservation_id == reservation.id
            and settlement.outcome in {"measured", "conservative_unknown"}
            and amounts.covers(BudgetVector.model_validate(settlement.charge))
            and state.admission_id == admission.id
            and stop.admission_id == admission.id
        ):
            # Versioned deferred process/settlement guards validate these actual
            # persisted receipts. Physical inventory separately charges retained
            # bytes; no receipt is synthesized and no run-budget refund occurs.
            continue
        result.append((reservation.owner_id, amounts.storage_bytes, 528))
    return tuple(result)


def require_process_capacity(session, fence, amounts, settings, *, extra_files):
    require_neutral_transaction(session, fence, resource_scope="processes")
    amounts = BudgetVector.model_validate(amounts)
    if type(extra_files) is not int or not 1 <= extra_files <= 1024:
        raise ValueError("Fixed process file ceiling")
    from backend.app.rdagent_provider_quota import provider_process_holds

    holds = (*process_holds(session), *provider_process_holds(session))
    pending = list(
        session.scalars(
            select(ResearchObjectPublication)
            .outerjoin(
                ResearchObjectOutcome,
                ResearchObjectOutcome.publication_id == ResearchObjectPublication.id,
            )
            .where(ResearchObjectOutcome.publication_id.is_(None))
            .limit(settings.neutral_object_max_files + 1)
        ).all()
    )
    if len(pending) > settings.neutral_object_max_files:
        raise ValueError("Object publication inventory bound")
    from backend.app.openevolve_upload_quota import upload_holds

    uploads = upload_holds(session)
    with object_storage_guard(settings.storage_root):
        physical = physical_inventory(
            settings.storage_root, max_files=settings.neutral_object_max_files
        )
        processes = process_bytes(
            settings.storage_root, settings.neutral_object_max_files
        )
        entries = [(r.owner_id, r.size_bytes) for r in physical] + list(processes)
        entries += [(r.owner_id, r.peak_bytes) for r in pending] + [
            (owner, size) for owner, size, _files in holds
        ]
        entries += [(owner, size) for owner, size, _count in uploads]
        total = sum(size for _owner, size in entries) + amounts.storage_bytes
        owned = (
            sum(size for owner, size in entries if owner == fence.owner_id)
            + amounts.storage_bytes
        )
        # 528 bounds each admitted native profile's 256 staged files + parent
        # directories, journals, retained exports and simultaneous atomic copies.
        # Applying the larger ceiling to historical controller units is deliberate.
        files = (
            len(physical)
            + len(processes)
            + 4 * len(pending)
            + 528 * len(holds)
            + sum(x[2] for x in uploads)
            + extra_files
        )
        if (
            total > settings.neutral_object_installation_max_bytes
            or owned > settings.neutral_object_owner_max_bytes
            or files > settings.neutral_object_max_files
        ):
            raise ValueError("Combined process/object installation capacity exhausted")


def combined_holds(session, settings):
    """Same installation ceiling for every object writer, explicit024 only."""
    from sqlalchemy import text

    if session.scalars(text("SELECT version_num FROM alembic_version")).all() not in (
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
    from backend.app.openevolve_upload_quota import upload_holds
    from backend.app.rdagent_provider_quota import provider_process_holds

    return (
        *upload_holds(session),
        *process_holds(session),
        *provider_process_holds(session),
        *(
            (owner, size, 1)
            for owner, size in process_bytes(
                settings.storage_root, settings.neutral_object_max_files
            )
        ),
    )


def process_quota_required(session):
    from sqlalchemy import text

    from backend.app.schema_capabilities import capabilities_for

    revisions = session.scalars(text("SELECT version_num FROM alembic_version")).all()
    if len(revisions) != 1:
        raise ValueError("Single authoritative installation schema required")
    return capabilities_for(revisions[0]).process_installation_quota


def admit_process_capacity(session, fence, amounts, settings):
    if not process_quota_required(session):
        return
    from backend.app.core.config import Settings

    if type(settings) is not Settings:
        raise ValueError("Explicit installation Settings required for process capacity")
    require_process_capacity(session, fence, amounts, settings, extra_files=528)
