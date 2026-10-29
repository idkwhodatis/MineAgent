"""Fresh owned/released snapshot + retained market preflight, never launch authority.

This function returns prospective data only. A027 persisted issuer must repeat
resolution inside its current fence before admitting/claiming any physical unit.
No acceptance initializer, random execution IDs, label reader or native parser.
"""

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from backend.app.core.maintenance import shared_lifecycle
from backend.app.engine_result_release import disclose
from backend.app.execution_snapshot_models import ExecutionSnapshotPublication
from backend.app.execution_snapshot_services import decode_publication
from backend.app.factor_analysis_models import FactorAnalysisPlan
from backend.app.factor_native_io import native_storage_guard
from backend.app.models import DatasetVersion
from backend.app.oe_structured_source_qualification import require_source_family
from backend.app.oe_structured_submission_schemas import StructuredSearchRequest
from backend.app.rdagent_configured_source import resolve_dataset_preflight
from backend.app.services import DomainError
from backend.app.structured_factor_io import read_structured_artifact
from packages.factors.contracts import digest, rows_digest
from packages.openevolve_structured.candidate import _raw_fields
from packages.rdagent_adapter.configured_source import ConfiguredSourcePolicy
from packages.structured_factor_strategy.market import ARTIFACTS, stage_market


def fail(code="structured_search_input_unavailable"):
    raise DomainError(
        code,
        "Exact owned released factor snapshot and supported retained market required",
        409,
    )


@dataclass(frozen=True)
class StructuredPreflightData:
    """Ephemeral bytes/data, not a reusable grant or a current-fence assertion."""

    owner_id: UUID
    request: StructuredSearchRequest
    manifest: object
    rows: tuple
    source_policy: ConfiguredSourcePolicy
    source: object
    market: object
    release_receipt_ids: tuple[UUID, ...]

    def envelope(self):
        program = self.request.seed_parameters.program(self.manifest.input.window)
        return {
            "schema_version": "mineagent.oe-structured-fixed-input.v1",
            "owner_id": str(self.owner_id),
            "request_sha256": self.request.sha256,
            "snapshot_id": str(self.manifest.snapshot_id),
            "snapshot_manifest_sha256": digest(self.manifest),
            "snapshot_input_sha256": self.manifest.input_sha256,
            "snapshot_output_sha256": self.manifest.output_rows_sha256,
            "factor_version_id": str(self.manifest.input.version.factor_version_id),
            "dataset_version_id": str(self.source.dataset.dataset_version_id),
            "dataset_sha256": self.source.dataset.content_sha256,
            "source_policy_sha256": digest(self.source_policy),
            "market_sha256": hashlib.sha256(self.market.content).hexdigest(),
            "market_size_bytes": len(self.market.content),
            "program_sha256": digest(program),
            "objective_sha256": digest(self.request.objective),
            "release_receipt_ids": sorted(str(x) for x in self.release_receipt_ids),
            "qualification": "prospective_owned_data_not_execution_authority",
        }


def resolve_preflight_data(factory, owner_id, request, settings):
    if type(owner_id) is not UUID or type(request) is not StructuredSearchRequest:
        fail()
    request = StructuredSearchRequest.model_validate(_raw_fields(request))
    # Lookup scopes before expensive historical validation; no cross-owner detail.
    with factory() as session:
        publication = session.scalar(
            select(ExecutionSnapshotPublication).filter_by(
                id=request.snapshot_id, owner_id=owner_id
            )
        )
        if publication is None:
            raise DomainError("not_found", "Factor snapshot not found", 404)
        require_source_family(session, owner_id, publication.run_id)
        source_run_id = publication.run_id
    historical, receipts = disclose(
        factory,
        settings,
        owner_id=owner_id,
        run_id=source_run_id,
        resource_id=request.snapshot_id,
        kind="execution_snapshot",
        create_release=False,
    )
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        native_storage_guard(settings.storage_root),
        factory.begin() as session,
    ):
        return _resolve_retained_data(
            session, owner_id, request, settings, historical, receipts, source_run_id
        )


