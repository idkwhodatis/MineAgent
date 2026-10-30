"""Fresh-fenced per-candidate opaque publication; no dispatcher/launcher entrypoint.

A committed reservation precedes every external write. The filesystem fence
survives SQL transaction loss; a missing outcome retains every charge and byte.
Neither DTOs nor a previous resolver result authorize a later transaction.
"""

import time

from sqlalchemy import select

from backend.app.core.maintenance import shared_lifecycle
from backend.app.declared_upload_capacity import dataset_inventory_enabled
from backend.app.factor_native_io import (
    native_storage_guard,
    publish_candidate_bytes,
    read_exact,
    require_native_storage_guard,
    verify_candidate_bytes,
)
from backend.app.factor_native_models import (
    NativeHDFObject,
    NativeHDFOutcome,
    NativeHDFPublication,
)
from backend.app.research_input_capacity import input_inventory_enabled
from backend.app.research_lifecycle import (
    neutral_transaction,
    require_neutral_transaction,
)
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.factors.native_publication import NativePublicationIntent
from packages.research_runtime.budget_contracts import BudgetVector


def error(code="native_publication_unverified"):
    return DomainError(code, "Native publication authority is unavailable", 409)


def footprint(intent):
    intent = NativePublicationIntent.model_validate(intent)
    size = len(canonical_bytes(intent))
    if size > 65536:
        raise error("native_publication_capacity")
    return 2 * intent.output.object.size_bytes + 131072, 8


def decode_intent(row):
    intent = NativePublicationIntent.model_validate_json(canonical_bytes(row.intent))
    obj = intent.output.object
    if (row.stop_kind, row.extraction_kind) != ("stop", "extraction"):
        raise error("native_publication_receipt_kind")
    if (
        row.id,
        row.reservation_id,
        row.admission_id,
        row.source_id,
        row.source_object_id,
        row.source_sha256,
        row.stop_receipt_id,
        row.extraction_receipt_id,
        row.object_id,
        row.sha256,
        row.size_bytes,
        row.storage_key,
        row.journal_key,
        row.intent_sha256,
        row.peak_bytes,
        row.peak_entries,
    ) != (
        intent.journal_id,
        intent.reservation_id,
        intent.admission.id,
        intent.source_id,
        intent.source_object_id,
        intent.source_sha256,
        intent.stop_receipt_id,
        intent.extraction_receipt_id,
        obj.object_id,
        obj.sha256,
        obj.size_bytes,
        intent.output.storage_key,
        intent.journal_key,
        digest(intent),
        *footprint(intent),
    ):
        raise error("native_publication_row_drift")
    if row.cutoff_id != intent.admission.logical_operation_id:
        raise error("native_publication_cutoff_drift")
    if any(
        getattr(row, key) != getattr(intent.admission, key)
        for key in ("owner_id", "run_id", "attempt_id", "generation")
    ):
        raise error("native_publication_scope")
    return intent


