"""Owner-scoped factor publication with physical quotas and nonblocking fences.

Only bounded trusted bytes are published here. Public binary import is disabled:
no native tabular parser, uploaded Python, or uploaded verification claim runs in
this credential-bearing process. Quota accounts all physical orphan bytes.
"""

import fcntl
import hashlib
import os
import re
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import func, select, text

from backend.app.core.atomic_storage import (
    _prepare_directory,
    publish_bytes,
)
from backend.app.core.storage_barrier import storage_guard
from backend.app.factor_models import (
    Factor,
    FactorBundle,
    FactorBundleMember,
    FactorComparison,
    FactorEvaluation,
    FactorObject,
    FactorPackage,
    FactorSubmission,
    FactorVersion,
    FeatureSnapshot,
    FeatureSnapshotObject,
)
from backend.app.models import User, utcnow
from backend.app.services import DomainError
from backend.app.storage import read_verified
from packages.factors.contracts import canonical_bytes, digest, source_sha256

OBJECT_TYPES = {
    "code": ("py", "text/plain"),
    "snapshot_manifest": ("json", "application/json"),
    "values_partition": ("parquet", "application/vnd.apache.parquet"),
    "calculation_samples": ("json", "application/json"),
    "analysis_manifest": ("json", "application/json"),
    "analysis_series": ("json", "application/json"),
    "native_evidence": ("json", "application/json"),
    "package": ("zip", "application/zip"),
}


def enabled(settings):
    if not settings.factor_library_enabled:
        raise DomainError("factor_library_disabled", "Factor library is disabled", 403)


def check_deadline(deadline):
    if time.monotonic() >= deadline:
        raise DomainError(
            "factor_admission_timeout", "Factor publication deadline exceeded", 503
        )


def try_lock(session, scope):
    key = int.from_bytes(
        hashlib.sha256(scope.encode()).digest()[:8], "big", signed=True
    )
    if (
        session.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key})
        is not True
    ):
        raise DomainError("factor_busy", "Factor publication is busy; retry later", 503)


def physical_inventory(
    root_fd,
    owner_id,
    deadline,
    max_files,
    *,
    allow_staging=False,
    native=False,
    native_entries=None,
):
    """Descriptor-pinned, bounded no-follow traversal; unknown staging fails closed."""
    total = owned = count = 0

    def scan(fd, parts):
        nonlocal total, owned, count
        check_deadline(deadline)
        before = os.fstat(fd)
        names = os.listdir(fd)
        if len(names) + count > max_files:
            raise DomainError(
                "factor_capacity", "Factor physical file budget exceeded", 409
            )
        for name in names:
            check_deadline(deadline)
            count += 1
            if count > max_files or len(parts) > 6:
                raise DomainError(
                    "factor_capacity", "Factor physical inventory exceeds bounds", 409
                )
            child_parts = (*parts, name)
            if not parts:
                try:
                    UUID(name)
                except ValueError:
                    raise DomainError(
                        "factor_storage_uncertain",
                        "Unknown factor owner namespace",
                        409,
                    ) from None
            elif len(parts) == 1 and name not in (
                {"objects", "staging", "datasets"}
                | ({"native-journals"} if native else set())
            ):
                raise DomainError(
                    "factor_storage_uncertain", "Unknown factor storage layout", 409
                )
            elif len(parts) == 2 and parts[1] == "staging" and not allow_staging:
                # No supervised import runtime is accepted yet. An operation left by
                # any writer is an independent recovery barrier, never mere garbage.
                raise DomainError(
                    "factor_recovery_required",
                    "Factor operation requires verified recovery",
                    409,
                )
            child = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd
            )
            try:
                info = os.fstat(child)
                if stat.S_ISDIR(info.st_mode):
                    if len(parts) >= 2 and parts[1] in {"objects", "native-journals"}:
                        raise DomainError(
                            "factor_storage_uncertain",
                            "Unexpected nested object directory",
                            409,
                        )
                    scan(child, child_parts)
                elif (
                    stat.S_ISREG(info.st_mode)
                    and info.st_nlink == 1
                    and (
                        (
                            len(parts) == 2
                            and (
                                parts[1] == "objects"
                                or (native and parts[1] == "native-journals")
                            )
                        )
                        or (len(parts) == 3 and parts[1] == "datasets")
                        or (allow_staging and len(parts) >= 2 and parts[1] == "staging")
                    )
                ):
                    if parts[1] == "objects" and not re.fullmatch(
                        (
                            r"(?:[a-f0-9]{64}\.(?:py|json|parquet|zip|h5)|\.[a-f0-9]{64}\.(?:py|json|parquet|zip|h5)\.[a-f0-9]{32}\.tmp)"
                            if native
                            else r"(?:[a-f0-9]{64}\.(?:py|json|parquet|zip)|\.[a-f0-9]{64}\.(?:py|json|parquet|zip)\.[a-f0-9]{32}\.tmp)"
                        ),
                        name,
                    ):
                        raise DomainError(
                            "factor_storage_uncertain",
                            "Unknown factor object name",
                            409,
                        )
                    if parts[1] == "datasets" and not re.fullmatch(
                        r"(?:manifest|observations)\.json|\.(?:manifest|observations)\.json\.[a-f0-9]{32}\.tmp",
                        name,
                    ):
                        raise DomainError(
                            "factor_storage_uncertain",
                            "Unknown fixture dataset file",
                            409,
                        )
                    if native and (parts[1] == "native-journals" or ".h5" in name):
                        if parts[1] == "native-journals":
                            if not re.fullmatch(r"[a-f0-9-]{36}\.json", name):
                                raise DomainError(
                                    "native_recovery_required",
                                    "Unknown or temporary native journal",
                                    409,
                                )
                            if (
                                str(UUID(name[:-5])) != name[:-5]
                                or info.st_size > 65536
                            ):
                                raise DomainError(
                                    "native_recovery_required",
                                    "Invalid native journal",
                                    409,
                                )
                        elif name.startswith(".") or info.st_size > 16777216:
                            raise DomainError(
                                "native_recovery_required",
                                "Native temporary or oversized object",
                                409,
                            )
                        if native_entries is not None:
                            native_entries.append(
                                ("factors/" + "/".join(child_parts), info.st_size)
                            )
                    total += info.st_size
                    if parts[0] == str(owner_id):
                        owned += info.st_size
                else:
                    raise DomainError(
                        "factor_storage_uncertain", "Unsafe factor physical entry", 409
                    )
            finally:
                os.close(child)
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise DomainError(
                "factor_storage_uncertain", "Factor inventory changed during scan", 409
            )

    try:
        scan(root_fd, ())
    except OSError:
        raise DomainError(
            "factor_storage_uncertain", "Factor inventory could not be verified", 409
        ) from None
    return total, owned, count


class PublicationAdmission(float):
    """Trusted in-process fence capability, never serialized or passed to parser."""

    def __new__(cls, deadline, root_fd, factor_fd):
        instance = super().__new__(cls, deadline)
        instance.root_fd = root_fd
        instance.factor_fd = factor_fd
        return instance


