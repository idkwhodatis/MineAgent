"""Unregistered owner-only derivation audit reads; no ingest or execution grant.

The actual source reader checks installed input capability and immutable receipt
scope. Main registration waits for coherent input/workflow storage and cold closure.
"""

from datetime import datetime
from hashlib import sha256
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from pydantic import Field, ValidationError, model_validator
from starlette.concurrency import run_in_threadpool

from backend.app.api.deps import CurrentUser, SessionDep
from backend.app.research_input_content import (
    read_external_content,
    read_normalized_content,
)
from backend.app.research_input_dispatch import retain_external_object
from backend.app.research_input_feedback_content import read_feedback_content
from backend.app.research_input_history import read_derivation_history
from backend.app.services import DomainError
from packages.factors.contracts import Contract, Sha256
from packages.research_inputs.contracts import InputVersionRef
from packages.research_inputs.errors import PublicDerivationErrorCode

router = APIRouter()


class InputDerivationView(Contract):
    derivation_id: UUID
    workflow_id: UUID
    stage_id: UUID
    operation: Literal["external_normalization", "experiment_feedback_projection"]
    source_sha256: Sha256
    raw_object_id: UUID | None = None
    terminal_event_id: UUID | None = None
    terminal_event_sha256: Sha256 | None = None
    raw_input_sha256: Sha256 | None
    mapping_sha256: Sha256 | None
    status: Literal[
        "prepared",
        "started_no_terminal_receipt",
        "succeeded",
        "rejected",
        "failed",
        "cancelled",
        "interrupted",
    ]
    error_code: PublicDerivationErrorCode | None
    prepared_at: datetime
    started_at: datetime | None
    terminal_at: datetime | None
    outputs: tuple[InputVersionRef, ...] = Field(max_length=32)
    qualification: Literal["receipt_metadata_not_live_status_or_content_approval"] = (
        "receipt_metadata_not_live_status_or_content_approval"
    )

    @model_validator(mode="after")
    def exact_receipt_state(self):
        terminal = self.status not in {"prepared", "started_no_terminal_receipt"}
        if terminal != (self.terminal_at is not None):
            raise ValueError("Terminal receipt time differs")
        if (self.status == "succeeded") != bool(self.outputs):
            raise ValueError("Only successful derivation has output references")
        if (self.status in {"rejected", "failed", "cancelled", "interrupted"}) != (
            self.error_code is not None
        ):
            raise ValueError("Terminal error evidence differs")
        times = [
            v
            for v in (self.prepared_at, self.started_at, self.terminal_at)
            if v is not None
        ]
        if any(
            v.tzinfo is None or v.utcoffset() is None for v in times
        ) or times != sorted(times):
            raise ValueError("Exact ordered receipt timestamps required")
        if self.status == "prepared" and self.started_at is not None:
            raise ValueError("Prepared prefix cannot contain a start receipt")
        expected_origin = (
            "normalized_external_proposal"
            if self.operation == "external_normalization"
            else "experiment_feedback_proposal"
        )
        if any(ref.origin != expected_origin for ref in self.outputs):
            raise ValueError("Derived output origin differs from the actual operation")
        if self.status == "started_no_terminal_receipt" and self.started_at is None:
            raise ValueError("Started receipt time required")
        return self


@router.get(
    "/research-workflows/{workflow_id}/stages/{stage_id}/input-derivation",
    response_model=InputDerivationView,
)
def read_input_derivation(
    workflow_id: UUID, stage_id: UUID, user: CurrentUser, session: SessionDep
):
    history = read_derivation_history(session, user.id, workflow_id, stage_id)
    try:
        result = InputDerivationView.model_validate(history, from_attributes=True)
        if (result.workflow_id, result.stage_id) != (workflow_id, stage_id):
            raise ValueError("Read projection scope differs")
        return result
    except (ValidationError, ValueError, TypeError):
        # Never return malformed raw exception prose or partially decoded outputs.
        raise DomainError(
            "input_derivation_changed", "Input derivation evidence differs", 409
        ) from None