def _resolve(session, fence, intent, settings, *, content=False):
    from backend.app.native_candidate_source import require_candidate_source
    from backend.app.numeric_process_services import read_numeric_output

    require_neutral_transaction(session, fence, resource_scope="factor_objects")
    if not native_capability(session):
        raise error("native_publication_schema_unsupported")
    if any(
        getattr(intent.admission, key) != getattr(fence, key)
        for key in ("owner_id", "run_id", "attempt_id", "generation")
    ):
        raise error("native_publication_scope")
    # Actual persisted cutoff/evaluation-start/source lineage, not same-owner code.
    from backend.app.factor_analysis_dispatch import configured_analysis_run
    from backend.app.native_candidate_source import require_configured_candidate_source

    source_resolver = (
        require_configured_candidate_source
        if configured_analysis_run(session, fence)
        else require_candidate_source
    )
    cutoff, prefix = source_resolver(
        session,
        fence,
        cutoff_id=intent.admission.logical_operation_id,
        source_id=intent.source_id,
        expected_factor_version_id=intent.factor_version_id,
        expected_code_object_id=intent.source_object_id,
        expected_sha256=intent.source_sha256,
        expected_start_id=intent.generation_start_id,
        expected_prefix_sha256=intent.prefix_sha256,
        expected_snapshot_input_sha256=intent.snapshot_input_sha256,
        expected_cutoff=intent.cutoff,
        settings=settings,
    )
    if (
        cutoff.candidate_admission_id,
        prefix.candidate_image,
        prefix.candidate_profile,
    ) != (intent.admission.id, intent.admission.image_id, intent.admission.profile):
        raise error("native_publication_cutoff_drift")
    if intent.output.object.size_bytes > prefix.max_output_bytes:
        raise error("native_publication_capacity")
    # Re-read protected output even for pre-admission/finalization; detached
    # metadata receipts never substitute for actual bounded immutable bytes.
    receipt, raw = read_numeric_output(
        session, fence, intent.admission.id, storage_root=settings.storage_root
    )
    if (
        receipt.admission,
        receipt.inputs.source_object_id,
        receipt.inputs.source_sha256,
        receipt.inputs.prefix_sha256,
        receipt.inputs.cutoff,
        receipt.stop_receipt_id,
        receipt.stop_receipt_hash,
        receipt.extraction_receipt_id,
        receipt.extraction_receipt_hash,
        receipt.basename,
        receipt.sha256,
        receipt.size_bytes,
    ) != (
        intent.admission,
        intent.source_object_id,
        intent.source_sha256,
        intent.prefix_sha256,
        intent.cutoff,
        intent.stop_receipt_id,
        intent.stop_receipt_sha256,
        intent.extraction_receipt_id,
        intent.extraction_receipt_sha256,
        "result.h5",
        intent.output.object.sha256,
        intent.output.object.size_bytes,
    ):
        raise error("native_publication_receipt_drift")
    return raw if content else None


def pending_native_usage(session, owner_id):
    rows = session.scalars(
        select(NativeHDFPublication)
        .outerjoin(
            NativeHDFOutcome, NativeHDFOutcome.publication_id == NativeHDFPublication.id
        )
        .where(NativeHDFOutcome.publication_id.is_(None))
        .limit(10001)
    ).all()
    if len(rows) > 10000:
        raise error("native_publication_capacity")
    return (
        sum(row.peak_bytes for row in rows),
        sum(row.peak_bytes for row in rows if row.owner_id == owner_id),
        sum(row.peak_entries for row in rows),
    )


def verify_native_journals(session, entries, settings):
    # Every native journal must have committed pre-admission. Unknown complete
    # journals are retained recovery barriers, never inferred successful writes.
    journals = {key: size for key, size in entries if "/native-journals/" in key}
    if journals:
        rows = session.scalars(
            select(NativeHDFPublication)
            .where(NativeHDFPublication.journal_key.in_(journals))
            .limit(10001)
        ).all()
        if len(rows) != len(journals):
            raise error("native_publication_recovery_required")
        for row in rows:
            intent = decode_intent(row)
            raw = canonical_bytes(intent)
            if journals[row.journal_key] != len(raw):
                raise error("native_publication_recovery_required")
            read_exact(
                settings.storage_root,
                row.journal_key,
                digest(intent),
                len(raw),
                maximum=65536,
            )


def _capacity(session, fence, settings, factor_fd, *, extra=(0, 0)):
    from backend.app.factor_services import physical_inventory
    from runtimes.factor_parser.supervisor import assert_quiescent

    assert_quiescent(settings.storage_root)
    entries = []
    total, owned, count = physical_inventory(
        factor_fd,
        fence.owner_id,
        time.monotonic() + 10,
        settings.factor_max_files,
        allow_staging=True,
        native=True,
        native_entries=entries,
        storage_root=settings.storage_root,
        include_dataset_inventory=dataset_inventory_enabled(session),
        include_input_inventory=input_inventory_enabled(session),
    )
    verify_native_journals(session, entries, settings)
    pending_total, pending_owned, pending_count = pending_native_usage(
        session, fence.owner_id
    )
    # Source publication uses this same quota and may have a durable pending peak.
    from backend.app.rdagent_source_capacity import pending_source_usage

    source_total, source_owned, source_count = pending_source_usage(
        session, fence.owner_id
    )
    from backend.app.execution_snapshot_capacity import (
        pending_snapshot_usage_for_schema,
    )

    snap_total, snap_owned, snap_count = pending_snapshot_usage_for_schema(
        session, fence.owner_id
    )
    pending_total += snap_total
    pending_owned += snap_owned
    pending_count += snap_count
    if (
        total + pending_total + source_total + extra[0]
        > settings.factor_installation_max_bytes
        or owned + pending_owned + source_owned + extra[0]
        > settings.factor_owner_max_bytes
        or count + pending_count + source_count + extra[1] > settings.factor_max_files
    ):
        raise error("native_publication_capacity")


