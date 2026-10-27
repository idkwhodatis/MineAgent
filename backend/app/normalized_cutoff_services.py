"""Fresh persisted primitive output resolution, never host native parsing.

One resolved cutoff is not a complete evaluation. Callers retain lifecycle and
native storage admission and acquire a fresh factor_numeric transaction each time.
"""

from dataclasses import dataclass
from uuid import UUID

from backend.app.native_normalizer_claim import resolve_completed_native_normalizer
from backend.app.numeric_process_services import _resolve
from backend.app.research_lifecycle import require_neutral_transaction
from packages.factors.contracts import canonical_bytes
from packages.rdagent_adapter.numeric_profile import normalizer_inputs
from packages.rdagent_adapter.prefix import PrefixManifest
from packages.research_runtime.numeric_process_journal import NumericReportExtraction
from packages.research_runtime.process_contracts import ProcessError
from runtimes.factor_normalizer.primitive_report import validate_report
from runtimes.research_supervisor.numeric_journal import (
    read_opaque_output,
    read_primitive_report,
)


@dataclass(frozen=True)
class NormalizedCutoff:
    owner_id: UUID
    run_id: UUID
    attempt_id: UUID
    generation: int
    source_id: UUID
    evaluation_id: UUID
    cutoff_id: UUID
    cutoff_ordinal: int
    factor_version_id: UUID
    code_object_id: UUID
    source_sha256: str
    prefix_sha256: str
    snapshot_input_sha256: str
    admission_id: UUID
    native_publication_id: UUID
    normalizer_input_sha256: str
    extraction_receipt_id: UUID
    extraction_receipt_sha256: str
    stop_receipt_id: UUID
    stop_receipt_sha256: str
    parquet_sha256: str
    parquet_size_bytes: int
    parquet_bytes: bytes
    report_sha256: str
    report_size_bytes: int
    listings: tuple[tuple[int, str], ...]
    rows: tuple[tuple[int, int, float | None, int], ...]


def read_normalized_cutoff(session, fence, normalizer_admission_id, settings):
    require_neutral_transaction(session, fence, resource_scope="factor_numeric")
    from backend.app.normalizer_report_ddl import require_report_capability

    require_report_capability(session)
    from backend.app.factor_native_services import decode_intent
    from backend.app.rdagent_models import FactorCutoffExecution, FactorEvaluationStart
    from backend.app.rdagent_services import _scope_row

    publication, admission, intent, identity, native = (
        resolve_completed_native_normalizer(
            session, fence, normalizer_admission_id, settings
        )
    )
    receipt, extraction = _resolve(
        session, fence, normalizer_admission_id, settings.storage_root
    )
    if (
        not isinstance(extraction, NumericReportExtraction)
        or receipt.admission != admission
    ):
        raise ProcessError("numeric_report_required")
    candidate = decode_intent(publication)
    cutoff = _scope_row(
        session, FactorCutoffExecution, publication.cutoff_id, fence, lock=True
    )
    evaluation = _scope_row(
        session, FactorEvaluationStart, cutoff.evaluation_id, fence, lock=True
    )
    prefix = PrefixManifest.model_validate(cutoff.projection)
    listings = canonical_bytes(
        [item.model_dump(mode="json") for item in prefix.listings]
    )
    files, inputs, _ = normalizer_inputs(
        prefix,
        hdf=native,
        expected_hdf_sha256=identity.object.sha256,
        listings=listings,
        primitive_report=True,
    )
    if tuple((x.destination, x.sha256, x.size_bytes) for x in files) != tuple(
        (x.destination, x.sha256, x.size_bytes) for x in intent.inputs.files
    ):
        raise ProcessError("numeric_report_input_identity")
    parquet = read_opaque_output(settings.storage_root, admission, extraction)
    report = read_primitive_report(settings.storage_root, admission, extraction)
    rows = validate_report(
        report,
        parquet=parquet,
        manifest=inputs["/input/manifest.json"],
        listings=listings,
    )
    return NormalizedCutoff(
        owner_id=fence.owner_id,
        run_id=fence.run_id,
        attempt_id=fence.attempt_id,
        generation=fence.generation,
        source_id=publication.source_id,
        evaluation_id=evaluation.id,
        cutoff_id=cutoff.id,
        cutoff_ordinal=cutoff.cutoff_ordinal,
        factor_version_id=cutoff.factor_version_id,
        code_object_id=candidate.source_object_id,
        source_sha256=candidate.source_sha256,
        prefix_sha256=prefix.sha256,
        snapshot_input_sha256=evaluation.input_sha256,
        admission_id=admission.id,
        native_publication_id=publication.id,
        normalizer_input_sha256=admission.input_sha256,
        extraction_receipt_id=receipt.extraction_receipt_id,
        extraction_receipt_sha256=receipt.extraction_receipt_hash,
        stop_receipt_id=receipt.stop_receipt_id,
        stop_receipt_sha256=receipt.stop_receipt_hash,
        parquet_sha256=extraction.sha256,
        parquet_size_bytes=extraction.size_bytes,
        parquet_bytes=parquet,
        report_sha256=extraction.report_sha256,
        report_size_bytes=extraction.report_size_bytes,
        listings=tuple((item.code, item.listing_id) for item in prefix.listings),
        rows=rows,
    )