@contextmanager
def publication(session, owner_id, settings, *, peak_bytes, peak_files=4):
    enabled(settings)
    # Authentication reads do not retain stale authority while entering publication.
    session.rollback()
    deadline = time.monotonic() + 20
    try:
        try_lock(session, f"submit:{owner_id}")
        try_lock(session, "factor-physical-quota")
        try_lock(session, f"factor-publish:{owner_id}")
        if not session.scalar(
            select(User.id).where(User.id == owner_id, User.is_active.is_(True))
        ):
            raise DomainError("unauthorized", "Invalid or inactive user", 401)
        with storage_guard(settings.storage_root) as root_fd:
            try:
                fd = _prepare_directory(
                    Path(settings.storage_root).absolute() / "factors",
                    Path(settings.storage_root).absolute(),
                )
            except OSError:
                raise DomainError(
                    "factor_storage_unavailable",
                    "Factor storage must be provisioned by the installation",
                    503,
                ) from None
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise DomainError(
                        "factor_busy", "Factor publisher is active; retry later", 503
                    ) from None
                current = os.stat(
                    Path(settings.storage_root) / "factors", follow_symlinks=False
                )
                pinned = os.fstat(fd)
                if (current.st_dev, current.st_ino) != (pinned.st_dev, pinned.st_ino):
                    raise DomainError(
                        "factor_storage_uncertain", "Factor fence identity changed", 409
                    )
                from runtimes.factor_parser.supervisor import (
                    ParserIsolationError,
                    assert_quiescent,
                )

                try:
                    assert_quiescent(settings.storage_root)
                except ParserIsolationError as exc:
                    raise DomainError(
                        exc.code, "Factor operation requires verified recovery", 409
                    ) from None
                maximum = getattr(settings, "factor_max_files", 10000)
                from backend.app.factor_native_services import (
                    native_capability,
                    pending_native_usage,
                    verify_native_journals,
                )
                from backend.app.rdagent_source_capacity import (
                    pending_source_usage_for_schema,
                )

                pending_total, pending_owned, pending_entries = (
                    pending_source_usage_for_schema(session, owner_id)
                )

                native = native_capability(session)
                native_entries = []
                total, owned, count = physical_inventory(
                    fd,
                    owner_id,
                    deadline,
                    maximum,
                    allow_staging=True,
                    native=native,
                    native_entries=native_entries,
                )
                if native:
                    verify_native_journals(session, native_entries, settings)
                    held_total, held_owned, held_entries = pending_native_usage(
                        session, owner_id
                    )
                    total += held_total
                    owned += held_owned
                    count += held_entries
                if (
                    total + pending_total + peak_bytes
                    > getattr(settings, "factor_installation_max_bytes", 2 * 1024**3)
                    or owned + pending_owned + peak_bytes
                    > getattr(settings, "factor_owner_max_bytes", 512 * 1024**2)
                    or count + pending_entries + peak_files > maximum
                ):
                    raise DomainError(
                        "factor_capacity", "Factor physical quota exceeded", 409
                    )
                yield PublicationAdmission(deadline, root_fd, fd)
                check_deadline(deadline)
                try:
                    assert_quiescent(settings.storage_root)
                except ParserIsolationError as exc:
                    raise DomainError(
                        exc.code, "Factor operation requires verified recovery", 409
                    ) from None
                native_entries = []
                final_total, final_owned, final_count = physical_inventory(
                    fd,
                    owner_id,
                    deadline,
                    maximum,
                    allow_staging=True,
                    native=native,
                    native_entries=native_entries,
                )
                if native:
                    verify_native_journals(session, native_entries, settings)
                    held_total, held_owned, held_entries = pending_native_usage(
                        session, owner_id
                    )
                    final_total += held_total
                    final_owned += held_owned
                    final_count += held_entries
                pending_total, pending_owned, pending_entries = (
                    pending_source_usage_for_schema(session, owner_id)
                )
                if (
                    final_total + pending_total > settings.factor_installation_max_bytes
                    or final_owned + pending_owned > settings.factor_owner_max_bytes
                    or final_count + pending_entries > maximum
                ):
                    raise DomainError(
                        "factor_capacity",
                        "Factor physical quota exceeded after publication",
                        409,
                    )
                # Never reconnect a lost session to finish publication.
                if not session.is_active or session.connection().invalidated:
                    raise DomainError(
                        "factor_admission_lost",
                        "Factor publication transaction was lost",
                        503,
                    )
                check_deadline(deadline)
                remaining_ms = max(
                    1, min(10000, int((deadline - time.monotonic()) * 1000))
                )
                session.execute(
                    text("SELECT set_config('statement_timeout', :timeout, true)"),
                    {"timeout": str(remaining_ms)},
                )
                check_deadline(deadline)
                session.commit()
                # Late or uncertain commit never receives a ready acknowledgement.
                check_deadline(deadline)
            finally:
                os.close(fd)
    except BaseException:
        session.rollback()
        raise


def owned(session, cls, owner_id, resource_id):
    row = session.scalar(
        select(cls).where(cls.id == resource_id, cls.owner_id == owner_id)
    )
    if row is None:
        raise DomainError("not_found", "Factor resource not found", 404)
    return row


