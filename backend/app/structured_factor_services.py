"""Fresh025 immutable strategy/snapshot/market binding; no caller launch grants."""

import hashlib
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select

from backend.app.execution_snapshot_history import read_snapshot_in_session
from backend.app.execution_snapshot_models import ExecutionSnapshotPublication
from backend.app.execution_snapshot_services import decode_publication
from backend.app.factor_native_io import require_native_storage_guard
from backend.app.models import DatasetVersion, DataUsageExposure
from backend.app.rdagent_configured_source import resolve_dataset_preflight
from backend.app.research_lifecycle import require_neutral_transaction
from backend.app.services import DomainError
from backend.app.structured_factor_io import read_structured_artifact
from backend.app.structured_factor_models import (
    StructuredExecution,
    StructuredStrategySpec,
    StructuredStrategyVersion,
)
from backend.app.structured_process_services import require_structured_capability
from packages.factors.contracts import canonical_bytes, digest
from packages.structured_factor_strategy.assembly import assemble_input
from packages.structured_factor_strategy.contracts import StructuredProgram
from packages.structured_factor_strategy.market import ARTIFACTS, stage_market
from packages.structured_factor_strategy.run import StructuredRunConfig
from packages.structured_factor_strategy.wire import read_input, write_input

SCOPE = ("owner_id", "run_id", "attempt_id", "generation")


def fail():
    raise DomainError(
        "structured_factor_binding",
        "Exact current structured factor execution binding required",
        409,
    )


def require_structured_identity(run):
    """Pure identity check used only by explicit025 neutral fence dispatch."""
    try:
        config = StructuredRunConfig.model_validate(run.config)
        if (
            (run.purpose, run.engine) != ("factor_strategy", "structured")
            or run.strategy_version_id is not None
            or run.dataset_version_id != config.dataset_version_id
            or run.request_sha256 != digest(config)
            or run.generation != 1
        ):
            fail()
        return config
    except (TypeError, ValueError, AttributeError):
        fail()


def _spec(session, fence):
    run, _ = require_neutral_transaction(
        session, fence, resource_scope="factor_numeric"
    )
    require_structured_capability(session)
    return decode_structured_spec(session, run)


def decode_structured_spec(session, run):
    """Immutable metadata decoding only; caller separately owns live/history authority."""
    config = require_structured_identity(run)
    spec = session.get(StructuredStrategySpec, run.id)
    version = session.get(
        StructuredStrategyVersion, config.structured_strategy_version_id
    )
    if (
        spec is None
        or version is None
        or spec.owner_id != run.owner_id
        or version.owner_id != run.owner_id
    ):
        fail()
    try:
        program = StructuredProgram.model_validate_json(version.program_canonical)
        if (
            canonical_bytes(program).decode("ascii") != version.program_canonical
            or program.model_dump(mode="json") != version.program
            or digest(program) != version.program_sha256
            or version.program_sha256 != config.program_sha256
        ):
            fail()
        if (
            spec.structured_strategy_version_id,
            spec.config,
            spec.config_canonical,
            spec.config_sha256,
        ) != (
            version.id,
            config.model_dump(mode="json"),
            canonical_bytes(config).decode("ascii"),
            digest(config),
        ):
            fail()
    except (ValueError, TypeError):
        fail()
    return run, spec, config, program


@dataclass(frozen=True)
class ResolvedStructuredExecution:
    execution_id: UUID
    structured_strategy_version_id: UUID
    snapshot_id: UUID
    engine_image_id: str
    engine_installed_parent_image_id: str
    engine_dependency_lock_sha256: str
    strategy_input_sha256: str
    strategy_input_size_bytes: int
    market_sha256: str
    market_size_bytes: int


