"""Bounded raw ingress and queued import admission, never API native parsing."""

import asyncio
import hashlib
import json
import re
import time
from uuid import uuid4

from sqlalchemy import select

from backend.app.factor_import_models import FactorImportAdmission, FactorImportEnvelope
from backend.app.factor_models import FactorObject
from backend.app.factor_services import (
    enabled,
    owned,
    public,
    publication,
    publish_object,
)
from backend.app.services import DomainError
from backend.app.storage import read_verified
from packages.factors.contracts import digest
from packages.factors.package import FactorPackageError, decode_package


class IngressRefusal(Exception):
    def __init__(self, code, status):
        self.code, self.status = code, status


class FactorImportIngressMiddleware:
    """Install outside the general body accumulator; never spool or hold DB locks.

    Two slots include buffered request lifetime, not just individual receive calls.
    The ordinary buffer and framework may each copy a body, so deployment must
    budget at least four maximum bodies per API process plus parser-independent
    application overhead. This guard does not increase the existing upload cap.
    """

    def __init__(self, app, max_bytes=10_000_000, max_inflight=2, read_seconds=15):
        self.app = app
        self.max_bytes = min(max_bytes, 10_000_000)
        self.max_inflight = max_inflight
        self.read_seconds = read_seconds
        self.inflight = 0

    async def reject(self, send, code, status):
        body = json.dumps(
            {
                "code": code,
                "message": "Factor import body was not admitted",
                "details": None,
                "correlation_id": str(uuid4()),
            }
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != "/api/v1/factor-packages/import"
        ):
            return await self.app(scope, receive, send)
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        media = headers.get(b"content-type", b"").split(b";", 1)[0].strip().lower()
        if (
            media not in {b"application/zip", b"application/octet-stream"}
            or headers.get(b"content-encoding", b"identity").lower() != b"identity"
        ):
            return await self.reject(send, "factor_import_media_type", 415)
        if self.inflight >= self.max_inflight:
            return await self.reject(send, "factor_import_busy", 503)
        self.inflight += 1
        deadline = time.monotonic() + self.read_seconds
        total = 0

        async def bounded_receive():
            nonlocal total
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise IngressRefusal("factor_import_body_timeout", 408)
            try:
                message = await asyncio.wait_for(receive(), timeout=remaining)
            except TimeoutError:
                raise IngressRefusal("factor_import_body_timeout", 408) from None
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > self.max_bytes:
                    raise IngressRefusal("factor_import_body_size", 413)
            return message

        try:
            await self.app(scope, bounded_receive, send)
        except IngressRefusal as exc:
            await self.reject(send, exc.code, exc.status)
        finally:
            self.inflight -= 1


def import_available(settings):
    return bool(
        settings.factor_library_enabled
        and getattr(settings, "factor_import_enabled", False)
        and re.fullmatch(
            r"sha256:[a-f0-9]{64}",
            getattr(settings, "factor_parser_image_id", "") or "",
        )
    )


def admit_import(session, owner_id, archive, key, settings):
    enabled(settings)
    if not import_available(settings):
        raise DomainError(
            "factor_import_unavailable",
            "Isolated factor import admission is not enabled",
            503,
        )
    if not archive or len(archive) > settings.max_upload_bytes:
        raise DomainError(
            "factor_import_body_size", "Archive exceeds bounded upload profile", 413
        )
    if not key or not re.fullmatch(r"[!-~]{1,128}", key):
        raise DomainError(
            "invalid_idempotency_key",
            "A bounded Idempotency-Key header is required",
            422,
        )
    archive_hash = hashlib.sha256(archive).hexdigest()
    request_hash = digest(
        {
            "archive_sha256": archive_hash,
            "parser_image_id": settings.factor_parser_image_id,
            "profile": "factor-import.v1",
        }
    )
    # This admission stores opaque ZIP_STORED bytes only. Decoded/native output
    # is reserved separately by the supervised worker before any staged write.
    with publication(
        session, owner_id, settings, peak_bytes=2 * len(archive) + 8192
    ) as deadline:
        previous = session.scalar(
            select(FactorImportAdmission).where(
                FactorImportAdmission.owner_id == owner_id,
                FactorImportAdmission.idempotency_key == key,
            )
        )
        if previous:
            if previous.request_sha256 != request_hash:
                raise DomainError(
                    "idempotency_conflict",
                    "Import key has different content or parser profile",
                    409,
                )
            obj = owned(session, FactorObject, owner_id, previous.archive_object_id)
            read_verified(
                settings.storage_root,
                obj.storage_key,
                previous.archive_sha256,
                settings.max_upload_bytes,
            )
            result = public(previous)
        else:
            try:
                decode_package(archive)
            except FactorPackageError as exc:
                raise DomainError(
                    exc.code, "Factor archive failed bounded package validation", 422
                ) from None
            obj = publish_object(
                session, owner_id, "package", archive, settings, deadline
            )
            admission = FactorImportAdmission(
                id=uuid4(),
                owner_id=owner_id,
                archive_object_id=obj.id,
                archive_sha256=archive_hash,
                idempotency_key=key,
                request_sha256=request_hash,
                parser_image_id=settings.factor_parser_image_id,
                status="queued",
                generation=0,
            )
            session.add(admission)
            session.flush()
            result = public(admission)
    return result


