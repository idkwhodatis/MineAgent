"""Owner-scoped immutable historical snapshot reads, never live execution grants.

Relocation preserves old execution identity as evidence. This path rechecks exact
DB relationships and current retained bytes under maintenance/storage guards,
without replacing the live launcher's original-inode or current-fence checks.
"""

import os
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, text

from backend.app.core.maintenance import shared_lifecycle
from backend.app.execution_snapshot_io import verify_snapshot_bytes
from backend.app.execution_snapshot_models import (
    ExecutionSnapshotOutcome,
    ExecutionSnapshotPublication,
)
from backend.app.execution_snapshot_services import decode_publication
from backend.app.factor_models import FactorObject, FactorVersion
from backend.app.factor_native_io import (
    native_storage_guard,
    read_exact,
    verify_candidate_bytes,
)
from backend.app.factor_native_models import NativeHDFPublication
from backend.app.factor_native_services import decode_intent
from backend.app.models import ResearchRun
from backend.app.numeric_historical_io import read_historical_numeric_bytes
from backend.app.rdagent_models import FactorCutoffExecution
from backend.app.rdagent_snapshot_config import provider_config_in_session
from backend.app.research_broker_models import (
    ResearchBudgetReservation,
    ResearchBudgetSettlement,
)
from backend.app.research_process_models import (
    ResearchProcessAdmission,
    ResearchProcessReceipt,
    ResearchProcessState,
)
from backend.app.services import DomainError
from packages.factors.contracts import (
    canonical_bytes,
    digest,
    rows_digest,
    validate_rows,
)
from packages.factors.native_normalizer_binding import primitive_report_context
from packages.rdagent_adapter.assembly import assemble_cutoff_rows
from packages.rdagent_adapter.prefix import PrefixManifest
from packages.research_runtime.process_persistence_codec import decode_row
from runtimes.factor_normalizer.primitive_report import validate_bound_report

SCOPE = ("owner_id", "run_id", "attempt_id", "generation")


def fail():
    raise DomainError(
        "historical_factor_unavailable",
        "Complete owned historical result is unavailable",
        409,
    )


def same(a, b):
    if any(getattr(a, k) != getattr(b, k) for k in SCOPE):
        fail()


@contextmanager
def historical_session(factory, owner_id, settings):
    if type(owner_id) is not UUID:
        fail()
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        native_storage_guard(settings.storage_root),
    ):
        if os.path.lexists(os.path.join(settings.storage_root, ".restore-incomplete")):
            fail()
        with factory.begin() as session:
            revisions = session.scalars(
                text("SELECT version_num FROM alembic_version")
            ).all()
            if revisions not in (
                ["021_execution_snapshots"],
                ["022_factor_analysis"],
                ["023_rd_submissions"],
                ["024_openevolve_initial"],
                ["025_structured_factor_strategy"],
                ["026_rd_seeded_submissions"],
            ):
                fail()
            yield session


def terminal_owner(session, owner_id, run_id):
    row = session.scalar(select(ResearchRun).filter_by(id=run_id, owner_id=owner_id))
    if (
        row is None
        or row.engine != "rdagent"
        or row.purpose != "factor_research"
        or row.status not in {"succeeded", "failed", "cancelled", "interrupted"}
    ):
        fail()
    return row


def process_history(session, publication, admission_id):
    admission = session.get(ResearchProcessAdmission, admission_id)
    if admission is None:
        fail()
    same(admission, publication)
    state = session.get(ResearchProcessState, admission_id)
    if state is None or state.status != "stopped":
        fail()
    same(state, publication)
    rows = session.scalars(
        select(ResearchProcessReceipt).filter_by(admission_id=admission_id)
    ).all()
    receipts = {r.kind: r for r in rows}
    if len(rows) != 4 or set(receipts) != {
        "launch_intent",
        "launch_identity",
        "stop",
        "extraction",
    }:
        fail()
    for row in rows:
        same(row, publication)
        if digest(row.payload) != row.payload_sha256:
            fail()
    fields = (
        "id",
        "schema_version",
        "owner_id",
        "run_id",
        "attempt_id",
        "generation",
        "role",
        "logical_operation_id",
        "launch_ordinal",
        "profile",
        "input_sha256",
        "reservation_id",
        "image_id",
        "ipc_policy",
        "ticket_sha256",
    )
    declaration = decode_row({key: getattr(admission, key) for key in fields})
    if receipts["launch_intent"].payload["admission"] != declaration.model_dump(
        mode="json"
    ):
        fail()
    reservation = session.get(ResearchBudgetReservation, admission.reservation_id)
    settled = session.scalar(
        select(ResearchBudgetSettlement).filter_by(
            reservation_id=admission.reservation_id
        )
    )
    if (
        reservation is None
        or settled is None
        or reservation.state != "settled"
        or settled.outcome != "conservative_unknown"
        or settled.charge != reservation.amounts
    ):
        fail()
    same(reservation, publication)
    same(settled, publication)
    return admission, receipts