def _publication(session, fence, publication_id):
    row = session.get(NativeHDFPublication, publication_id)
    if row is None or any(
        getattr(row, key) != getattr(fence, key)
        for key in ("owner_id", "run_id", "attempt_id", "generation")
    ):
        raise error("native_publication_missing")
    return row


def _reserve_native(session, fence, intent, settings, *, factor_fd):
    """Flush immutable pre-admission; caller must commit before external writes."""
    if require_native_storage_guard(settings.storage_root) != factor_fd:
        raise error("native_storage_fence_differs")
    from backend.app.research_broker_services import require_reservation

    intent = NativePublicationIntent.model_validate(intent)
    _resolve(session, fence, intent, settings)
    previous = session.get(NativeHDFPublication, intent.journal_id)
    if previous is not None:
        if decode_intent(previous) != intent:
            raise error("native_publication_request_drift")
        return previous.id
    peak, entries = footprint(intent)
    reservation = require_reservation(
        session,
        fence,
        intent.reservation_id,
        purpose="object_storage",
        minimum=BudgetVector(storage_bytes=peak),
    )
    if reservation.amounts != BudgetVector(
        storage_bytes=peak
    ).model_dump() or reservation.logical_key != "native:" + str(intent.journal_id):
        raise error("native_publication_reservation_drift")
    _capacity(session, fence, settings, factor_fd, extra=(peak, entries))
    obj = intent.output.object
    session.add(
        NativeHDFPublication(
            id=intent.journal_id,
            **{
                key: getattr(fence, key)
                for key in ("owner_id", "run_id", "attempt_id", "generation")
            },
            admission_id=intent.admission.id,
            cutoff_id=intent.admission.logical_operation_id,
            reservation_id=intent.reservation_id,
            source_id=intent.source_id,
            source_object_id=intent.source_object_id,
            source_sha256=intent.source_sha256,
            stop_receipt_id=intent.stop_receipt_id,
            extraction_receipt_id=intent.extraction_receipt_id,
            intent=intent.model_dump(mode="json"),
            intent_sha256=digest(intent),
            object_id=obj.object_id,
            sha256=obj.sha256,
            size_bytes=obj.size_bytes,
            storage_key=intent.output.storage_key,
            journal_key=intent.journal_key,
            peak_bytes=peak,
            peak_entries=entries,
        )
    )
    session.flush()  # Immediate shared reservation-consumer trigger must win here.
    return intent.journal_id


def _finalize(session, fence, publication_id, settings):
    require_native_storage_guard(settings.storage_root)
    from backend.app.research_broker_models import ResearchBudgetReservation
    from backend.app.research_broker_services import settle_budget

    row = _publication(session, fence, publication_id)
    intent = decode_intent(row)
    _resolve(session, fence, intent, settings)
    verify_candidate_bytes(settings.storage_root, intent)
    physical_hash = digest(
        {
            "storage_key": row.storage_key,
            "sha256": row.sha256,
            "size_bytes": row.size_bytes,
            "journal_key": row.journal_key,
            "journal_sha256": row.intent_sha256,
        }
    )
    existing = session.get(NativeHDFObject, row.object_id)
    if existing is None:
        session.add(
            NativeHDFObject(
                id=row.object_id,
                owner_id=row.owner_id,
                sha256=row.sha256,
                size_bytes=row.size_bytes,
                storage_key=row.storage_key,
            )
        )
    elif (
        existing.owner_id,
        existing.object_kind,
        existing.sha256,
        existing.size_bytes,
        existing.storage_key,
        existing.media_type,
    ) != (
        row.owner_id,
        "native_hdf",
        row.sha256,
        row.size_bytes,
        row.storage_key,
        "application/x-hdf5",
    ):
        raise error("native_publication_object_drift")
    outcome = session.get(NativeHDFOutcome, row.id)
    if outcome is None:
        session.add(
            NativeHDFOutcome(
                publication_id=row.id,
                owner_id=row.owner_id,
                object_id=row.object_id,
                sha256=row.sha256,
                size_bytes=row.size_bytes,
                storage_key=row.storage_key,
                journal_sha256=row.intent_sha256,
                physical_receipt_sha256=physical_hash,
            )
        )
    elif (
        outcome.object_id,
        outcome.journal_sha256,
        outcome.physical_receipt_sha256,
    ) != (row.object_id, row.intent_sha256, physical_hash):
        raise error("native_publication_outcome_drift")
    session.flush()
    reservation = session.get(ResearchBudgetReservation, row.reservation_id)
    if reservation is None:
        raise error()
    settle_budget(
        session,
        fence,
        row.reservation_id,
        charge=BudgetVector.model_validate(reservation.amounts),
        outcome="conservative_unknown",
    )
    return row.object_id


