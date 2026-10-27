"""Fresh process/source assembly for the execution snapshot publisher.

Never accepts a caller manifest, rows, bytes, stop receipt, or detached resolver
result. Imports the explicit composed controller/report boundaries on demand.
"""

import hashlib
from uuid import uuid4

from backend.app.factor_native_io import require_native_storage_guard
from backend.app.research_lifecycle import require_neutral_transaction
from backend.app.services import DomainError
from packages.factors.contracts import (
    canonical_bytes,
    digest,
    rows_digest,
    validate_rows,
)
from packages.factors.execution_snapshot import (
    MAX_CUTOFFS,
    MAX_SNAPSHOT_BYTES,
    MAX_SNAPSHOT_ROWS,
    ExecutionObject,
    ExecutionSnapshotManifest,
    NormalizedPartition,
)
from packages.rdagent_adapter.assembly import assemble_cutoff_rows


def _error():
    return DomainError(
        "execution_snapshot_unverified",
        "Complete execution snapshot could not be verified",
        409,
    )


def resolve_snapshot_materialization(
    session, fence, evaluation_id, settings, *, snapshot_id, existing=None
):
    """Internal current-transaction reconstruction; results never reusable grants."""
    from backend.app.normalized_cutoff_services import read_normalized_cutoff
    from backend.app.rdagent_complete_evaluation import resolve_complete_evaluation
    from backend.app.rdagent_source_resolution import require_source_code

    require_neutral_transaction(session, fence, resource_scope="factor_numeric")
    require_native_storage_guard(settings.storage_root)
    from backend.app.factor_analysis_dispatch import configured_analysis_run
    from backend.app.rdagent_complete_evaluation import (
        resolve_configured_complete_evaluation,
    )

    resolver = (
        resolve_configured_complete_evaluation
        if configured_analysis_run(session, fence)
        else resolve_complete_evaluation
    )
    inventory = resolver(session, fence, evaluation_id, settings)
    if not 1 <= len(inventory.cutoffs) <= MAX_CUTOFFS:
        raise _error()
    snapshot = inventory.snapshot_input
    source = require_source_code(
        session,
        fence,
        source_id=inventory.source_id,
        expected_factor_version_id=inventory.factor_version_id,
        expected_code_object_id=inventory.source_object_id,
        expected_sha256=snapshot.version.code_sha256,
        settings=settings,
    )
    if existing is not None:
        existing = ExecutionSnapshotManifest.model_validate(existing)
        if (
            existing.snapshot_id != snapshot_id
            or existing.evaluation_id != evaluation_id
            or len(existing.partitions) != len(inventory.cutoffs)
        ):
            raise _error()
    physical = {}
    partitions, all_rows = [], []
    total = 0

    def object_for(kind, raw, previous=None):
        nonlocal total
        total += len(raw)
        if total > MAX_SNAPSHOT_BYTES:
            raise _error()
        sha = hashlib.sha256(raw).hexdigest()
        suffix = "parquet" if kind == "normalized_values" else "json"
        obj = ExecutionObject(
            object_id=previous.object_id if previous else uuid4(),
            owner_id=fence.owner_id,
            kind=kind,
            sha256=sha,
            size_bytes=len(raw),
            storage_key=f"factors/{fence.owner_id}/objects/{sha}.{suffix}",
        )
        if previous is not None and obj != previous:
            raise _error()
        old = physical.setdefault(obj.storage_key, raw)
        if old != raw:
            raise _error()
        return obj

    for cutoff in inventory.cutoffs:
        resolved = read_normalized_cutoff(
            session, fence, cutoff.normalizer_admission_id, settings
        )
        if (
            resolved.owner_id,
            resolved.run_id,
            resolved.attempt_id,
            resolved.generation,
            resolved.source_id,
            resolved.evaluation_id,
            resolved.cutoff_id,
            resolved.cutoff_ordinal,
            resolved.factor_version_id,
            resolved.code_object_id,
            resolved.source_sha256,
            resolved.prefix_sha256,
            resolved.snapshot_input_sha256,
            resolved.admission_id,
            resolved.native_publication_id,
        ) != (
            fence.owner_id,
            fence.run_id,
            fence.attempt_id,
            fence.generation,
            inventory.source_id,
            inventory.evaluation_id,
            cutoff.execution_id,
            cutoff.ordinal,
            inventory.factor_version_id,
            inventory.source_object_id,
            snapshot.version.code_sha256,
            cutoff.prefix.sha256,
            snapshot.sha256,
            cutoff.normalizer_admission_id,
            cutoff.native_publication_id,
        ):
            raise _error()
        if resolved.listings != tuple(
            (item.code, item.listing_id) for item in cutoff.prefix.listings
        ):
            raise _error()
        if (
            hashlib.sha256(resolved.parquet_bytes).hexdigest(),
            len(resolved.parquet_bytes),
        ) != (resolved.parquet_sha256, resolved.parquet_size_bytes):
            raise _error()
        # Numeric resolver already binds protected report+Parquet to this exact
        # native claim. Retrieve native hash from that same persisted publication.
        from backend.app.factor_native_models import NativeHDFPublication

        native = session.get(NativeHDFPublication, cutoff.native_publication_id)
        if native is None or native.admission_id != cutoff.candidate_admission_id:
            raise _error()
        rows, assembly = assemble_cutoff_rows(
            snapshot,
            cutoff.prefix,
            resolved.rows,
            hdf_sha256=native.sha256,
            normalized_values_sha256=resolved.parquet_sha256,
        )
        all_rows.extend(rows)
        if len(all_rows) > MAX_SNAPSHOT_ROWS:
            raise _error()
        previous = existing.partitions[cutoff.ordinal] if existing else None
        values = object_for(
            "normalized_values",
            resolved.parquet_bytes,
            previous.values if previous else None,
        )
        partition = NormalizedPartition(
            cutoff_id=cutoff.execution_id,
            ordinal=cutoff.ordinal,
            prefix_sha256=cutoff.prefix.sha256,
            native_publication_id=cutoff.native_publication_id,
            candidate_admission_id=cutoff.candidate_admission_id,
            normalizer_admission_id=cutoff.normalizer_admission_id,
            normalizer_input_sha256=resolved.normalizer_input_sha256,
            stop_receipt_id=resolved.stop_receipt_id,
            stop_receipt_sha256=resolved.stop_receipt_sha256,
            extraction_receipt_id=resolved.extraction_receipt_id,
            extraction_receipt_sha256=resolved.extraction_receipt_sha256,
            primitive_report_sha256=resolved.report_sha256,
            primitive_report_size_bytes=resolved.report_size_bytes,
            values=values,
            assembly_sha256=digest(assembly),
            output_rows_sha256=assembly.output_rows_sha256,
            row_count=assembly.row_count,
            valid_count=assembly.valid_count,
        )
        if previous is not None and previous != partition:
            raise _error()
        partitions.append(partition)
    rows = tuple(all_rows)
    validate_rows(snapshot, rows)
    row_document = canonical_bytes(
        {
            "schema_version": "mineagent.execution-feature-rows.v1",
            "input_sha256": snapshot.sha256,
            "rows": [row.model_dump(mode="json") for row in rows],
        }
    )
    assembled = object_for(
        "assembled_rows", row_document, existing.assembled_rows if existing else None
    )
    manifest = ExecutionSnapshotManifest(
        schema_version="mineagent.factor-execution-snapshot.v1",
        snapshot_id=snapshot_id,
        owner_id=fence.owner_id,
        run_id=fence.run_id,
        attempt_id=fence.attempt_id,
        generation=fence.generation,
        generation_start_id=inventory.generation_start_id,
        source_id=inventory.source_id,
        evaluation_id=inventory.evaluation_id,
        source_read_witness_id=source.read_witness_id,
        source_read_witness_sha256=source.read_witness_sha256,
        input=snapshot,
        input_sha256=snapshot.sha256,
        expected_cutoffs=len(inventory.cutoffs),
        partitions=tuple(partitions),
        assembled_rows=assembled,
        output_rows_sha256=rows_digest(rows),
        row_count=len(rows),
        valid_count=sum(row.value_state == "valid" for row in rows),
    )
    if existing is not None and manifest != existing:
        raise _error()
    return manifest, physical, rows