class RetainedExternalInput(Contract):
    id: UUID
    sha256: Sha256
    size_bytes: int = Field(strict=True, ge=0, le=65536)
    qualification: Literal["raw_retained_not_normalized"] = (
        "raw_retained_not_normalized"
    )
    workflow_created: Literal[False] = False


@router.post(
    "/research-inputs/external-objects",
    response_model=RetainedExternalInput,
    status_code=201,
)
async def retain_external_input(request: Request, user: CurrentUser):
    # The browser/API supplies passive bytes, not URLs, parsed instructions or
    # successful outputs. Even empty or malformed JSON can be audited by a stage.
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 65536:
            raise DomainError(
                "external_input_too_large", "External input exceeds 64 KiB", 413
            )
        raw.extend(chunk)
    retained = await run_in_threadpool(
        retain_external_object,
        request.app.state.session_factory,
        request.app.state.settings,
        owner_id=user.id,
        raw=bytes(raw),
    )
    # Factory owns capacity checks and commit before returning this receipt.
    return RetainedExternalInput.model_validate(retained, from_attributes=True)


async def _content_response(request, user, identity, expected_sha256, *, normalized):
    reader = read_normalized_content if normalized else read_external_content
    identity_argument = {"version_id" if normalized else "object_id": identity}
    retained = await run_in_threadpool(
        reader,
        request.app.state.session_factory,
        request.app.state.settings,
        owner_id=user.id,
        expected_sha256=expected_sha256,
        **identity_argument,
    )
    if (
        retained.sha256 != expected_sha256
        or retained.size_bytes != len(retained.content)
        or len(retained.content) > (32768 if normalized else 65536)
    ):
        raise DomainError("input_content_inconsistent", "Retained content differs", 409)
    # Fixed attachment names/MIME: retained input text never controls headers.
    filename = (
        f"normalized-input-{identity}.json"
        if normalized
        else f"external-input-{identity}.bin"
    )
    return Response(
        retained.content,
        media_type="application/json" if normalized else "application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Content-SHA256": retained.sha256,
        },
    )


@router.get("/research-inputs/external-objects/{object_id}/content")
async def external_input_content(
    object_id: UUID,
    request: Request,
    user: CurrentUser,
    expected_sha256: str = Query(pattern="^[a-f0-9]{64}$"),
):
    return await _content_response(
        request, user, object_id, expected_sha256, normalized=False
    )


@router.get("/research-inputs/versions/{version_id}/content")
async def normalized_input_content(
    version_id: UUID,
    request: Request,
    user: CurrentUser,
    expected_sha256: str = Query(pattern="^[a-f0-9]{64}$"),
):
    return await _content_response(
        request, user, version_id, expected_sha256, normalized=True
    )


@router.get("/research-inputs/feedback-versions/{version_id}/content")
async def feedback_input_content(
    version_id: UUID,
    request: Request,
    user: CurrentUser,
    expected_sha256: str = Query(pattern="^[a-f0-9]{64}$"),
):
    """Distinct release-qualified output; never reuse the external-only reader."""
    retained = await run_in_threadpool(
        read_feedback_content,
        request.app.state.session_factory,
        request.app.state.settings,
        owner_id=user.id,
        version_id=version_id,
        expected_sha256=expected_sha256,
    )
    if (
        type(retained.content) is not bytes
        or retained.sha256 != expected_sha256
        or sha256(retained.content).hexdigest() != expected_sha256
        or retained.size_bytes != len(retained.content)
        or not 1 <= len(retained.content) <= 32768
    ):
        raise DomainError("input_content_inconsistent", "Retained content differs", 409)
    return Response(
        retained.content,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="feedback-input-{version_id}.json"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Content-SHA256": retained.sha256,
        },
    )