def publish_native(factory, fence, publication_id, settings):
    """Only committed pre-admission; fresh actual receipt/source reads on both sides."""
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        native_storage_guard(settings.storage_root) as factor_fd,
    ):
        with neutral_transaction(
            factory, fence, resource_scope="factor_objects"
        ) as session:
            row = _publication(session, fence, publication_id)
            intent = decode_intent(row)
            if session.get(NativeHDFOutcome, row.id) is not None:
                return _finalize(
                    session, fence, row.id, settings
                )  # verified read-only physical replay
            from backend.app.research_broker_services import require_reservation

            require_reservation(
                session,
                fence,
                row.reservation_id,
                purpose="object_storage",
                minimum=BudgetVector(storage_bytes=row.peak_bytes),
            )
            _capacity(session, fence, settings, factor_fd)
            raw = _resolve(session, fence, intent, settings, content=True)
        publish_candidate_bytes(settings.storage_root, intent, raw)
        with neutral_transaction(
            factory, fence, resource_scope="factor_objects"
        ) as session:
            result = _finalize(session, fence, publication_id, settings)
        return result


def read_completed_native(session, fence, publication_id, settings):
    """Normalizer input: completed DB outcome + current source/stop + actual bytes."""
    require_neutral_transaction(session, fence, resource_scope="factor_objects")
    require_native_storage_guard(settings.storage_root)
    row = _publication(session, fence, publication_id)
    if session.get(NativeHDFOutcome, row.id) is None:
        raise error("native_publication_uncommitted")
    # Do not settle, create an outcome or turn a pending journal into success.
    intent = decode_intent(row)
    _resolve(session, fence, intent, settings)
    outcome = session.get(NativeHDFOutcome, row.id)
    obj = session.get(NativeHDFObject, row.object_id)
    expected_physical = digest(
        {
            "storage_key": row.storage_key,
            "sha256": row.sha256,
            "size_bytes": row.size_bytes,
            "journal_key": row.journal_key,
            "journal_sha256": row.intent_sha256,
        }
    )
    if (
        obj is None
        or (
            obj.owner_id,
            obj.object_kind,
            obj.media_type,
            obj.sha256,
            obj.size_bytes,
            obj.storage_key,
        )
        != (
            row.owner_id,
            "native_hdf",
            "application/x-hdf5",
            row.sha256,
            row.size_bytes,
            row.storage_key,
        )
        or (
            outcome.owner_id,
            outcome.object_id,
            outcome.sha256,
            outcome.size_bytes,
            outcome.storage_key,
            outcome.journal_sha256,
            outcome.physical_receipt_sha256,
        )
        != (
            row.owner_id,
            row.object_id,
            row.sha256,
            row.size_bytes,
            row.storage_key,
            row.intent_sha256,
            expected_physical,
        )
    ):
        raise error("native_publication_outcome_drift")
    from backend.app.research_broker_models import (
        ResearchBudgetReservation,
        ResearchBudgetSettlement,
    )

    reservation = session.get(ResearchBudgetReservation, row.reservation_id)
    settlement = session.scalar(
        select(ResearchBudgetSettlement).where(
            ResearchBudgetSettlement.reservation_id == row.reservation_id
        )
    )
    if (
        reservation is None
        or settlement is None
        or reservation.state != "settled"
        or (settlement.charge, settlement.outcome)
        != (reservation.amounts, "conservative_unknown")
    ):
        raise error("native_publication_charge_drift")
    return intent.output, verify_candidate_bytes(settings.storage_root, intent)


