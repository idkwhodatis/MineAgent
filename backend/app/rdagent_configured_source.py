"""Fresh retained dataset/PIT-assumption resolution; no detached execution grant."""

import hashlib
import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from backend.app.core.atomic_storage import _open_directory
from backend.app.models import DatasetVersion
from backend.app.rdagent_services import require_research_spec
from backend.app.research_lifecycle import require_neutral_transaction
from backend.app.services import DomainError
from packages.factors.contracts import digest
from packages.rdagent_adapter.configured_source import (
    ARTIFACTS,
    ConfiguredSource,
    ConfiguredSourcePolicy,
    parse_retained_source,
)
from packages.strategy_sdk import JsonLimits, parse_json

MANIFEST_LIMIT = 2 * 1024**2
ARTIFACT_LIMIT = 32 * 1024**2
TOTAL_LIMIT = 64 * 1024**2


def _fail():
    raise DomainError(
        "rd_retained_dataset_drift",
        "Exact retained declared daily source is unavailable or changed",
        409,
    )


def read_dataset_artifact(root, storage_key, name, expected_hash, maximum):
    """Fixed leaf under exact dataset directory, bounded stable nofollow read."""
    if (
        type(storage_key) is not str
        or re.fullmatch(r"datasets/[a-f0-9-]{36}/[a-f0-9-]{36}", storage_key) is None
        or name not in {"manifest.json", *ARTIFACTS}
        or type(expected_hash) is not str
        or re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None
    ):
        _fail()
    deadline = time.monotonic() + 10
    parent = _open_directory(Path(root).absolute() / storage_key)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or not 0 < before.st_size <= maximum
            ):
                _fail()
            content = bytearray()
            while chunk := os.read(fd, min(65536, maximum + 1 - len(content))):
                if time.monotonic() >= deadline:
                    _fail()
                content.extend(chunk)
                if len(content) > maximum:
                    _fail()
            after = os.fstat(fd)
            named = os.stat(name, dir_fd=parent, follow_symlinks=False)
            identity = lambda value: (
                value.st_dev,
                value.st_ino,
                value.st_mode,
                value.st_nlink,
                value.st_size,
                value.st_mtime_ns,
                value.st_ctime_ns,
            )
            if (
                identity(before) != identity(after)
                or identity(after) != identity(named)
                or len(content) != before.st_size
                or hashlib.sha256(content).hexdigest() != expected_hash
                or time.monotonic() >= deadline
            ):
                _fail()
            return bytes(content)
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def resolve_dataset_preflight(
    session, *, owner_id, dataset_id, policy, input_names, settings
):
    """Read-only prospective input; issuer MUST resolve again inside its fence."""
    if type(owner_id) is not UUID or type(dataset_id) is not UUID:
        _fail()
    row = session.scalar(
        select(DatasetVersion).filter_by(owner_id=owner_id, id=dataset_id)
    )
    if row is None or row.storage_key != f"datasets/{owner_id}/{dataset_id}":
        _fail()
    try:
        raw = read_dataset_artifact(
            settings.storage_root,
            row.storage_key,
            "manifest.json",
            row.content_sha256,
            MANIFEST_LIMIT,
        )
        manifest = parse_json(
            raw,
            limits=JsonLimits(
                max_bytes=MANIFEST_LIMIT, max_nodes=100000, max_items=10000
            ),
        )
        if manifest != row.manifest:
            _fail()
        artifacts = {}
        total = len(raw)
        for name in ARTIFACTS:
            artifacts[name] = read_dataset_artifact(
                settings.storage_root,
                row.storage_key,
                name,
                manifest["artifact_files"][name],
                ARTIFACT_LIMIT,
            )
            total += len(artifacts[name])
            if total > TOTAL_LIMIT:
                _fail()
        source = parse_retained_source(
            dataset_id=row.id,
            manifest_sha256=row.content_sha256,
            manifest_bytes=raw,
            artifacts=artifacts,
            policy=policy,
            input_names=input_names,
        )
        if (row.start_date, row.end_date) != (source.source_start, source.source_end):
            _fail()
        expected_timezone = {"US": "America/New_York", "CN": "Asia/Shanghai"}.get(
            row.market
        )
        if expected_timezone != source.calendar.timezone:
            _fail()
        return source
    except (OSError, ValueError, KeyError, TypeError):
        _fail()


def _plan(session, fence):
    from backend.app.factor_analysis_models import FactorAnalysisPlan

    plan = session.scalar(
        select(FactorAnalysisPlan)
        .filter_by(owner_id=fence.owner_id, run_id=fence.run_id)
        .with_for_update()
    )
    if plan is None:
        _fail()
    policy = ConfiguredSourcePolicy.model_validate(plan.source_policy)
    if digest(policy) != plan.source_policy_sha256:
        _fail()
    return plan, policy


