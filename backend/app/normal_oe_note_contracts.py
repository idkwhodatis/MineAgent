"""Normal OE note identities, activated only by the dedicated032 runtime gate.

No native EvaluationBinding, legacy strategy ID, release authority or new result
is implied. Activation requires the dedicated pin/retention migration first.
"""

from datetime import date
from typing import Annotated, Literal
from urllib.parse import urlencode
from uuid import UUID

from pydantic import Field, field_validator

from packages.research_trace.contracts import Contract, Sha256, digest


class NormalOENoteIdentity(Contract):
    owner_id: UUID
    run_id: UUID
    execution_attempt_id: UUID
    generation: int = Field(strict=True, ge=1, le=1)
    execution_id: UUID
    candidate_id: UUID
    program_version_id: UUID
    program_sha256: Sha256
    factor_id: UUID
    factor_version_id: UUID
    snapshot_id: UUID
    snapshot_input_sha256: Sha256
    result_sha256: Sha256
    market_sha256: Sha256
    config_sha256: Sha256
    measurement_sha256: Sha256
    label: str = Field(default="", max_length=200)


class NormalOEExecutionNoteEvidence(NormalOENoteIdentity):
    kind: Literal["normal_oe_execution"]


class NormalOEDecisionNoteEvidence(NormalOENoteIdentity):
    kind: Literal["normal_oe_decision"]
    decision_sequence: int = Field(strict=True, ge=0, lt=8)
    row_sha256: Sha256 = Field(
        description="SHA-256 of the canonical validated execution_view row; result_sha256 independently binds retained original result bytes."
    )


class NormalOEFillNoteEvidence(NormalOENoteIdentity):
    kind: Literal["normal_oe_fill"]
    native_fill_id: str = Field(
        min_length=1, max_length=200, pattern=r"^[^\x00-\x1f\x7f]+$"
    )
    listing_id: UUID
    session_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    row_sha256: Sha256 = Field(
        description="SHA-256 of the canonical validated execution_view row; result_sha256 independently binds retained original result bytes."
    )

    @field_validator("session_date")
    @classmethod
    def actual_date(cls, value):
        date.fromisoformat(value)
        return value


NormalOENoteEvidence = Annotated[
    NormalOEExecutionNoteEvidence
    | NormalOEDecisionNoteEvidence
    | NormalOEFillNoteEvidence,
    Field(discriminator="kind"),
]


class NormalOENoteRestore(Contract):
    schema_version: Literal["mineagent.note-normal-oe-restore.v1"]
    evidence_index: int = Field(strict=True, ge=0, lt=32)


def physical_reference_sha256(ref: NormalOENoteEvidence):
    """Anchor identity excludes human label; full note/request hashes still include it."""
    return digest(ref.model_dump(mode="json", exclude={"label"}))


class NormalOENoteCapture(Contract):
    evidence: NormalOENoteEvidence
    href: str = Field(min_length=1, max_length=4096)
    qualification: Literal["released_original_identity_not_note_write"] = (
        "released_original_identity_not_note_write"
    )


def normal_note_href(ref: NormalOENoteEvidence):
    """Call only after server original-result admission; not an admission function."""
    params = {
        "normal_note_version": "1",
        "execution_id": str(ref.execution_id),
        "candidate_id": str(ref.candidate_id),
        "program_version_id": str(ref.program_version_id),
        "result_sha256": ref.result_sha256,
        "evidence_sha256": physical_reference_sha256(ref),
        "kind": ref.kind,
    }
    if ref.kind == "normal_oe_decision":
        params.update(
            decision_sequence=str(ref.decision_sequence), row_sha256=ref.row_sha256
        )
    elif ref.kind == "normal_oe_fill":
        params.update(
            native_fill_id=ref.native_fill_id,
            listing_id=str(ref.listing_id),
            session_date=ref.session_date,
            row_sha256=ref.row_sha256,
        )
    return f"/engine-runs/structured-search/runs/{ref.run_id}?{urlencode(params)}"


def exact_normal_restore(restore, refs, resolved):
    """Pure bounded selector; caller supplies actual separately admitted references.

    No fallback to a newer/first execution and no legacy restore interpretation.
    """
    if not isinstance(restore, NormalOENoteRestore) or len(refs) != len(resolved):
        raise ValueError("Exact normal restore inventory required")
    if restore.evidence_index >= len(refs):
        raise ValueError("Normal restore evidence index is absent")
    ref, available = refs[restore.evidence_index], resolved[restore.evidence_index]
    if (
        type(ref)
        not in (
            NormalOEExecutionNoteEvidence,
            NormalOEDecisionNoteEvidence,
            NormalOEFillNoteEvidence,
        )
        or available is not True
    ):
        raise ValueError("Normal restore requires this exact admitted evidence")
    return normal_note_href(ref)


class NormalOENotePinDetails(Contract):
    schema_version: Literal["mineagent.note-normal-oe-pin.v1"]
    run_id: UUID
    attempt_id: UUID
    generation: int = Field(strict=True, ge=1, le=1)
    candidate_id: UUID
    program_version_id: UUID
    program_sha256: Sha256
    factor_id: UUID
    factor_version_id: UUID
    snapshot_id: UUID
    snapshot_input_sha256: Sha256
    market_sha256: Sha256
    config_sha256: Sha256
    measurement_sha256: Sha256