def admit_native(factory, fence, intent, settings):
    """Commit immutable reservation under retained lifecycle/namespace authority."""
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        native_storage_guard(settings.storage_root) as factor_fd,
    ):
        with neutral_transaction(
            factory, fence, resource_scope="factor_objects"
        ) as session:
            result = _reserve_native(
                session, fence, intent, settings, factor_fd=factor_fd
            )
        return result


def native_capability(session):
    """Exact revision dispatch, never table-existence or revision-order inference."""
    from sqlalchemy import text

    from backend.app.schema_capabilities import capabilities_for

    revisions = session.scalars(text("SELECT version_num FROM alembic_version")).all()
    if len(revisions) != 1:
        raise error("native_publication_schema_unsupported")
    return getattr(capabilities_for(revisions[0]), "native_closure_schema", None) in {
        "mineagent.native-hdf-restore-closure.v1",
        "mineagent.native-hdf-restore-closure.v2",
        "mineagent.native-hdf-restore-closure.v3",
    }


def publish_completed_candidate(factory, fence, admission_id, settings):
    """Derive and publish actual completed candidate; no caller-supplied evidence DTO.

    Idempotency is the immutable candidate admission, not a newly generated UUID.
    Returns the publication ID for a later fresh normalizer claim, not a grant.
    """
    from uuid import uuid4

    from backend.app.numeric_process_services import read_numeric_output
    from backend.app.rdagent_models import FactorCutoffExecution, FactorEvaluationStart
    from backend.app.rdagent_services import _scope_row
    from backend.app.research_broker_services import reserve_budget
    from packages.factors.v2_evidence import EvidenceObject
    from packages.factors.v2_storage_contracts import storage_identity
    from packages.rdagent_adapter.prefix import PrefixManifest

    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        native_storage_guard(settings.storage_root) as factor_fd,
        neutral_transaction(factory, fence, resource_scope="factor_objects") as session,
    ):
        if not native_capability(session):
            raise error("native_publication_schema_unsupported")
        previous = session.scalar(
            select(NativeHDFPublication).where(
                NativeHDFPublication.admission_id == admission_id
            )
        )
        if previous is not None:
            publication_id = _publication(session, fence, previous.id).id
        else:
            receipt, _ = read_numeric_output(
                session, fence, admission_id, storage_root=settings.storage_root
            )
            cutoff = _scope_row(
                session,
                FactorCutoffExecution,
                receipt.admission.logical_operation_id,
                fence,
                lock=True,
            )
            evaluation = _scope_row(
                session,
                FactorEvaluationStart,
                cutoff.evaluation_id,
                fence,
                lock=True,
            )
            prefix = PrefixManifest.model_validate(cutoff.projection)
            publication_id = uuid4()
            reservation = reserve_budget(
                session,
                fence,
                purpose="object_storage",
                logical_key="native:" + str(publication_id),
                amounts=BudgetVector(storage_bytes=2 * receipt.size_bytes + 131072),
            )
            output = storage_identity(
                fence.owner_id,
                EvidenceObject(
                    role="native_hdf",
                    object_id=uuid4(),
                    object_kind="native_hdf",
                    media_type="application/x-hdf5",
                    sha256=receipt.sha256,
                    size_bytes=receipt.size_bytes,
                ),
            )
            intent = NativePublicationIntent(
                schema_version="mineagent.candidate-native-intent.v2",
                journal_id=publication_id,
                reservation_id=reservation.id,
                admission=receipt.admission,
                source_id=cutoff.evaluated_source_id,
                generation_start_id=cutoff.start_id,
                factor_version_id=cutoff.factor_version_id,
                source_object_id=receipt.inputs.source_object_id,
                source_sha256=receipt.inputs.source_sha256,
                prefix_sha256=cutoff.projection_sha256,
                snapshot_input_sha256=evaluation.input_sha256,
                cutoff=prefix.host_cutoff,
                stop_receipt_id=receipt.stop_receipt_id,
                stop_receipt_sha256=receipt.stop_receipt_hash,
                extraction_receipt_id=receipt.extraction_receipt_id,
                extraction_receipt_sha256=receipt.extraction_receipt_hash,
                output=output,
                journal_key=f"factors/{fence.owner_id}/native-journals/{publication_id}.json",
            )
            _reserve_native(session, fence, intent, settings, factor_fd=factor_fd)
    publish_native(factory, fence, publication_id, settings)
    return publication_id