def require_structured_execution(session, fence, execution_id):
    _, _, config, program = _spec(session, fence)
    row = session.get(StructuredExecution, execution_id)
    if (
        row is None
        or any(getattr(row, k) != getattr(fence, k) for k in SCOPE)
        or row.structured_strategy_version_id != config.structured_strategy_version_id
        or not 0 <= row.ordinal < len(config.snapshots)
        or config.snapshots[row.ordinal] != row.snapshot_id
    ):
        fail()
    try:
        raw = row.strategy_input_canonical.encode("ascii")
        value = read_input(raw)
        if (
            value.model_dump(mode="json") != row.strategy_input
            or hashlib.sha256(raw).hexdigest() != row.strategy_input_sha256
            or len(raw) != row.strategy_input_size_bytes
            or value.program != program
            or value.execution_id != row.id
            or value.snapshot.snapshot_id != row.snapshot_id
            or value.snapshot.input.version.factor_version_id != row.factor_version_id
            or value.structured_strategy_version_id
            != row.structured_strategy_version_id
            or value.market_sha256 != row.market_sha256
            or any(getattr(value, k) != getattr(fence, k) for k in SCOPE)
        ):
            fail()
    except (ValueError, TypeError, UnicodeError):
        fail()
    return ResolvedStructuredExecution(
        row.id,
        row.structured_strategy_version_id,
        row.snapshot_id,
        config.engine_image_id,
        config.engine_installed_parent_image_id,
        config.engine_dependency_lock_sha256,
        row.strategy_input_sha256,
        row.strategy_input_size_bytes,
        row.market_sha256,
        row.market_size_bytes,
    )


def exposure_values(session, fence, run, config, dataset):
    from backend.app.experiment_services import _identities

    return {
        "owner_id": fence.owner_id,
        "dataset_version_id": dataset.id,
        "run_id": fence.run_id,
        "experiment_id": run.experiment_id,
        "split_version_id": run.split_version_id,
        "source_key": "structured-factor-source:" + str(fence.run_id),
        "identities": _identities(session, dataset),
        "start_date": dataset.start_date,
        "end_date": dataset.end_date,
        "purpose": "structured_factor_execution_source",
        "provenance": {
            "schema_version": "mineagent.structured-source-exposure.v1",
            "config_sha256": digest(config),
            "program_sha256": config.program_sha256,
            "snapshot_ids": [str(x) for x in config.snapshots],
            "dataset_manifest_sha256": config.dataset_sha256,
            "extent": "whole_retained_dataset",
            "interpretation": "research_input_admission_not_order_or_fill_evidence",
        },
    }


def _exposure(session, fence, run, config, dataset, *, create):
    values = exposure_values(session, fence, run, config, dataset)
    row = session.scalar(
        select(DataUsageExposure).filter_by(
            owner_id=fence.owner_id, source_key=values["source_key"]
        )
    )
    if row is None:
        if not create:
            fail()
        row = DataUsageExposure(id=uuid4(), **values)
        session.add(row)
        session.flush()
    if any(getattr(row, k) != v for k, v in values.items()):
        fail()
    return row


def _materialize(
    session, fence, execution_id, snapshot_id, settings, *, create_exposure=False
):
    run, _, config, program = _spec(session, fence)
    value, market, dataset = reconstruct_retained_input(
        session, fence, execution_id, snapshot_id, config, program, settings
    )
    _exposure(session, fence, run, config, dataset, create=create_exposure)
    return value, market