def import_status(session, owner_id, admission_id, settings):
    enabled(settings)
    row = owned(session, FactorImportAdmission, owner_id, admission_id)
    result = public(row)
    # Lease/runtime control is not a client capability.
    result.pop("lease_token", None)
    if row.result_envelope_id:
        envelope = owned(
            session, FactorImportEnvelope, owner_id, row.result_envelope_id
        )
        result["local_bindings"] = envelope.local_bindings
        result["exposure_history"] = envelope.exposure_history
        result["history_completeness"] = "unknown"
    return result


def admit_import_exposures(
    session, owner_id, payload, bindings, archive_sha, settings, deadline=None
):
    """Conservative owner-wide admissions from declared imported exposure claims.

    Source UUIDs/display names do not establish a local identity. Exact retained
    content plus complete listing identities are required; unresolved evidence
    stays unknown and remains in the immutable import envelope.
    """
    from datetime import date

    from backend.app.experiment_services import _identities, record_exposure
    from backend.app.factor_services import check_deadline
    from backend.app.models import DatasetVersion
    from packages.factors.contracts import DateWindow, canonical_bytes
    from packages.factors.package import _json as bounded_json

    claims = payload.get("exposure_claims", [])
    if (
        not isinstance(claims, list)
        or len(claims) > 256
        or len(canonical_bytes(claims)) > 262144
    ):
        raise DomainError(
            "factor_exposure_capacity",
            "Imported exposure claims exceed bounded profile",
            422,
        )
    result = {"admitted": [], "unresolved": [], "history_completeness": "unknown"}
    for claim in claims:
        if deadline is not None:
            check_deadline(deadline)
        claim_hash = digest(claim)

        def unresolved(reason, claim_hash=claim_hash):
            result["unresolved"].append({"claim_sha256": claim_hash, "reason": reason})

        if not isinstance(claim, dict) or not isinstance(claim.get("evidence"), dict):
            unresolved("unsupported_claim_shape")
            continue
        evidence = claim["evidence"]
        if digest(evidence) != claim.get("evidence_sha256"):
            unresolved("source_evidence_hash_mismatch")
            continue
        content_hash = claim.get("dataset_content_sha256")
        if not isinstance(content_hash, str) or not re.fullmatch(
            r"[a-f0-9]{64}", content_hash
        ):
            unresolved("source_dataset_identity_missing")
            continue
        try:
            window = DateWindow(
                start=date.fromisoformat(evidence["start_date"]),
                end=date.fromisoformat(evidence["end_date"]),
            )
        except (KeyError, TypeError, ValueError):
            unresolved("source_exposure_window_invalid")
            continue
        # Do not guess a range from output dates: label/fitting/selection evidence
        # may legitimately extend beyond the imported materialization window.
        dataset = session.scalar(
            select(DatasetVersion)
            .where(
                DatasetVersion.owner_id == owner_id,
                DatasetVersion.content_sha256 == content_hash,
            )
            .order_by(DatasetVersion.id)
            .limit(1)
        )
        if dataset is None:
            unresolved("local_dataset_content_unresolved")
            continue
        raw = read_verified(
            settings.storage_root,
            f"{dataset.storage_key}/manifest.json",
            content_hash,
            1048576,
        )
        if bounded_json(raw) != dataset.manifest:
            raise DomainError(
                "factor_import_integrity",
                "Local dataset manifest registration differs",
                409,
            )
        local_identities = _identities(session, dataset)
        source_identities = evidence.get("identities")
        if not isinstance(source_identities, list) or sorted(
            canonical_bytes(i) for i in source_identities
        ) != sorted(canonical_bytes(i) for i in local_identities):
            unresolved("source_listing_identity_mapping_unresolved")
            continue
        mapping = {
            "dataset_content_sha256": content_hash,
            "local_dataset_version_id": str(dataset.id),
            "identities": local_identities,
        }
        mapping_hash = digest(mapping)
        source_key = f"factor:import:{archive_sha}:{claim_hash}:{mapping_hash[:32]}"
        exposure = record_exposure(
            session,
            owner_id=owner_id,
            dataset=dataset,
            window=window,
            purpose="imported_factor_research_exposure",
            source_key=source_key,
            provenance={
                "source_archive_sha256": archive_sha,
                "claim_sha256": claim_hash,
                "source_evidence_sha256": claim["evidence_sha256"],
                "mapping_sha256": mapping_hash,
                "source_purpose": str(evidence.get("purpose", "unknown"))[:80],
                "verification": "declared_imported_claim",
                "history_completeness": "unknown",
            },
        )
        result["admitted"].append(
            {
                "claim_sha256": claim_hash,
                "local_exposure_id": str(exposure.id),
                "dataset_version_id": str(dataset.id),
                "dataset_content_sha256": content_hash,
                "mapping_sha256": mapping_hash,
            }
        )
    if not claims:
        result["unresolved"].append(
            {"claim_sha256": None, "reason": "source_history_not_supplied"}
        )
    bindings["exposure_admissions"] = result
    return result