def resolve_configured_source(session, fence, settings) -> ConfiguredSource:
    """Current run/spec + pre-generation plan + actual byte reread on EVERY use."""
    require_neutral_transaction(session, fence, resource_scope="factor_objects")
    spec, config = require_research_spec(session, fence)
    plan, policy = _plan(session, fence)
    if (
        plan.spec_id,
        plan.config_sha256,
        plan.input_sha256,
        plan.label_source_dataset_id,
        plan.label_source_manifest_sha256,
    ) != (
        spec.id,
        config.sha256,
        config.input.sha256,
        config.input.dataset.dataset_version_id,
        config.input.dataset.content_sha256,
    ):
        _fail()
    source = resolve_dataset_preflight(
        session,
        owner_id=fence.owner_id,
        dataset_id=config.input.dataset.dataset_version_id,
        policy=policy,
        input_names=tuple(field.name for field in config.input.input_fields),
        settings=settings,
    )
    if (
        source.dataset,
        source.universe,
        source.calendar.sha256,
        source.calendar.timezone,
        source.input_fields,
    ) != (
        config.input.dataset,
        config.input.calculation_universe,
        config.input.calendar_sha256,
        config.input.market_timezone,
        config.input.input_fields,
    ):
        _fail()
    if (
        config.input.preprocessing.kind != "none"
        or not source.source_start
        <= config.input.window.start
        < config.input.window.end
        <= source.source_end
    ):
        _fail()
    days = tuple(day.session_date for day in source.calendar.sessions)
    selected = [
        day
        for day in days
        if config.input.window.start <= day < config.input.window.end
    ]
    if (
        not selected
        or days.index(selected[0]) < config.input.lookback_sessions
        or days.index(selected[-1]) + config.input.lag_sessions >= len(days)
    ):
        _fail()
    return source


@dataclass(frozen=True)
class VerifiedLabelSource:
    dataset_id: UUID
    source_manifest_sha256: str
    source_policy_sha256: str
    label_recipe_sha256: str
    manifest: object
    rows: tuple
    calendar: object
    qualification: str = "retained_declared_source_not_independently_verified_pit"


def resolve_configured_labels(session, fence, settings):
    """Labels stay host/analysis-only, never mounted into candidate prefix units."""

    source = resolve_configured_source(session, fence, settings)
    _spec, config = require_research_spec(session, fence)
    plan, policy = _plan(session, fence)
    from backend.app.factor_analysis_canonical import read_objective

    try:
        objective = read_objective(plan)
    except ValueError:
        _fail()
    if digest(objective) != plan.objective_sha256:
        _fail()
    # Read the independently hash-bound same retained bars, selecting close only
    # for labels. A factor that requests volume alone never receives these closes.
    closes = resolve_dataset_preflight(
        session,
        owner_id=fence.owner_id,
        dataset_id=source.dataset.dataset_version_id,
        policy=policy,
        input_names=("close",),
        settings=settings,
    )
    if (
        closes.dataset != source.dataset
        or closes.calendar != source.calendar
        or closes.universe != source.universe
    ):
        _fail()
    if (
        objective.window.start < config.input.window.start
        or objective.window.end > config.input.window.end
    ):
        _fail()
    return derive_configured_labels(closes, objective)


def derive_configured_labels(source, objective):
    """Pure close-only retained-source label derivation shared with cold checks.

    Source hash/ownership and objective predeclaration are the caller's separate
    obligations; this data result is never an admission/publication grant.
    """
    from packages.qlib_adapter.contracts import LabelManifest, LabelRow
    from packages.qlib_adapter.objective import DirectObjectiveDeclaration

    objective = DirectObjectiveDeclaration.model_validate_json(
        objective.model_dump_json()
    )
    if tuple(field.name for field in source.input_fields) != ("close",):
        raise ValueError("Label derivation requires exact close-only source")
    sessions = source.calendar.sessions
    horizon = objective.label_horizon_sessions
    positions = {day.session_date: i for i, day in enumerate(sessions)}
    selected = [
        day
        for day in sessions
        if objective.window.start <= day.session_date < objective.window.end
    ]
    if (
        not selected
        or objective.window.start < source.source_start
        or objective.window.end > source.source_end
        or positions[selected[-1].session_date] + horizon >= len(sessions)
    ):
        _fail()
    prices = {
        (row.session, row.listing_id): row.values[0] for row in source.observations
    }
    rows = []
    for day in selected:
        end = sessions[positions[day.session_date] + horizon]
        for listing in source.universe.listing_ids:
            initial, final = (
                prices[(day.session_date, listing)],
                prices[(end.session_date, listing)],
            )
            value = (
                None if initial in (None, 0) or final is None else final / initial - 1
            )
            rows.append(
                LabelRow(
                    session=day.session_date,
                    listing_id=listing,
                    horizon_end=end.session_date,
                    horizon_end_time=end.close_time,
                    available_at=end.close_time,
                    value=value,
                )
            )
    manifest = LabelManifest(
        dataset_id=source.dataset.dataset_version_id,
        dataset_sha256=source.dataset.content_sha256,
        calendar_sha256=source.calendar.sha256,
        calendar_sessions_sha256=digest(
            [day.session_date.isoformat() for day in sessions]
        ),
        market_timezone=source.calendar.timezone,
        universe_sha256=source.universe.content_sha256,
        rows_sha256=digest([row.model_dump(mode="json") for row in rows]),
        row_count=len(rows),
        horizon_sessions=horizon,
        price_semantics="unadjusted",
    )
    recipe = digest(
        {
            "schema_version": "mineagent.retained-close-label-recipe.v1",
            "source_availability_revision": source.dataset.availability_revision_sha256,
            "objective_sha256": objective.sha256,
            "formula": "close[t+h]/close[t]-1;zero_or_missing_denominator_is_missing",
            "manifest_sha256": manifest.sha256,
        }
    )
    return VerifiedLabelSource(
        source.dataset.dataset_version_id,
        source.dataset.content_sha256,
        source.policy_sha256,
        recipe,
        manifest,
        tuple(rows),
        source.calendar,
    )