def reconstruct_retained_input(
    session,
    fence,
    execution_id,
    snapshot_id,
    config,
    program,
    settings,
    *,
    declared_upload=False,
):
    """Data reconstruction shared with historical reads, never a live capability.

    The scope argument supplies immutable IDs, not lease or launch authority.
    Public callers must establish current owner/terminal graph or fresh live scope.
    """
    require_native_storage_guard(settings.storage_root)
    if snapshot_id not in config.snapshots:
        fail()
    historical = read_snapshot_in_session(
        session, fence.owner_id, snapshot_id, settings
    )
    publication = session.get(ExecutionSnapshotPublication, snapshot_id)
    if publication is None or publication.owner_id != fence.owner_id:
        fail()
    manifest = decode_publication(publication).manifest
    if (
        historical.manifest_sha256 != digest(manifest)
        or historical.snapshot_id != manifest.snapshot_id
        or historical.factor_version_id != manifest.input.version.factor_version_id
    ):
        fail()
    source = resolve_dataset_preflight(
        session,
        owner_id=fence.owner_id,
        dataset_id=config.dataset_version_id,
        policy=config.source_policy,
        input_names=tuple(x.name for x in manifest.input.definition.input_fields),
        settings=settings,
    )
    dataset = session.get(DatasetVersion, config.dataset_version_id)
    if (
        dataset is None
        or dataset.owner_id != fence.owner_id
        or dataset.content_sha256 != config.dataset_sha256
        or dataset.storage_key != f"datasets/{fence.owner_id}/{dataset.id}"
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
    import json

    metadata = json.loads(raw)
    if metadata != dataset.manifest:
        fail()
    actual_declared = metadata.get("schema_version") == 3
    if actual_declared and declared_upload is not True:
        fail()
    artifacts = {}
    total = len(raw)
    if actual_declared:
        from backend.app.declared_upload_io import read_dataset_bytes
        from backend.app.declared_upload_services import (
            require_revision,
            verify_basket_relations,
        )

        require_revision(session)
        verify_basket_relations(session, fence.owner_id, dataset)
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
        dataset_sha256=config.dataset_sha256,
        window=config.execution_window,
        declared_upload=actual_declared,
        dataset_id=dataset.id,
        owner_id=fence.owner_id,
    )
    value = assemble_input(
        fence=fence,
        execution_id=execution_id,
        config=config,
        program=program,
        snapshot=manifest,
        rows=historical.rows,
        source=source,
        market=market,
    )
    return value, market.content, dataset


def resolve_structured_execution_input(session, fence, execution_id, settings):
    binding = require_structured_execution(session, fence, execution_id)
    value, market = _materialize(
        session, fence, execution_id, binding.snapshot_id, settings
    )
    strategy = write_input(value)
    if (
        hashlib.sha256(strategy).hexdigest(),
        len(strategy),
        hashlib.sha256(market).hexdigest(),
        len(market),
    ) != (
        binding.strategy_input_sha256,
        binding.strategy_input_size_bytes,
        binding.market_sha256,
        binding.market_size_bytes,
    ):
        fail()
    require_neutral_transaction(session, fence, resource_scope="factor_numeric")
    require_native_storage_guard(settings.storage_root)
    return strategy, market


def prepare_structured_execution(session, fence, *, ordinal, settings):
    """Flush-only immutable input declaration; no launch, commit, or detached grant."""
    _, _, config, _ = _spec(session, fence)
    if type(ordinal) is not int or not 0 <= ordinal < len(config.snapshots):
        fail()
    old = session.scalar(
        select(StructuredExecution).filter_by(
            owner_id=fence.owner_id, run_id=fence.run_id, ordinal=ordinal
        )
    )
    if old is not None:
        resolve_structured_execution_input(session, fence, old.id, settings)
        return old
    execution_id = uuid4()
    value, market = _materialize(
        session,
        fence,
        execution_id,
        config.snapshots[ordinal],
        settings,
        create_exposure=True,
    )
    raw = write_input(value)
    row = StructuredExecution(
        id=execution_id,
        **{k: getattr(fence, k) for k in SCOPE},
        ordinal=ordinal,
        structured_strategy_version_id=config.structured_strategy_version_id,
        snapshot_id=value.snapshot.snapshot_id,
        factor_version_id=value.snapshot.input.version.factor_version_id,
        strategy_input=value.model_dump(mode="json"),
        strategy_input_canonical=raw.decode("ascii"),
        strategy_input_sha256=hashlib.sha256(raw).hexdigest(),
        strategy_input_size_bytes=len(raw),
        market_sha256=hashlib.sha256(market).hexdigest(),
        market_size_bytes=len(market),
    )
    session.add(row)
    session.flush()
    return row