def physical_process(root, receipts):
    return read_historical_numeric_bytes(
        root,
        intent=receipts["launch_intent"].payload,
        identity=receipts["launch_identity"].payload,
        stop=receipts["stop"].payload,
        extraction=receipts["extraction"].payload,
    )


@dataclass(frozen=True)
class HistoricalExecutionSnapshot:
    snapshot_id: UUID
    source_id: UUID
    evaluation_id: UUID
    factor_version_id: UUID
    input_sha256: str
    output_sha256: str
    manifest_sha256: str
    rows: tuple
    qualification: str = "owned_historical_integrity_not_live_execution_authority"


def read_snapshot_in_session(session, owner_id, snapshot_id, settings):
    publication = session.scalar(
        select(ExecutionSnapshotPublication).filter_by(
            id=snapshot_id, owner_id=owner_id
        )
    )
    if publication is None:
        fail()
    terminal_owner(session, owner_id, publication.run_id)
    outcome = session.get(ExecutionSnapshotOutcome, snapshot_id)
    if outcome is None:
        fail()
    same(outcome, publication)
    # Read-only installed predicate validates complete persisted source, cutoff,
    # native, process receipt, reservation and immutable physical inventory graph.
    session.execute(
        text("SELECT mineagent_execution_snapshot_check(:id)"), {"id": snapshot_id}
    )
    journal = decode_publication(publication)
    manifest = journal.manifest
    verify_snapshot_bytes(settings.storage_root, journal)
    check_configured_history(session, publication, settings)
    version = session.get(FactorVersion, manifest.input.version.factor_version_id)
    if version is None or version.owner_id != owner_id:
        fail()
    code = session.get(FactorObject, version.code_object_id)
    if (
        code is None
        or code.owner_id != owner_id
        or code.sha256 != manifest.input.version.code_sha256
    ):
        fail()
    read_exact(
        settings.storage_root,
        code.storage_key,
        code.sha256,
        code.size_bytes,
        maximum=131072,
    )
    combined = []
    for part in manifest.partitions:
        native = session.get(NativeHDFPublication, part.native_publication_id)
        cutoff = session.get(FactorCutoffExecution, part.cutoff_id)
        if native is None or cutoff is None:
            fail()
        same(native, publication)
        same(cutoff, publication)
        intent = decode_intent(native)
        verify_candidate_bytes(settings.storage_root, intent)
        prefix = PrefixManifest.model_validate(cutoff.projection)
        _, candidate_receipts = process_history(
            session, publication, part.candidate_admission_id
        )
        candidate = physical_process(settings.storage_root, candidate_receipts)
        hdf = read_exact(
            settings.storage_root, native.storage_key, native.sha256, native.size_bytes
        )
        if (
            candidate.output_bytes != hdf
            or candidate.sha256 != intent.output.object.sha256
        ):
            fail()
        _, receipts = process_history(
            session, publication, part.normalizer_admission_id
        )
        normalizer = physical_process(settings.storage_root, receipts)
        if (
            receipts["stop"].id != part.stop_receipt_id
            or receipts["stop"].payload_sha256 != part.stop_receipt_sha256
            or receipts["extraction"].id != part.extraction_receipt_id
            or receipts["extraction"].payload_sha256 != part.extraction_receipt_sha256
            or normalizer.sha256 != part.values.sha256
            or len(normalizer.output_bytes) != part.values.size_bytes
            or normalizer.report_sha256 != part.primitive_report_sha256
            or normalizer.report_bytes is None
            or len(normalizer.report_bytes) != part.primitive_report_size_bytes
        ):
            fail()
        raw_manifest, listings = primitive_report_context(prefix, intent.output)
        primitives = validate_bound_report(
            normalizer.report_bytes,
            parquet_sha256=normalizer.sha256,
            parquet_size_bytes=len(normalizer.output_bytes),
            manifest=raw_manifest,
            listings=listings,
        )
        rows, assembly = assemble_cutoff_rows(
            manifest.input,
            prefix,
            primitives,
            hdf_sha256=native.sha256,
            normalized_values_sha256=normalizer.sha256,
        )
        if (
            digest(assembly) != part.assembly_sha256
            or assembly.output_rows_sha256 != part.output_rows_sha256
            or assembly.row_count != part.row_count
            or assembly.valid_count != part.valid_count
        ):
            fail()
        combined.extend(rows)
    rows = tuple(combined)
    validate_rows(manifest.input, rows)
    raw = canonical_bytes(
        {
            "schema_version": "mineagent.execution-feature-rows.v1",
            "input_sha256": manifest.input_sha256,
            "rows": [r.model_dump(mode="json") for r in rows],
        }
    )
    if (
        rows_digest(rows) != manifest.output_rows_sha256
        or read_exact(
            settings.storage_root,
            manifest.assembled_rows.storage_key,
            manifest.assembled_rows.sha256,
            manifest.assembled_rows.size_bytes,
        )
        != raw
    ):
        fail()
    return HistoricalExecutionSnapshot(
        snapshot_id,
        manifest.source_id,
        manifest.evaluation_id,
        manifest.input.version.factor_version_id,
        manifest.input_sha256,
        manifest.output_rows_sha256,
        publication.manifest_sha256,
        rows,
    )