def _resolve_retained_data(
    session,
    owner_id,
    request,
    settings,
    historical,
    receipts,
    source_run_id,
    *,
    workflow_predecessor=None,
):
    publication = session.scalar(
        select(ExecutionSnapshotPublication).filter_by(
            id=request.snapshot_id, owner_id=owner_id, run_id=source_run_id
        )
    )
    if publication is None:
        fail()
    if workflow_predecessor is None:
        require_source_family(session, owner_id, source_run_id)
    else:
        from backend.app.oe_workflow_source import require_workflow_source

        require_workflow_source(
            session,
            owner_id,
            source_run_id,
            request.snapshot_id,
            workflow_predecessor,
            settings,
        )
    manifest = decode_publication(publication).manifest
    if (
        historical.snapshot_id,
        historical.factor_version_id,
        historical.manifest_sha256,
        historical.input_sha256,
        historical.output_sha256,
    ) != (
        manifest.snapshot_id,
        manifest.input.version.factor_version_id,
        digest(manifest),
        manifest.input_sha256,
        manifest.output_rows_sha256,
    ) or rows_digest(historical.rows) != manifest.output_rows_sha256:
        fail()
    program = request.seed_parameters.program(manifest.input.window)
    window = request.objective.window
    if (
        not window.start
        <= program.signal_window.start
        < program.signal_window.end
        < window.end
    ):
        fail("structured_search_window_unsupported")
    plan = session.scalar(
        select(FactorAnalysisPlan).filter_by(owner_id=owner_id, run_id=source_run_id)
    )
    if plan is None:
        fail()
    policy = ConfiguredSourcePolicy.model_validate(plan.source_policy)
    if digest(policy) != plan.source_policy_sha256:
        fail()
    source = resolve_dataset_preflight(
        session,
        owner_id=owner_id,
        dataset_id=manifest.input.dataset.dataset_version_id,
        policy=policy,
        input_names=tuple(x.name for x in manifest.input.definition.input_fields),
        settings=settings,
    )
    if (
        source.dataset,
        source.universe,
        source.calendar.sha256,
        source.calendar.timezone,
    ) != (
        manifest.input.dataset,
        manifest.input.calculation_universe,
        manifest.input.calendar_sha256,
        manifest.input.market_timezone,
    ):
        fail()
    dataset = session.get(DatasetVersion, source.dataset.dataset_version_id)
    if (
        dataset is None
        or dataset.owner_id != owner_id
        or dataset.content_sha256 != source.dataset.content_sha256
        or dataset.storage_key != f"datasets/{owner_id}/{dataset.id}"
        or dataset.market != "US"
    ):
        fail()
    raw = read_structured_artifact(
        settings.storage_root,
        dataset.storage_key,
        "manifest.json",
        dataset.content_sha256,
        2 * 1024**2,
    )
    metadata = json.loads(raw)
    if metadata != dataset.manifest:
        fail()
    artifacts = {}
    declared = metadata.get("schema_version") == 3
    total = len(raw)
    try:
        if declared:
            from backend.app.declared_upload_io import read_dataset_bytes
            from backend.app.declared_upload_services import (
                require_revision,
                verify_basket_relations,
            )

            require_revision(session)
            verify_basket_relations(session, owner_id, dataset)
            raw, artifacts = read_dataset_bytes(settings.storage_root, dataset)
        else:
            for name in sorted(ARTIFACTS):
                artifacts[name] = read_structured_artifact(
                    settings.storage_root,
                    dataset.storage_key,
                    name,
                    metadata["artifact_files"][name],
                    32 * 1024**2,
                )
                total += len(artifacts[name])
                if total > 64 * 1024**2:
                    fail()
        market = stage_market(
            raw,
            artifacts,
            dataset_sha256=dataset.content_sha256,
            window=window,
            declared_upload=declared,
            dataset_id=dataset.id,
            owner_id=owner_id,
        )
    except (KeyError, ValueError, FileNotFoundError):
        fail("structured_search_market_unsupported")
    sessions = {row["session_date"] for row in market.sessions}
    if program.signal_window.start.isoformat() not in sessions or not any(
        day >= program.signal_window.end.isoformat() for day in sessions
    ):
        fail("structured_search_window_unsupported")
    # Exact snapshot grid/listing identity, never ticker-based inference.
    if {str(x.listing_id) for x in market.listings} != set(source.universe.listing_ids):
        fail()
    return StructuredPreflightData(
        owner_id,
        request,
        manifest,
        historical.rows,
        policy,
        source,
        market,
        receipts,
    )