def public(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def submission(session, owner_id, operation, key, payload):
    if not key or not re.fullmatch(r"[!-~]{1,128}", key):
        raise DomainError(
            "invalid_idempotency_key",
            "A bounded Idempotency-Key header is required",
            422,
        )
    request_hash = digest(payload)
    existing = session.scalar(
        select(FactorSubmission).where(
            FactorSubmission.owner_id == owner_id,
            FactorSubmission.operation == operation,
            FactorSubmission.idempotency_key == key,
        )
    )
    if existing and existing.request_sha256 != request_hash:
        raise DomainError(
            "idempotency_conflict", "Idempotency key has different request content", 409
        )
    return existing, request_hash


def save_submission(session, owner_id, operation, key, request_hash, resource_id):
    session.add(
        FactorSubmission(
            owner_id=owner_id,
            operation=operation,
            idempotency_key=key,
            request_sha256=request_hash,
            resource_id=resource_id,
        )
    )


def publish_object(session, owner_id, kind, content, settings, deadline):
    check_deadline(deadline)
    if kind not in OBJECT_TYPES:
        raise DomainError(
            "invalid_factor_object", "Unsupported factor object kind", 422
        )
    sha = hashlib.sha256(content).hexdigest()
    ext, media = OBJECT_TYPES[kind]
    # Kind remains part of the physical address to avoid ambiguous json registrations.
    key = f"factors/{owner_id}/objects/{sha}.{ext}"
    existing = session.scalar(
        select(FactorObject).where(
            FactorObject.owner_id == owner_id, FactorObject.storage_key == key
        )
    )
    if existing:
        if (
            existing.object_kind != kind
            or existing.sha256 != sha
            or existing.size_bytes != len(content)
        ):
            raise DomainError(
                "factor_object_conflict", "Physical object registration differs", 409
            )
        read_verified(settings.storage_root, key, sha, len(content))
        check_deadline(deadline)
        return existing
    publish_bytes(
        Path(settings.storage_root) / key,
        content,
        sha256=sha,
        durable_root=Path(settings.storage_root),
    )
    check_deadline(deadline)
    row = FactorObject(
        id=uuid4(),
        owner_id=owner_id,
        storage_key=key,
        sha256=sha,
        size_bytes=len(content),
        media_type=media,
        object_kind=kind,
    )
    session.add(row)
    session.flush()
    return row


def verify_version(session, owner_id, version, settings):
    obj = owned(session, FactorObject, owner_id, version.code_object_id)
    content = read_verified(settings.storage_root, obj.storage_key, obj.sha256, 131072)
    if (
        content != version.code.encode()
        or source_sha256(version.code) != version.code_sha256
        or digest(version.definition) != version.definition_sha256
    ):
        raise DomainError(
            "factor_integrity", "Factor immutable definition differs", 409
        )


def _new_version(
    session, owner_id, factor, body, settings, deadline, source_kind="manual"
):
    definition = body.definition.model_dump(mode="json")
    if source_sha256(body.code) != definition["code_sha256"]:
        raise DomainError(
            "factor_source_mismatch", "Exact source bytes do not match definition", 422
        )
    parent = None
    if body.parent_version_id:
        parent = owned(session, FactorVersion, owner_id, body.parent_version_id)
        if parent.factor_id != factor.id:
            raise DomainError("not_found", "Factor parent not found", 404)
    ordinal = (
        session.scalar(
            select(func.max(FactorVersion.ordinal)).where(
                FactorVersion.factor_id == factor.id
            )
        )
        or 0
    ) + 1
    obj = publish_object(
        session, owner_id, "code", body.code.encode(), settings, deadline
    )
    version = FactorVersion(
        id=uuid4(),
        owner_id=owner_id,
        factor_id=factor.id,
        ordinal=ordinal,
        parent_version_id=parent.id if parent else None,
        definition=definition,
        definition_sha256=digest(definition),
        code=body.code,
        code_sha256=obj.sha256,
        code_object_id=obj.id,
        source_kind=source_kind,
        source_provenance={
            "history_completeness": "unknown",
            "origin": source_kind,
            "change_note": body.change_note,
        },
    )
    session.add(version)
    session.flush()
    return version


def create_factor(session, owner_id, body, key, settings, factor_id=None):
    operation = "create_factor" if factor_id is None else f"append_version:{factor_id}"
    with publication(
        session, owner_id, settings, peak_bytes=2 * len(body.code.encode()) + 8192
    ) as deadline:
        old, request_hash = submission(
            session, owner_id, operation, key, body.model_dump(mode="json")
        )
        if old:
            version = owned(session, FactorVersion, owner_id, old.resource_id)
            factor = owned(session, Factor, owner_id, version.factor_id)
            verify_version(session, owner_id, version, settings)
            result = {"factor": public(factor), "version": public(version)}
        else:
            if factor_id is None:
                factor = Factor(
                    id=uuid4(),
                    owner_id=owner_id,
                    name=body.name,
                    category=body.category,
                )
                session.add(factor)
                session.flush()
            else:
                factor = owned(session, Factor, owner_id, factor_id)
                if body.parent_version_id is None:
                    raise DomainError(
                        "factor_parent_required",
                        "New versions require an exact parent",
                        422,
                    )
            version = _new_version(session, owner_id, factor, body, settings, deadline)
            save_submission(session, owner_id, operation, key, request_hash, version.id)
            result = {"factor": public(factor), "version": public(version)}
    return result


def inherited_exposure_claims(session, owner_id, version_ids, deadline=None):
    """Carry known local history, while never promoting absent evidence to clean."""
    from fastapi.encoders import jsonable_encoder

    from backend.app.models import DatasetVersion, DataUsageExposure

    inherited = []
    seen = set()
    pending = list(version_ids)
    dataset_ids = set()
    while pending:
        if deadline is not None:
            check_deadline(deadline)
        version_id = pending.pop()
        if version_id in seen:
            continue
        seen.add(version_id)
        if len(seen) > 1000:
            raise DomainError(
                "factor_provenance_capacity",
                "Factor lineage exceeds export profile",
                409,
            )
        version = owned(session, FactorVersion, owner_id, version_id)
        envelope_id = version.source_provenance.get("import_envelope_id")
        if envelope_id:
            from backend.app.factor_import_models import FactorImportEnvelope

            envelope = owned(session, FactorImportEnvelope, owner_id, UUID(envelope_id))
            inherited.extend(envelope.origin_payload.get("exposure_claims", []))
            if len(inherited) > 256:
                raise DomainError(
                    "factor_provenance_capacity",
                    "Imported exposure evidence exceeds export profile",
                    409,
                )
        if version.parent_version_id:
            pending.append(version.parent_version_id)
        dataset_ids.update(
            session.scalars(
                select(FeatureSnapshot.dataset_version_id).where(
                    FeatureSnapshot.owner_id == owner_id,
                    FeatureSnapshot.factor_version_id == version_id,
                )
            )
        )
    if not dataset_ids:
        if len(canonical_bytes(inherited)) > 262144:
            raise DomainError(
                "factor_provenance_capacity",
                "Exposure evidence exceeds byte profile",
                409,
            )
        return inherited
    rows = list(
        session.scalars(
            select(DataUsageExposure)
            .where(
                DataUsageExposure.owner_id == owner_id,
                DataUsageExposure.dataset_version_id.in_(dataset_ids),
            )
            .order_by(DataUsageExposure.created_at, DataUsageExposure.id)
            .limit(257)
        )
    )
    if len(rows) > 256:
        raise DomainError(
            "factor_provenance_capacity",
            "Factor exposure evidence exceeds export profile",
            409,
        )
    result = inherited
    for row in rows:
        if deadline is not None:
            check_deadline(deadline)
        dataset = owned(session, DatasetVersion, owner_id, row.dataset_version_id)
        evidence = jsonable_encoder(public(row))
        result.append(
            {
                "source": "locally_recorded_claim",
                "evidence_sha256": digest(evidence),
                "dataset_content_sha256": dataset.content_sha256,
                "evidence": evidence,
            }
        )
    if len(canonical_bytes(result)) > 262144:
        raise DomainError(
            "factor_provenance_capacity",
            "Factor exposure evidence exceeds byte profile",
            409,
        )
    return result


def usage():
    return {
        "declared": 0,
        "explicit_read": 0,
        "experiment": 0,
        "observation_coverage": "not_instrumented",
    }


def list_factors(
    session, owner_id, *, offset=0, limit=50, search=None, bookmarked=None, kind=None
):
    query = select(Factor).where(Factor.owner_id == owner_id)
    if search:
        query = query.where(
            Factor.name.ilike(
                "%" + search.replace("%", "\\%").replace("_", "\\_") + "%", escape="\\"
            )
        )
    if bookmarked is not None:
        query = query.where(Factor.bookmarked == bookmarked)
    # Filter on the exact latest immutable version, not arbitrary historical kinds.
    latest = (
        select(func.max(FactorVersion.ordinal))
        .where(FactorVersion.factor_id == Factor.id)
        .correlate(Factor)
        .scalar_subquery()
    )
    if kind:
        query = query.where(
            select(FactorVersion.id)
            .where(
                FactorVersion.factor_id == Factor.id,
                FactorVersion.ordinal == latest,
                FactorVersion.definition["factor_kind"].astext == kind,
            )
            .exists()
        )
    count = session.scalar(select(func.count()).select_from(query.subquery()))
    data = []
    for factor in session.scalars(
        query.order_by(Factor.updated_at.desc(), Factor.id).offset(offset).limit(limit)
    ):
        version = session.scalar(
            select(FactorVersion)
            .where(
                FactorVersion.factor_id == factor.id, FactorVersion.owner_id == owner_id
            )
            .order_by(FactorVersion.ordinal.desc())
            .limit(1)
        )
        snapshot = (
            session.scalar(
                select(FeatureSnapshot)
                .where(
                    FeatureSnapshot.owner_id == owner_id,
                    FeatureSnapshot.factor_version_id == version.id,
                )
                .order_by(FeatureSnapshot.created_at.desc(), FeatureSnapshot.id)
                .limit(1)
            )
            if version
            else None
        )
        if snapshot:
            expose_snapshot(session, owner_id, snapshot, "list")
        data.append(
            {
                **public(factor),
                "latest_version_id": version.id if version else None,
                "ordinal": version.ordinal if version else None,
                "definition_sha256": version.definition_sha256 if version else None,
                "kind": version.definition["factor_kind"] if version else None,
                "calculation_state": "ready" if snapshot else "not_materialized",
                "selected_snapshot_id": snapshot.id if snapshot else None,
                "coverage": {
                    "row_count": snapshot.row_count,
                    "valid_count": snapshot.valid_count,
                    "missing_count": snapshot.missing_count,
                }
                if snapshot
                else None,
                "analysis_status": "not_evaluated",
                "usage": usage(),
            }
        )
    session.commit()
    return {"data": data, "count": count}


def patch_factor(session, owner_id, factor_id, body):
    try_lock(session, f"submit:{owner_id}")
    factor = owned(session, Factor, owner_id, factor_id)
    if factor.presentation_revision != body.expected_presentation_revision:
        raise DomainError(
            "factor_presentation_conflict",
            "Factor presentation changed; reload before editing",
            409,
        )
    for key in ("name", "category", "bookmarked"):
        if key in body.model_fields_set:
            value = getattr(body, key)
            if key != "category" and (
                value is None or (key == "name" and not value.strip())
            ):
                raise DomainError(
                    "invalid_factor_presentation", "Presentation value is required", 422
                )
            setattr(factor, key, value)
    factor.presentation_revision += 1
    factor.updated_at = utcnow()
    session.flush()
    result = public(factor)
    session.commit()
    return result


def verify_evaluation(evaluation):
    if (
        digest(evaluation.protocol) != evaluation.protocol_sha256
        or digest(evaluation.result_payload) != evaluation.result_sha256
    ):
        raise DomainError("factor_integrity", "Factor evaluation checksum differs", 409)
    if evaluation.kind != "materialization":
        from packages.factors.contracts import AnalysisResult

        try:
            result = AnalysisResult.model_validate(evaluation.result_payload)
        except ValueError:
            raise DomainError(
                "factor_integrity", "Factor analysis payload is invalid", 409
            ) from None
        if (
            result.protocol.model_dump(mode="json") != evaluation.protocol
            or result.status != evaluation.status
        ):
            raise DomainError(
                "factor_integrity", "Factor analysis protocol differs", 409
            )


def get_version(session, owner_id, factor_id, version_id, settings):
    owned(session, Factor, owner_id, factor_id)
    version = owned(session, FactorVersion, owner_id, version_id)
    if version.factor_id != factor_id:
        raise DomainError("not_found", "Factor version not found", 404)
    verify_version(session, owner_id, version, settings)
    snapshots = list(
        session.scalars(
            select(FeatureSnapshot)
            .where(
                FeatureSnapshot.owner_id == owner_id,
                FeatureSnapshot.factor_version_id == version.id,
            )
            .order_by(FeatureSnapshot.created_at, FeatureSnapshot.id)
        )
    )
    evaluations = list(
        session.scalars(
            select(FactorEvaluation).where(
                FactorEvaluation.owner_id == owner_id,
                FactorEvaluation.factor_version_id == version.id,
            )
        )
    )
    for evaluation in evaluations:
        verify_evaluation(evaluation)
    for snapshot in snapshots:
        verified_snapshot(session, owner_id, snapshot.id, settings)
        expose_snapshot(session, owner_id, snapshot, "detail")
    session.commit()
    return {
        "version": public(version),
        "snapshots": [public(s) for s in snapshots],
        "evaluations": [public(e) for e in evaluations],
        "usage": usage(),
    }


def expose_snapshot(session, owner_id, snapshot, purpose):
    from backend.app.experiment_services import record_exposure
    from backend.app.models import DatasetVersion
    from packages.factors.contracts import DateWindow

    dataset = owned(session, DatasetVersion, owner_id, snapshot.dataset_version_id)
    # Conservative full prefix covers warmup, membership and fixture calculation.
    record_exposure(
        session,
        owner_id=owner_id,
        dataset=dataset,
        window=DateWindow(start=dataset.start_date, end=dataset.end_date),
        purpose="factor_" + purpose,
        source_key=f"factor:{snapshot.id}:{purpose}",
        provenance={
            "snapshot_id": str(snapshot.id),
            "input_digest": snapshot.input_digest,
            "output_digest": snapshot.output_digest,
            "history": "unknown",
            "warmup_coverage": "conservative_dataset_extent",
        },
    )


def verified_snapshot(session, owner_id, snapshot_id, settings):
    from packages.factors.contracts import FeatureSnapshotManifest

    snapshot = owned(session, FeatureSnapshot, owner_id, snapshot_id)
    obj = owned(session, FactorObject, owner_id, snapshot.manifest_object_id)
    raw = read_verified(
        settings.storage_root, obj.storage_key, snapshot.manifest_sha256, 1048576
    )
    manifest = FeatureSnapshotManifest.model_validate_json(raw)
    if (
        manifest.snapshot_id != snapshot.id
        or manifest.input.version.factor_version_id != snapshot.factor_version_id
        or manifest.input.definition.sha256 != snapshot.definition_sha256
        or manifest.input_digest != snapshot.input_digest
        or manifest.output_digest != snapshot.output_digest
        or manifest.sha256 != snapshot.manifest_sha256
        or manifest.input.environment_sha256 != snapshot.environment_digest
        or manifest.input.dataset.dataset_version_id != snapshot.dataset_version_id
        or manifest.input.dataset.content_sha256 != snapshot.dataset_content_sha256
        or (manifest.row_count, manifest.valid_count, manifest.missing_count)
        != (snapshot.row_count, snapshot.valid_count, snapshot.missing_count)
    ):
        raise DomainError("factor_integrity", "Snapshot manifest binding differs", 409)
    associations = list(
        session.scalars(
            select(FeatureSnapshotObject).where(
                FeatureSnapshotObject.owner_id == owner_id,
                FeatureSnapshotObject.snapshot_id == snapshot.id,
            )
        )
    )
    inventory = []
    verified_bytes = 0
    for link in associations:
        child = owned(session, FactorObject, owner_id, link.object_id)
        verified_bytes += child.size_bytes
        if verified_bytes > 128 * 1024 * 1024:
            raise DomainError(
                "factor_query_capacity",
                "Snapshot verification exceeds bounded profile",
                409,
            )
        content = read_verified(
            settings.storage_root,
            child.storage_key,
            child.sha256,
            min(child.size_bytes, 128 * 1024 * 1024),
        )
        if len(content) != child.size_bytes or child.object_kind != link.role:
            raise DomainError(
                "factor_integrity", "Snapshot physical object differs", 409
            )
        inventory.append((link.role, link.ordinal, child.sha256, child.size_bytes))
    if sorted(inventory) != sorted(
        (o.role, o.ordinal, o.sha256, o.size_bytes) for o in manifest.objects
    ):
        raise DomainError(
            "factor_integrity", "Snapshot physical inventory differs", 409
        )
    if snapshot.local_verification.get("kind") == "declared_or_unknown":
        verify_imported_snapshot(session, owner_id, snapshot, manifest, settings)
    return snapshot, manifest, associations


def verify_imported_snapshot(session, owner_id, snapshot, manifest, settings):
    from backend.app.factor_import_models import FactorImportEnvelope
    from packages.factors.parser import validate_report
    from packages.factors.storage import FactorStorageError

    claims = snapshot.source_claims
    verification = snapshot.local_verification
    if (
        verification.get("admission_profile") != "isolated-import.v1"
        or verification.get("source_pit_verified") is not False
        or claims.get("derived_identity") is not True
    ):
        raise DomainError(
            "factor_import_integrity",
            "Snapshot has no admitted derived import identity",
            409,
        )
    try:
        envelope_id = UUID(claims["import_envelope_id"])
        report = validate_report(claims["parser_report"])
    except (KeyError, TypeError, ValueError, FactorStorageError):
        raise DomainError(
            "factor_import_integrity", "Snapshot parser evidence is invalid", 409
        ) from None
    envelope = owned(session, FactorImportEnvelope, owner_id, envelope_id)
    archive = owned(session, FactorObject, owner_id, envelope.archive_object_id)
    read_verified(
        settings.storage_root, archive.storage_key, envelope.archive_sha256, 10_000_000
    )
    if (
        digest(envelope.origin_payload) != envelope.origin_payload_sha256
        or digest(envelope.local_bindings) != envelope.local_bindings_sha256
        or str(snapshot.id) not in envelope.local_bindings.get("snapshot_ids", [])
        or not any(
            m.get("local_kind") == "feature_snapshot"
            and m.get("local_id") == str(snapshot.id)
            and m.get("local_sha256") == snapshot.manifest_sha256
            for m in envelope.local_bindings.get("origin_map", [])
        )
        or digest(report) != verification.get("evidence_sha256")
        or report["rows_sha256"] != snapshot.output_digest
        or report["snapshot_input_sha256"] != snapshot.input_digest
        or report["factor_version_ids"] != [str(snapshot.factor_version_id)]
        or (report["row_count"], report["valid_count"], report["missing_count"])
        != (snapshot.row_count, snapshot.valid_count, snapshot.missing_count)
        or not any(
            o.role == "values_partition"
            and o.sha256 == report["output_sha256"]
            and o.size_bytes == report["output_size_bytes"]
            for o in manifest.objects
        )
    ):
        raise DomainError(
            "factor_import_integrity", "Derived snapshot admission binding differs", 409
        )
    origin = next(
        (
            m
            for m in envelope.origin_payload.get("snapshots", [])
            if m.get("snapshot_id") == claims.get("origin_snapshot_id")
        ),
        None,
    )
    if (
        origin is None
        or origin.get("output_digest") != report["source_rows_sha256"]
        or not any(
            o.get("role") == "values_partition"
            and o.get("sha256") == report["input_sha256"]
            for o in origin.get("objects", [])
        )
    ):
        raise DomainError(
            "factor_import_integrity", "Original snapshot evidence differs", 409
        )


def create_bundle(session, owner_id, body, key, settings):
    with publication(
        session, owner_id, settings, peak_bytes=0, peak_files=0
    ) as deadline:
        old, sha = submission(
            session, owner_id, "create_bundle", key, body.model_dump(mode="json")
        )
        if old:
            result = public(owned(session, FactorBundle, owner_id, old.resource_id))
        else:
            for member in body.members:
                version = owned(
                    session, FactorVersion, owner_id, member.factor_version_id
                )
                if version.definition_sha256 != member.definition_sha256:
                    raise DomainError(
                        "factor_binding_conflict", "Bundle definition differs", 409
                    )
                verify_version(session, owner_id, version, settings)
            definition = {
                "schema_version": "mineagent.factor-bundle.v1",
                **body.model_dump(mode="json"),
                "exposure_claims": inherited_exposure_claims(
                    session,
                    owner_id,
                    [m.factor_version_id for m in body.members],
                    deadline,
                ),
                "exposure_history": {
                    "completeness": "unknown",
                    "unresolved_reasons": ["manual_or_external_history_unknown"],
                },
            }
            row = FactorBundle(
                id=uuid4(),
                owner_id=owner_id,
                name=body.name,
                definition=definition,
                content_sha256=digest(definition),
            )
            session.add(row)
            session.flush()
            for i, member in enumerate(body.members):
                session.add(
                    FactorBundleMember(
                        owner_id=owner_id,
                        bundle_id=row.id,
                        factor_version_id=member.factor_version_id,
                        definition_sha256=member.definition_sha256,
                        ordinal=i,
                    )
                )
            save_submission(session, owner_id, "create_bundle", key, sha, row.id)
            result = public(row)
    return result


def create_comparison(session, owner_id, body, key, settings):
    with publication(session, owner_id, settings, peak_bytes=0, peak_files=0):
        old, sha = submission(
            session, owner_id, "create_comparison", key, body.model_dump(mode="json")
        )
        if old:
            result = public(owned(session, FactorComparison, owner_id, old.resource_id))
        else:
            left = owned(session, FactorVersion, owner_id, body.left.version_id)
            right = owned(session, FactorVersion, owner_id, body.right.version_id)
            for version in (left, right):
                verify_version(session, owner_id, version, settings)
            snapshots = []
            for side in (body.left, body.right):
                snapshot = None
                if side.snapshot_id:
                    snapshot, _, _ = verified_snapshot(
                        session, owner_id, side.snapshot_id, settings
                    )
                    if snapshot.factor_version_id != side.version_id:
                        raise DomainError("not_found", "Factor snapshot not found", 404)
                    expose_snapshot(session, owner_id, snapshot, "comparison")
                snapshots.append(snapshot)
            aligned = (
                all(snapshots)
                and snapshots[0].dataset_content_sha256
                == snapshots[1].dataset_content_sha256
            )
            same_unit = (
                left.definition["output_unit"] == right.definition["output_unit"]
                and left.definition["preprocessing"]
                == right.definition["preprocessing"]
            )
            definition = {
                **body.model_dump(mode="json"),
                "left_definition": left.definition,
                "right_definition": right.definition,
                "left_code": left.code,
                "right_code": right.code,
                "observation_alignment": {
                    "status": "aligned" if aligned else "definition_only",
                    "reasons": [] if aligned else ["exact_common_dataset_not_bound"],
                },
                "unit_scale_comparability": {
                    "status": "comparable" if same_unit else "incompatible",
                    "reasons": [] if same_unit else ["unit_or_preprocessing_differs"],
                },
                "controlled_protocol_comparability": {
                    "status": "not_evaluated",
                    "reasons": ["no_controlled_experiment"],
                },
                "numeric_delta": None,
                "delta_reason": "definition_only_no_paired_values",
            }
            row = FactorComparison(
                id=uuid4(),
                owner_id=owner_id,
                left_version_id=left.id,
                right_version_id=right.id,
                definition=definition,
                content_sha256=digest(definition),
            )
            session.add(row)
            session.flush()
            save_submission(session, owner_id, "create_comparison", key, sha, row.id)
            result = public(row)
    return result


def snapshot_values(
    session,
    owner_id,
    snapshot_id,
    settings,
    *,
    listing_id=None,
    start=None,
    end=None,
    offset=0,
    limit=100,
    admit=True,
):
    from backend.app.storage import safe_path
    from packages.factors.storage import FactorStorageError, read_values_page

    if start is not None and end is not None and start >= end:
        raise DomainError(
            "factor_query_window",
            "Factor query window must be nonempty and half-open",
            422,
        )
    snapshot, _manifest, links = verified_snapshot(
        session, owner_id, snapshot_id, settings
    )
    partitions = [link for link in links if link.role == "values_partition"]
    # Initial trusted profile has one complete canonical partition. Never give an
    # inaccurate count or concatenate independently paged fragments.
    if len(partitions) != 1:
        raise DomainError(
            "factor_query_capacity",
            "Snapshot requires an unsupported partition query profile",
            409,
        )
    obj = owned(session, FactorObject, owner_id, partitions[0].object_id)
    if snapshot.local_verification.get("kind") not in {
        "fixed_fixture_recomputed",
        "declared_or_unknown",
    }:
        raise DomainError(
            "factor_query_unavailable",
            "Snapshot lacks locally admitted query evidence",
            409,
        )
    try:
        page = read_values_page(
            safe_path(settings.storage_root, obj.storage_key),
            obj.sha256,
            offset=offset,
            limit=limit,
            listing_id=listing_id,
            start=start,
            end=end,
        )
    except FactorStorageError as exc:
        raise DomainError(
            exc.code, "Factor values failed bounded integrity verification", 409
        ) from None
    result = {
        "snapshot_id": snapshot.id,
        "manifest_sha256": snapshot.manifest_sha256,
        "output_sha256": snapshot.output_digest,
        **page,
        "scope": {"listing_id": listing_id, "start": start, "end": end},
        "downsampled": False,
    }
    if admit:
        expose_snapshot(session, owner_id, snapshot, "values")
        session.commit()
    return result


def snapshot_sample(session, owner_id, snapshot_id, settings, listing_id, session_date):
    from packages.factors.package import _json as bounded_json

    snapshot, _, links = verified_snapshot(session, owner_id, snapshot_id, settings)
    matches = []
    for link in links:
        if link.role == "calculation_samples":
            obj = owned(session, FactorObject, owner_id, link.object_id)
            payload = bounded_json(
                read_verified(
                    settings.storage_root, obj.storage_key, obj.sha256, 1048576
                )
            )
            samples = (
                payload.get("samples", []) if isinstance(payload, dict) else payload
            )
            matches.extend(
                s
                for s in samples
                if s.get("listing_id") == listing_id
                and s.get("session_date") == session_date.isoformat()
            )
    origin_evidence = False
    if not matches and snapshot.source_claims.get("import_envelope_id"):
        from backend.app.factor_import_models import FactorImportEnvelope

        envelope = owned(
            session,
            FactorImportEnvelope,
            owner_id,
            UUID(snapshot.source_claims["import_envelope_id"]),
        )
        origin = next(
            (
                m
                for m in envelope.origin_payload.get("snapshots", [])
                if m.get("snapshot_id")
                == snapshot.source_claims.get("origin_snapshot_id")
            ),
            None,
        )
        for ref in (origin or {}).get("objects", []):
            if ref.get("role") != "calculation_samples":
                continue
            binding = next(
                (
                    b
                    for b in envelope.local_bindings.get("objects", [])
                    if b.get("source_path") == f"objects/{ref['sha256']}.json"
                    and b.get("sha256") == ref["sha256"]
                ),
                None,
            )
            if binding is None:
                raise DomainError(
                    "factor_import_integrity",
                    "Retained source sample mapping is missing",
                    409,
                )
            obj = owned(session, FactorObject, owner_id, UUID(binding["object_id"]))
            if obj.sha256 != ref["sha256"]:
                raise DomainError(
                    "factor_import_integrity",
                    "Retained source sample hash differs",
                    409,
                )
            payload = bounded_json(
                read_verified(
                    settings.storage_root, obj.storage_key, obj.sha256, 1048576
                )
            )
            if not isinstance(payload, dict) or not isinstance(
                payload.get("samples"), list
            ):
                raise DomainError(
                    "factor_import_integrity",
                    "Retained source samples are malformed",
                    409,
                )
            matches.extend(
                item
                for item in payload["samples"]
                if isinstance(item, dict)
                and item.get("listing_id") == listing_id
                and item.get("session_date") == session_date.isoformat()
            )
            origin_evidence = bool(matches)
    expose_snapshot(session, owner_id, snapshot, "sample")
    session.commit()
    return {
        "snapshot_id": snapshot_id,
        "listing_id": listing_id,
        "session_date": session_date,
        "evidence_status": "available" if matches else "unavailable",
        "identity_mode": "origin_evidence" if origin_evidence else "local",
        "origin_snapshot_id": snapshot.source_claims.get("origin_snapshot_id")
        if origin_evidence
        else None,
        "samples": matches,
    }


def get_comparison(session, owner_id, comparison_id, settings):
    row = owned(session, FactorComparison, owner_id, comparison_id)
    for side in ("left", "right"):
        snapshot_id = row.definition[side].get("snapshot_id")
        if snapshot_id:
            snapshot, _, _ = verified_snapshot(
                session, owner_id, UUID(snapshot_id), settings
            )
            expose_snapshot(session, owner_id, snapshot, "comparison_detail")
    result = public(row)
    session.commit()
    return result


def comparison_values(
    session, owner_id, comparison_id, settings, *, offset=0, limit=100
):
    row = owned(session, FactorComparison, owner_id, comparison_id)
    definition = row.definition
    left_id = definition["left"].get("snapshot_id")
    right_id = definition["right"].get("snapshot_id")
    if (
        not left_id
        or not right_id
        or definition["observation_alignment"]["status"] != "aligned"
    ):
        return {
            "comparison_id": row.id,
            "data": [],
            "count": 0,
            "scope": "definition_only",
            "observation_alignment": definition["observation_alignment"],
            "unit_scale_comparability": definition["unit_scale_comparability"],
            "controlled_protocol_comparability": definition[
                "controlled_protocol_comparability"
            ],
        }
    # Bilateral fixture profile is capped at 500 rows per side, not a silent sample.
    sides = []
    for snapshot_id in (UUID(left_id), UUID(right_id)):
        page = snapshot_values(
            session, owner_id, snapshot_id, settings, limit=500, admit=False
        )
        if page["count"] > 500:
            raise DomainError(
                "factor_comparison_capacity",
                "Bilateral comparison exceeds fixed profile",
                409,
            )
        sides.append(
            {
                (str(r["session_date"]), r["listing_id"], str(r["event_time"])): r
                for r in page["data"]
            }
        )
    comparable = definition["unit_scale_comparability"]["status"] == "comparable"
    rows = []
    for key in sorted(set(sides[0]) | set(sides[1])):
        left = sides[0].get(key)
        right = sides[1].get(key)
        valid = (
            left
            and right
            and left["value_state"] == "valid"
            and right["value_state"] == "valid"
        )
        rows.append(
            {
                "session_date": key[0],
                "listing_id": key[1],
                "event_time": key[2],
                "left": left,
                "right": right,
                "delta": right["value"] - left["value"]
                if valid and comparable
                else None,
                "delta_reason": None
                if valid and comparable
                else "incompatible_unit_scale"
                if not comparable
                else "missing_or_unavailable_observation",
            }
        )
    for snapshot_id in (UUID(left_id), UUID(right_id)):
        expose_snapshot(
            session,
            owner_id,
            owned(session, FeatureSnapshot, owner_id, snapshot_id),
            "comparison_values",
        )
    session.commit()
    return {
        "comparison_id": row.id,
        "data": rows[offset : offset + limit],
        "count": len(rows),
        "scope": "full_bilateral_fixture",
        "observation_alignment": definition["observation_alignment"],
        "unit_scale_comparability": definition["unit_scale_comparability"],
        "controlled_protocol_comparability": definition[
            "controlled_protocol_comparability"
        ],
    }


def export_package(session, owner_id, version_id, body, key, settings):
    from packages.factors.package import encode_package

    # The maximum archive plus atomic temporary is reserved before any write.
    with publication(
        session, owner_id, settings, peak_bytes=20_000_000 + 8192
    ) as deadline:
        old, sha = submission(
            session, owner_id, f"export:{version_id}", key, body.model_dump(mode="json")
        )
        if old:
            package = owned(session, FactorPackage, owner_id, old.resource_id)
            obj = owned(session, FactorObject, owner_id, package.archive_object_id)
            read_verified(
                settings.storage_root, obj.storage_key, obj.sha256, 10_000_000
            )
        else:
            version = owned(session, FactorVersion, owner_id, version_id)
            verify_version(session, owner_id, version, settings)
            if body.include_values and not body.snapshot_ids:
                raise DomainError(
                    "factor_snapshot_required",
                    "Value export requires exact snapshots",
                    422,
                )
            objects = {f"objects/{version.code_sha256}.py": version.code.encode()}
            manifests = []
            source_datasets = {}
            for snapshot_id in body.snapshot_ids:
                snapshot, manifest, links = verified_snapshot(
                    session, owner_id, snapshot_id, settings
                )
                if snapshot.factor_version_id != version.id:
                    raise DomainError("not_found", "Factor snapshot not found", 404)
                manifests.append(manifest.model_dump(mode="json"))
                objects[f"objects/{snapshot.manifest_sha256}.json"] = canonical_bytes(
                    manifest
                )
                if body.include_values:
                    for link in links:
                        obj = owned(session, FactorObject, owner_id, link.object_id)
                        objects[
                            f"objects/{obj.sha256}.{OBJECT_TYPES[obj.object_kind][0]}"
                        ] = read_verified(
                            settings.storage_root,
                            obj.storage_key,
                            obj.sha256,
                            10_000_000,
                        )
                if body.include_values:
                    from backend.app.models import DatasetVersion

                    dataset = owned(
                        session, DatasetVersion, owner_id, snapshot.dataset_version_id
                    )
                    if (
                        dataset.manifest.get("schema_version")
                        == "mineagent.factor-fixture-dataset.v1"
                    ):
                        manifest_bytes = read_verified(
                            settings.storage_root,
                            f"{dataset.storage_key}/manifest.json",
                            dataset.content_sha256,
                            1048576,
                        )
                        source_refs = []
                        objects[f"objects/{dataset.content_sha256}.json"] = (
                            manifest_bytes
                        )
                        # Only the one immutable fixed-fixture input profile is
                        # exportable here, never an arbitrary source path/glob.
                        if set(dataset.manifest.get("files", {})) != {
                            "observations.json"
                        }:
                            raise DomainError(
                                "factor_integrity",
                                "Fixture source inventory differs",
                                409,
                            )
                        input_hash = dataset.manifest["files"]["observations.json"]
                        input_bytes = read_verified(
                            settings.storage_root,
                            f"{dataset.storage_key}/observations.json",
                            input_hash,
                            1048576,
                        )
                        objects[f"objects/{input_hash}.json"] = input_bytes
                        source_refs.append(
                            {
                                "name": "observations.json",
                                "sha256": input_hash,
                                "object_path": f"objects/{input_hash}.json",
                                "size_bytes": len(input_bytes),
                            }
                        )
                        source_datasets[str(dataset.id)] = {
                            "dataset_version_id": str(dataset.id),
                            "content_sha256": dataset.content_sha256,
                            "manifest": dataset.manifest,
                            "manifest_object_path": f"objects/{dataset.content_sha256}.json",
                            "files": source_refs,
                            "market": dataset.market,
                            "start_date": dataset.start_date.isoformat(),
                            "end_date": dataset.end_date.isoformat(),
                            "source": "synthetic_fixture",
                            "is_synthetic": True,
                        }
                expose_snapshot(session, owner_id, snapshot, "export")
            evaluations = []
            for evaluation_id in body.evaluation_ids:
                evaluation = owned(session, FactorEvaluation, owner_id, evaluation_id)
                verify_evaluation(evaluation)
                if evaluation.factor_version_id != version.id:
                    raise DomainError("not_found", "Factor evaluation not found", 404)
                evaluations.append(
                    {
                        "id": str(evaluation.id),
                        "factor_version_id": str(evaluation.factor_version_id),
                        "feature_snapshot_id": str(evaluation.feature_snapshot_id)
                        if evaluation.feature_snapshot_id
                        else None,
                        "environment_digest": evaluation.environment_digest,
                        "error_code": evaluation.error_code,
                        "evidence_binding_status": "selected_snapshot"
                        if evaluation.feature_snapshot_id in body.snapshot_ids
                        else "unresolved_source_claim",
                        "kind": evaluation.kind,
                        "protocol": evaluation.protocol,
                        "protocol_sha256": evaluation.protocol_sha256,
                        "status": evaluation.status,
                        "result_payload": evaluation.result_payload,
                        "result_sha256": evaluation.result_sha256,
                    }
                )
                if evaluation.feature_snapshot_id:
                    expose_snapshot(
                        session,
                        owner_id,
                        owned(
                            session,
                            FeatureSnapshot,
                            owner_id,
                            evaluation.feature_snapshot_id,
                        ),
                        "export",
                    )
            definition = {
                "origin_namespace": f"declared-owner-{owner_id}",
                "factor_id": str(version.factor_id),
                "version_id": str(version.id),
                "parent_version_id": str(version.parent_version_id)
                if version.parent_version_id
                else None,
                "ordinal": version.ordinal,
                "definition": version.definition,
                "definition_sha256": version.definition_sha256,
                "code_sha256": version.code_sha256,
                "source_provenance": version.source_provenance,
                "snapshots": manifests,
                "source_datasets": list(source_datasets.values()),
                "evaluations": evaluations,
                "include_values": body.include_values,
                "history_completeness": "unknown",
                "exposure_claims": inherited_exposure_claims(
                    session, owner_id, [version.id], deadline
                ),
                "execution_binding_status": "unresolved",
            }
            data = encode_package(definition, objects)
            obj = publish_object(session, owner_id, "package", data, settings, deadline)
            package = FactorPackage(
                id=uuid4(),
                owner_id=owner_id,
                factor_version_id=version.id,
                archive_object_id=obj.id,
                archive_sha256=obj.sha256,
                definition=definition,
            )
            session.add(package)
            session.flush()
            save_submission(
                session, owner_id, f"export:{version_id}", key, sha, package.id
            )
        result = {
            **public(package),
            "download_url": f"/api/v1/factor-packages/{package.id}/download",
        }
    return result


def download_package(session, owner_id, package_id, settings):
    package = owned(session, FactorPackage, owner_id, package_id)
    obj = owned(session, FactorObject, owner_id, package.archive_object_id)
    data = read_verified(
        settings.storage_root, obj.storage_key, package.archive_sha256, 10_000_000
    )
    for manifest in package.definition["snapshots"]:
        snapshot = owned(
            session, FeatureSnapshot, owner_id, UUID(manifest["snapshot_id"])
        )
        expose_snapshot(session, owner_id, snapshot, "download")
    session.commit()
    return data


def create_fixed_fixture(session, owner_id, body, key, settings):
    """Recompute one server-owned fixture; submitted source is never executed."""
    from datetime import date

    from backend.app.factor_schemas import FactorVersionCreate
    from backend.app.models import DatasetVersion
    from packages.factors.contracts import ObjectRef
    from packages.factors.fixtures import (
        demo_fixture,
        demo_inputs,
        fixed_factor_code,
        fixture_definition,
    )
    from packages.factors.storage import write_partition

    if settings.data_provider_mode != "fixture":
        raise DomainError(
            "factor_fixture_disabled",
            "Fixed factor materialization requires explicit fixture mode",
            403,
        )
    with publication(
        session, owner_id, settings, peak_bytes=8 * 1024 * 1024, peak_files=20
    ) as deadline:
        old, request_hash = submission(
            session, owner_id, "fixed_fixture", key, body.model_dump(mode="json")
        )
        if old:
            snapshot, _, _ = verified_snapshot(
                session, owner_id, old.resource_id, settings
            )
            version = owned(
                session, FactorVersion, owner_id, snapshot.factor_version_id
            )
            verify_version(session, owner_id, version, settings)
            factor = owned(session, Factor, owner_id, version.factor_id)
            evaluation = session.scalar(
                select(FactorEvaluation).where(
                    FactorEvaluation.owner_id == owner_id,
                    FactorEvaluation.feature_snapshot_id == snapshot.id,
                    FactorEvaluation.kind == "materialization",
                )
            )
        else:
            if body.factor_id:
                factor = owned(session, Factor, owner_id, body.factor_id)
            else:
                factor = Factor(
                    id=uuid4(),
                    owner_id=owner_id,
                    name=body.name,
                    category="synthetic_fixture",
                )
                session.add(factor)
                session.flush()
            version_body = FactorVersionCreate(
                parent_version_id=body.parent_version_id,
                code=fixed_factor_code(body.kind),
                definition=fixture_definition(
                    body.kind,
                    lookback_sessions=body.lookback_sessions,
                    lag_sessions=body.lag_sessions,
                ),
                change_note="Server-owned fixed fixture recomputation; not live data or Qlib",
            )
            version = _new_version(
                session,
                owner_id,
                factor,
                version_body,
                settings,
                deadline,
                source_kind="fixture",
            )
            _calendar, observations = demo_inputs()
            inputs = canonical_bytes([o.model_dump(mode="json") for o in observations])
            input_obj = publish_object(
                session, owner_id, "native_evidence", inputs, settings, deadline
            )
            dataset_manifest = {
                "schema_version": "mineagent.factor-fixture-dataset.v1",
                "is_synthetic": True,
                "source": "synthetic_fixture",
                "market": "US",
                "files": {"observations.json": input_obj.sha256},
                "listings": [
                    {
                        "listing_id": listing,
                        "symbol": listing.rsplit(":", 1)[-1],
                        "market": "US",
                        "exchange": "XNAS",
                        "currency": "USD",
                    }
                    for listing in sorted({o.listing_id for o in observations})
                ],
                "evaluation_compatibility": {
                    "compatible": False,
                    "reasons": ["factor_fixture_only"],
                },
            }
            dataset_bytes = canonical_bytes(dataset_manifest)
            dataset_hash = hashlib.sha256(dataset_bytes).hexdigest()
            dataset = session.scalar(
                select(DatasetVersion).where(
                    DatasetVersion.owner_id == owner_id,
                    DatasetVersion.source == "mineagent_fixed_factor_fixture",
                    DatasetVersion.content_sha256 == dataset_hash,
                )
            )
            if dataset is None:
                dataset_id = uuid4()
                dataset_key = f"factors/{owner_id}/datasets/{dataset_id}"
                publish_bytes(
                    Path(settings.storage_root) / dataset_key / "observations.json",
                    inputs,
                    sha256=input_obj.sha256,
                    durable_root=Path(settings.storage_root),
                )
                check_deadline(deadline)
                publish_bytes(
                    Path(settings.storage_root) / dataset_key / "manifest.json",
                    dataset_bytes,
                    sha256=dataset_hash,
                    durable_root=Path(settings.storage_root),
                )
                check_deadline(deadline)
                dataset = DatasetVersion(
                    id=dataset_id,
                    owner_id=owner_id,
                    name="MineAgent fixed-factor synthetic panel",
                    market="US",
                    start_date=date(2024, 3, 6),
                    end_date=date(2024, 3, 16),
                    source="mineagent_fixed_factor_fixture",
                    is_synthetic=True,
                    content_sha256=dataset_hash,
                    storage_key=dataset_key,
                    manifest=dataset_manifest,
                )
                session.add(dataset)
                session.flush()
            else:
                read_verified(
                    settings.storage_root,
                    f"{dataset.storage_key}/manifest.json",
                    dataset_hash,
                    1048576,
                )
                read_verified(
                    settings.storage_root,
                    f"{dataset.storage_key}/observations.json",
                    input_obj.sha256,
                    1048576,
                )
            materialized = demo_fixture(
                body.kind,
                factor_version_id=version.id,
                dataset_version_id=dataset.id,
                dataset_content_sha256=dataset.content_sha256,
                lookback_sessions=body.lookback_sessions,
                lag_sessions=body.lag_sessions,
            )
            if (
                materialized.manifest.input.dataset.content_sha256
                != dataset.content_sha256
            ):
                raise DomainError(
                    "factor_fixture_integrity", "Fixture dataset digest differs", 409
                )
            # The temporary is charged and fenced. A process crash leaves its
            # operation directory as a recovery barrier for the next publisher.
            staging = (
                Path(settings.storage_root)
                / "factors"
                / str(owner_id)
                / "staging"
                / str(uuid4())
            )
            stage_fd = _prepare_directory(staging, Path(settings.storage_root))
            os.close(stage_fd)
            partition_path = staging / "values.parquet"
            try:
                check_deadline(deadline)
                partition = write_partition(
                    materialized.rows,
                    partition_path,
                    seconds=max(0.001, deadline - time.monotonic() - 2),
                )
                if partition.size_bytes > 2 * 1024 * 1024:
                    raise DomainError(
                        "factor_capacity",
                        "Fixed fixture exceeded its measured publication profile",
                        409,
                    )
                content = partition_path.read_bytes()
                partition_obj = publish_object(
                    session, owner_id, "values_partition", content, settings, deadline
                )
            finally:
                # Only these trusted synchronous writers ran, and have returned.
                if partition_path.exists():
                    partition_path.unlink()
                staging.rmdir()
            samples = canonical_bytes(
                {"samples": [s.model_dump(mode="json") for s in materialized.samples]}
            )
            sample_obj = publish_object(
                session, owner_id, "calculation_samples", samples, settings, deadline
            )
            code_obj = owned(session, FactorObject, owner_id, version.code_object_id)
            objects = [code_obj, partition_obj, sample_obj, input_obj]
            refs = tuple(
                ObjectRef(
                    role=o.object_kind,
                    ordinal=0,
                    sha256=o.sha256,
                    size_bytes=o.size_bytes,
                    name=f"{o.sha256}.{OBJECT_TYPES[o.object_kind][0]}",
                    rows=materialized.manifest.row_count
                    if o.object_kind == "values_partition"
                    else 0,
                )
                for o in objects
            )
            manifest = materialized.manifest.model_copy(update={"objects": refs})
            manifest_obj = publish_object(
                session,
                owner_id,
                "snapshot_manifest",
                canonical_bytes(manifest),
                settings,
                deadline,
            )
            snapshot = FeatureSnapshot(
                id=manifest.snapshot_id,
                owner_id=owner_id,
                factor_version_id=version.id,
                dataset_version_id=dataset.id,
                dataset_content_sha256=dataset.content_sha256,
                definition_sha256=version.definition_sha256,
                input_digest=manifest.input_digest,
                output_digest=manifest.output_digest,
                manifest_object_id=manifest_obj.id,
                manifest_sha256=manifest_obj.sha256,
                manifest=manifest.model_dump(mode="json"),
                environment_digest=manifest.input.environment_sha256,
                row_count=manifest.row_count,
                valid_count=manifest.valid_count,
                missing_count=manifest.missing_count,
                identity_mode="local",
                local_verification=materialized.verification.model_dump(mode="json"),
                source_claims={
                    "source": "synthetic_fixture",
                    "not_live_market_validation": True,
                },
            )
            session.add(snapshot)
            session.flush()
            for obj in objects:
                session.add(
                    FeatureSnapshotObject(
                        owner_id=owner_id,
                        snapshot_id=snapshot.id,
                        object_id=obj.id,
                        role=obj.object_kind,
                        ordinal=0,
                    )
                )
            protocol = {
                "schema_version": "mineagent.factor-materialization.v1",
                "profile": "fixed-factors.v1",
                "input_digest": manifest.input_digest,
            }
            result = {
                "verification": "fixed_fixture_recomputed",
                "snapshot_id": str(snapshot.id),
                "output_digest": manifest.output_digest,
                "row_count": manifest.row_count,
                "valid_count": manifest.valid_count,
                "missing_count": manifest.missing_count,
            }
            evaluation = FactorEvaluation(
                id=uuid4(),
                owner_id=owner_id,
                factor_version_id=version.id,
                feature_snapshot_id=snapshot.id,
                kind="materialization",
                protocol=protocol,
                protocol_sha256=digest(protocol),
                status="succeeded",
                result_payload=result,
                result_sha256=digest(result),
                environment_digest=snapshot.environment_digest,
            )
            session.add(evaluation)
            session.flush()
            save_submission(
                session, owner_id, "fixed_fixture", key, request_hash, snapshot.id
            )
        expose_snapshot(session, owner_id, snapshot, "materialization")
        result = {
            "factor": public(factor),
            "version": public(version),
            "snapshot": public(snapshot),
            "evaluation": public(evaluation),
        }
    return result