def read_historical_execution_snapshot(factory, owner_id, snapshot_id, settings):
    with historical_session(factory, owner_id, settings) as session:
        return read_snapshot_in_session(session, owner_id, snapshot_id, settings)


def check_configured_history(session, publication, settings):
    """022 data provenance only; no resurrection of a terminal transaction fence."""
    revisions = session.scalars(text("SELECT version_num FROM alembic_version")).all()
    if revisions not in (
        ["022_factor_analysis"],
        ["023_rd_submissions"],
        ["024_openevolve_initial"],
        ["025_structured_factor_strategy"],
        ["026_rd_seeded_submissions"],
    ):
        return
    from backend.app.factor_analysis_history import retained_reader, row_document
    from backend.app.factor_analysis_models import FactorAnalysisPlan
    from backend.app.models import DatasetVersion
    from backend.app.rdagent_configured_backup import validate_configured_input_closure
    from backend.app.rdagent_configured_input_models import FactorConfiguredInputWitness
    from backend.app.rdagent_models import (
        FactorEvaluationStart,
        FactorGenerationSource,
        FactorResearchSpec,
    )
    from backend.app.rdagent_source_witness_models import FactorSourceReadWitness

    plan = session.scalar(
        select(FactorAnalysisPlan).filter_by(
            owner_id=publication.owner_id, run_id=publication.run_id
        )
    )
    if plan is None:
        return
    tables = {}
    for model in (
        FactorCutoffExecution,
        FactorEvaluationStart,
        FactorGenerationSource,
        FactorSourceReadWitness,
        FactorResearchSpec,
        FactorAnalysisPlan,
        ResearchProcessAdmission,
        FactorConfiguredInputWitness,
    ):
        rows = session.scalars(
            select(model)
            .filter_by(owner_id=publication.owner_id, run_id=publication.run_id)
            .limit(10001)
        ).all()
        if len(rows) > 10000:
            fail()
        tables[model.__tablename__] = [row_document(r) for r in rows]
    object_ids = {
        UUID(r["source_object_id"]) for r in tables["factor_source_read_witnesses"]
    }
    objects = session.scalars(
        select(FactorObject)
        .where(
            FactorObject.owner_id == publication.owner_id,
            FactorObject.id.in_(object_ids),
        )
        .limit(10001)
    ).all()
    if len(objects) != len(object_ids):
        fail()
    tables["factor_objects"] = [row_document(r) for r in objects]
    dataset = session.get(DatasetVersion, plan.label_source_dataset_id)
    if dataset is None:
        fail()
    check_exposure_history(
        session,
        tables["factor_analysis_plans"],
        [row_document(dataset)],
        tables["factor_evaluation_starts"],
    )
    validate_configured_input_closure(
        tables["factor_configured_input_witnesses"],
        tables=tables,
        datasets=[row_document(dataset)],
        read_bytes=retained_reader(settings.storage_root),
        max_rows=10000,
        provider_config=provider_config_in_session(session),
    )


def check_exposure_history(session, plans, datasets, evaluations):
    from backend.app.factor_analysis_history import row_document
    from backend.app.models import DataUsageExposure
    from backend.app.rdagent_configured_exposure_backup import (
        validate_configured_exposure_links,
    )

    owners = {UUID(p["owner_id"]) for p in plans}
    rows = session.scalars(
        select(DataUsageExposure)
        .where(DataUsageExposure.owner_id.in_(owners))
        .limit(10001)
    ).all()
    if len(rows) > 10000:
        fail()
    validate_configured_exposure_links(
        plans,
        datasets,
        [row_document(r) for r in rows],
        evaluations,
        maximum_rows=10000,
    )
