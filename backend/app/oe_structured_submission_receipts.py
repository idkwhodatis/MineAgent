"""Exact authenticated027 request replay; never a rerun or process grant."""

from sqlalchemy import select

from backend.app.models import ResearchRun
from backend.app.oe_structured_identity import decode_spec, fail
from backend.app.oe_structured_models import OEStructuredSubmission
from backend.app.oe_structured_provider_submission import (
    ProviderStructuredSearchRequest,
    ProviderStructuredSearchSubmit,
    ProviderStructuredSubmissionReceipt,
)
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.openevolve_structured.candidate import _raw_fields


def validate_key(key):
    if (
        type(key) is not str
        or not 1 <= len(key) <= 128
        or any(not 33 <= ord(char) <= 126 for char in key)
    ):
        raise DomainError(
            "invalid_idempotency_key",
            "Printable ASCII Idempotency-Key without spaces required",
            422,
        )
    return key


def existing_receipt(session, owner_id, key, submitted):
    """Read BEFORE current runtime/profile readiness; exact accepted replay survives stop."""
    validate_key(key)
    if type(submitted) is not ProviderStructuredSearchSubmit:
        fail()
    submitted = ProviderStructuredSearchSubmit.model_validate(_raw_fields(submitted))
    row = session.scalar(
        select(OEStructuredSubmission).filter_by(owner_id=owner_id, idempotency_key=key)
    )
    if row is None:
        if (
            session.scalar(
                select(ResearchRun).filter_by(owner_id=owner_id, idempotency_key=key)
            )
            is not None
        ):
            raise DomainError(
                "idempotency_conflict", "Key already identifies another request", 409
            )
        return None
    try:
        request = ProviderStructuredSearchRequest.model_validate_json(
            row.request_canonical
        )
        if (
            row.request_canonical != canonical_bytes(request).decode("ascii")
            or row.request_sha256 != request.sha256
            or row.origin != "authenticated_user_submission"
        ):
            fail()
        if (
            request != submitted.request()
            or row.preflight_sha256 != submitted.preflight_sha256
        ):
            raise DomainError(
                "idempotency_conflict", "Key already identifies another request", 409
            )
        run = session.get(ResearchRun, row.run_id)
        if run is None or (run.owner_id, run.idempotency_key) != (owner_id, key):
            fail()
        spec, config = decode_spec(session, run)
        if (
            spec.submission_id != row.id
            or row.config_sha256 != digest(config)
            or spec.preflight_sha256 != row.preflight_sha256
        ):
            fail()
        return ProviderStructuredSubmissionReceipt(
            id=row.id,
            run_id=run.id,
            origin_user_id=owner_id,
            request_sha256=row.request_sha256,
            preflight_sha256=row.preflight_sha256,
            normalized_request=request,
            required_process_units=request.required_process_units,
        )
    except (TypeError, ValueError, AttributeError):
        fail()
