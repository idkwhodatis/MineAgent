"""Fresh-fenced execution snapshot publication; no legacy admission or parser.

An unresolved outcome retains every byte and the whole reserved peak. Detached
DTOs are never accepted as publication/reader authority; actual stopped outputs
are resolved again in every fresh transaction on both sides of external writes.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from backend.app.declared_upload_capacity import dataset_inventory_enabled
from backend.app.execution_snapshot_assembly import resolve_snapshot_materialization
from backend.app.execution_snapshot_io import (
    inventory,
    journal_address,
    verify_snapshot_bytes,
)
from backend.app.execution_snapshot_models import (
    ExecutionSnapshotObject,
    ExecutionSnapshotOutcome,
    ExecutionSnapshotPartition,
    ExecutionSnapshotPublication,
)
from backend.app.factor_native_io import (
    require_native_storage_guard,
)
from backend.app.research_lifecycle import (
    require_neutral_transaction,
)
from backend.app.services import DomainError
from packages.factors.contracts import FeatureRow, canonical_bytes, digest
from packages.factors.execution_snapshot import (
    ExecutionPublicationJournal,
)
from packages.research_runtime.budget_contracts import BudgetVector

SCHEMA = "mineagent.execution-snapshot-restore-closure.v1"


def _error(code="execution_snapshot_unverified"):
    return DomainError(code, "Execution snapshot authority is unavailable", 409)


from backend.app.execution_snapshot_capacity import (
    pending_snapshot_usage,
    snapshot_capability,
)


def _require(session, fence, settings):
    require_neutral_transaction(session, fence, resource_scope="factor_numeric")
    require_native_storage_guard(settings.storage_root)
    if not snapshot_capability(session):
        raise _error("execution_snapshot_schema_unsupported")


def _row(session, fence, publication_id):
    row = session.get(ExecutionSnapshotPublication, publication_id)
    if row is None or any(
        getattr(row, k) != getattr(fence, k)
        for k in ("owner_id", "run_id", "attempt_id", "generation")
    ):
        raise _error()
    return row


def decode_publication(row):
    journal = ExecutionPublicationJournal.model_validate_json(
        canonical_bytes(row.journal)
    )
    manifest = journal.manifest
    key, sha, _ = journal_address(journal)
    if (
        row.id,
        row.evaluation_id,
        row.source_id,
        row.start_id,
        row.factor_version_id,
        row.reservation_id,
        row.manifest,
        row.manifest_sha256,
        row.manifest_object_id,
        row.manifest_size_bytes,
        row.manifest_storage_key,
        row.journal_sha256,
        row.journal_key,
        row.peak_bytes,
        row.peak_entries,
    ) != (
        journal.publication_id,
        manifest.evaluation_id,
        manifest.source_id,
        manifest.generation_start_id,
        manifest.input.version.factor_version_id,
        journal.reservation_id,
        manifest.model_dump(mode="json"),
        journal.manifest_object.sha256,
        journal.manifest_object.object_id,
        journal.manifest_object.size_bytes,
        journal.manifest_object.storage_key,
        sha,
        key,
        journal.peak_bytes,
        journal.peak_entries,
    ):
        raise _error()
    if any(
        getattr(row, k) != getattr(manifest, k)
        for k in ("owner_id", "run_id", "attempt_id", "generation")
    ):
        raise _error()
    return journal


def _capacity(session, fence, settings, fd, *, extra=(0, 0)):
    import time

    from backend.app.factor_native_services import (
        pending_native_usage,
        verify_native_journals,
    )
    from backend.app.factor_services import physical_inventory
    from backend.app.rdagent_source_capacity import pending_source_usage
    from runtimes.factor_parser.supervisor import assert_quiescent

    assert_quiescent(settings.storage_root)
    native_entries = []
    total, owned, count = physical_inventory(
        fd,
        fence.owner_id,
        time.monotonic() + 20,
        settings.factor_max_files,
        allow_staging=True,
        native=True,
        native_entries=native_entries,
        storage_root=settings.storage_root,
        include_dataset_inventory=dataset_inventory_enabled(session),
    )
    verify_native_journals(session, native_entries, settings)
    for reader in (pending_native_usage, pending_source_usage, pending_snapshot_usage):
        held, own, entries = reader(session, fence.owner_id)
        total += held
        owned += own
        count += entries
    if (
        total + extra[0] > settings.factor_installation_max_bytes
        or owned + extra[0] > settings.factor_owner_max_bytes
        or count + extra[1] > settings.factor_max_files
    ):
        raise _error("execution_snapshot_capacity")


def _physical_receipt(journal):
    return digest(
        {
            "journal_sha256": journal_address(journal)[1],
            "objects": [o.model_dump(mode="json") for o in inventory(journal)],
        }
    )


def _resolve_existing(session, fence, row, settings):
    _require(session, fence, settings)
    journal = decode_publication(row)
    manifest, physical, rows = resolve_snapshot_materialization(
        session,
        fence,
        row.evaluation_id,
        settings,
        snapshot_id=row.id,
        existing=journal.manifest,
    )
    physical[journal.manifest_object.storage_key] = canonical_bytes(manifest)
    current = session.scalars(
        select(ExecutionSnapshotPartition)
        .where(ExecutionSnapshotPartition.publication_id == row.id)
        .order_by(ExecutionSnapshotPartition.ordinal)
    ).all()
    if len(current) != len(manifest.partitions):
        raise _error()
    for persisted, part in zip(current, manifest.partitions, strict=True):
        if (
            persisted.ordinal,
            persisted.cutoff_id,
            persisted.native_publication_id,
            persisted.normalizer_admission_id,
            persisted.stop_receipt_id,
            persisted.extraction_receipt_id,
            persisted.binding,
            persisted.binding_sha256,
            persisted.stop_kind,
            persisted.extraction_kind,
        ) != (
            part.ordinal,
            part.cutoff_id,
            part.native_publication_id,
            part.normalizer_admission_id,
            part.stop_receipt_id,
            part.extraction_receipt_id,
            part.model_dump(mode="json"),
            digest(part),
            "stop",
            "extraction",
        ):
            raise _error()
    return journal, physical, rows


@dataclass(frozen=True)
class VerifiedExecutionSnapshot:
    snapshot_id: UUID
    source_id: UUID
    evaluation_id: UUID
    generation_start_id: UUID
    factor_version_id: UUID
    input_sha256: str
    output_sha256: str
    manifest_sha256: str
    rows: tuple[FeatureRow, ...]


def read_execution_snapshot(session, fence, snapshot_id, settings):
    from backend.app.research_broker_models import (
        ResearchBudgetReservation,
        ResearchBudgetSettlement,
    )

    _require(session, fence, settings)
    row = _row(session, fence, snapshot_id)
    outcome = session.get(ExecutionSnapshotOutcome, row.id)
    if outcome is None:
        raise _error("execution_snapshot_uncommitted")
    journal, _, rows = _resolve_existing(session, fence, row, settings)
    verify_snapshot_bytes(settings.storage_root, journal)
    manifest = journal.manifest
    if (
        outcome.manifest_sha256,
        outcome.input_sha256,
        outcome.output_sha256,
        outcome.physical_receipt_sha256,
    ) != (
        journal.manifest_object.sha256,
        manifest.input_sha256,
        manifest.output_rows_sha256,
        _physical_receipt(journal),
    ):
        raise _error()
    registered = session.scalars(
        select(ExecutionSnapshotObject).where(
            ExecutionSnapshotObject.publication_id == row.id
        )
    ).all()
    by_id = {o.id: o for o in registered}
    if set(by_id) != {o.object_id for o in inventory(journal)}:
        raise _error()
    for obj in inventory(journal):
        actual = by_id[obj.object_id]
        if (
            actual.owner_id,
            actual.object_kind,
            actual.sha256,
            actual.size_bytes,
            actual.storage_key,
        ) != (obj.owner_id, obj.kind, obj.sha256, obj.size_bytes, obj.storage_key):
            raise _error()
    budget = session.get(ResearchBudgetReservation, row.reservation_id)
    settlement = session.scalar(
        select(ResearchBudgetSettlement).where(
            ResearchBudgetSettlement.reservation_id == row.reservation_id
        )
    )
    if (
        budget is None
        or settlement is None
        or budget.purpose != "object_storage"
        or budget.logical_key != "execution-snapshot:" + str(row.id)
        or budget.state != "settled"
        or settlement.outcome != "conservative_unknown"
        or settlement.charge != budget.amounts
        or budget.amounts != BudgetVector(storage_bytes=row.peak_bytes).model_dump()
    ):
        raise _error()
    return VerifiedExecutionSnapshot(
        row.id,
        manifest.source_id,
        manifest.evaluation_id,
        manifest.generation_start_id,
        manifest.input.version.factor_version_id,
        manifest.input_sha256,
        manifest.output_rows_sha256,
        journal.manifest_object.sha256,
        rows,
    )


def _require_pending_reservation(session, fence, row):
    from backend.app.research_broker_services import require_reservation

    reservation = require_reservation(
        session,
        fence,
        row.reservation_id,
        purpose="object_storage",
        minimum=BudgetVector(storage_bytes=row.peak_bytes),
    )
    if (
        reservation.logical_key != "execution-snapshot:" + str(row.id)
        or reservation.amounts
        != BudgetVector(storage_bytes=row.peak_bytes).model_dump()
    ):
        raise _error()
    return reservation


def _finalize_snapshot(session, fence, row, settings):
    from backend.app.research_broker_services import settle_budget

    _require(session, fence, settings)
    if session.get(ExecutionSnapshotOutcome, row.id) is not None:
        return read_execution_snapshot(session, fence, row.id, settings).snapshot_id
    journal, _, _ = _resolve_existing(session, fence, row, settings)
    verify_snapshot_bytes(settings.storage_root, journal)
    reservation = _require_pending_reservation(session, fence, row)
    for obj in inventory(journal):
        session.add(
            ExecutionSnapshotObject(
                id=obj.object_id,
                owner_id=obj.owner_id,
                publication_id=row.id,
                object_kind=obj.kind,
                sha256=obj.sha256,
                size_bytes=obj.size_bytes,
                storage_key=obj.storage_key,
            )
        )
    session.add(
        ExecutionSnapshotOutcome(
            publication_id=row.id,
            **{
                k: getattr(fence, k)
                for k in ("owner_id", "run_id", "attempt_id", "generation")
            },
            manifest_sha256=journal.manifest_object.sha256,
            input_sha256=journal.manifest.input_sha256,
            output_sha256=journal.manifest.output_rows_sha256,
            physical_receipt_sha256=_physical_receipt(journal),
        )
    )
    settle_budget(
        session,
        fence,
        row.reservation_id,
        charge=BudgetVector.model_validate(reservation.amounts),
        outcome="conservative_unknown",
    )
    session.flush()
    return row.id


def publish_execution_snapshot(factory, fence, evaluation_id, settings):
    from uuid import uuid4

    from backend.app.core.maintenance import shared_lifecycle
    from backend.app.execution_snapshot_io import publish_snapshot_bytes
    from backend.app.factor_native_io import native_storage_guard
    from backend.app.research_broker_services import reserve_budget
    from backend.app.research_lifecycle import neutral_transaction
    from packages.factors.execution_snapshot import ExecutionObject

    # Initial admission is separately committed before any publication write.
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        native_storage_guard(settings.storage_root) as fd,
        neutral_transaction(factory, fence, resource_scope="factor_numeric") as session,
    ):
        _require(session, fence, settings)
        previous = session.scalar(
            select(ExecutionSnapshotPublication).where(
                ExecutionSnapshotPublication.evaluation_id == evaluation_id
            )
        )
        if previous is not None:
            publication_id = _row(session, fence, previous.id).id
        else:
            publication_id = uuid4()
            manifest, physical, _ = resolve_snapshot_materialization(
                session, fence, evaluation_id, settings, snapshot_id=publication_id
            )
            raw = canonical_bytes(manifest)
            manifest_object = ExecutionObject(
                object_id=uuid4(),
                owner_id=fence.owner_id,
                kind="execution_manifest",
                sha256=digest(manifest),
                size_bytes=len(raw),
                storage_key=f"factors/{fence.owner_id}/objects/{digest(manifest)}.json",
            )
            total = (
                manifest_object.size_bytes
                + manifest.assembled_rows.size_bytes
                + sum(p.values.size_bytes for p in manifest.partitions)
            )
            peak = 2 * total + 4_194_304
            entries = 2 * (len(manifest.partitions) + 3) + 3
            _capacity(session, fence, settings, fd, extra=(peak, entries))
            reservation = reserve_budget(
                session,
                fence,
                purpose="object_storage",
                logical_key="execution-snapshot:" + str(publication_id),
                amounts=BudgetVector(storage_bytes=peak),
            )
            journal = ExecutionPublicationJournal(
                schema_version="mineagent.execution-snapshot-publication.v1",
                publication_id=publication_id,
                reservation_id=reservation.id,
                manifest=manifest,
                manifest_object=manifest_object,
                peak_bytes=peak,
                peak_entries=entries,
            )
            key, journal_sha, _ = journal_address(journal)
            scope = {
                k: getattr(fence, k)
                for k in ("owner_id", "run_id", "attempt_id", "generation")
            }
            session.add(
                ExecutionSnapshotPublication(
                    id=publication_id,
                    **scope,
                    evaluation_id=evaluation_id,
                    source_id=manifest.source_id,
                    start_id=manifest.generation_start_id,
                    factor_version_id=manifest.input.version.factor_version_id,
                    reservation_id=reservation.id,
                    manifest=manifest.model_dump(mode="json"),
                    manifest_sha256=manifest_object.sha256,
                    manifest_object_id=manifest_object.object_id,
                    manifest_size_bytes=manifest_object.size_bytes,
                    manifest_storage_key=manifest_object.storage_key,
                    journal=journal.model_dump(mode="json"),
                    journal_sha256=journal_sha,
                    journal_key=key,
                    peak_bytes=peak,
                    peak_entries=entries,
                )
            )
            session.flush()  # shared immediate reservation-consumer arbitration
            for part in manifest.partitions:
                session.add(
                    ExecutionSnapshotPartition(
                        publication_id=publication_id,
                        **scope,
                        ordinal=part.ordinal,
                        cutoff_id=part.cutoff_id,
                        native_publication_id=part.native_publication_id,
                        normalizer_admission_id=part.normalizer_admission_id,
                        stop_receipt_id=part.stop_receipt_id,
                        extraction_receipt_id=part.extraction_receipt_id,
                        stop_kind="stop",
                        extraction_kind="extraction",
                        binding=part.model_dump(mode="json"),
                        binding_sha256=digest(part),
                    )
                )
            session.flush()
    # Reacquire actual bytes and all current authority, never use first txn data.
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        native_storage_guard(settings.storage_root) as fd,
    ):
        with neutral_transaction(
            factory, fence, resource_scope="factor_numeric"
        ) as session:
            _require(session, fence, settings)
            row = _row(session, fence, publication_id)
            if session.get(ExecutionSnapshotOutcome, row.id) is not None:
                return read_execution_snapshot(
                    session, fence, row.id, settings
                ).snapshot_id
            _require_pending_reservation(session, fence, row)
            journal, physical, _ = _resolve_existing(session, fence, row, settings)
            _capacity(session, fence, settings, fd)
        publish_snapshot_bytes(settings.storage_root, journal, physical)
        with neutral_transaction(
            factory, fence, resource_scope="factor_numeric"
        ) as session:
            return _finalize_snapshot(
                session, fence, _row(session, fence, publication_id), settings
            )
