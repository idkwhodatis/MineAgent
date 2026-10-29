"""One shared source-side reference vocabulary for workflow/UI persistence.

Refs identify immutable content; the owning host must resolve actual owner, stage,
release and current-version rules. A parsed ref is never permission to consume it.
Manual refs use RDSteerVersion.content_sha256, NOT its text_sha256. Unknown origin
may be displayed honestly but is never automatically eligible for a new stage.
"""

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from packages.factors.contracts import Contract, Sha256, digest

InputOrigin = Literal[
    "manual_user_submission",
    "normalized_external_proposal",
    "experiment_feedback_proposal",
    "unknown",
]
ProposalKind = Literal["direction", "hypothesis", "constraints"]


class InputVersionRef(Contract):
    origin: InputOrigin
    record_id: UUID
    version_id: UUID
    content_sha256: Sha256


class ExternalFeedMapping(Contract):
    """Owner/workflow-selected JSON paths; no JSONPath evaluation or URL fetch."""

    schema_version: Literal["mineagent.external-feed-mapping.v1"] = (
        "mineagent.external-feed-mapping.v1"
    )
    items_path: tuple[str | int, ...] = Field(default=("items",), max_length=8)
    text_path: tuple[str | int, ...] = Field(
        default=("text",), min_length=1, max_length=8
    )
    title_path: tuple[str | int, ...] | None = ("title",)
    kind: ProposalKind = "direction"

    @field_validator("items_path", "text_path", "title_path", mode="before")
    @classmethod
    def bounded_paths(cls, value):
        if value is None:
            return value
        if not isinstance(value, (tuple, list)) or len(value) > 8:
            raise ValueError("Bounded JSON path required")
        for part in value:
            if type(part) is int and 0 <= part <= 4095:
                continue
            if (
                type(part) is str
                and 1 <= len(part) <= 100
                and not any(ord(c) < 32 or ord(c) == 127 for c in part)
            ):
                continue
            raise ValueError(
                "Only literal bounded JSON keys or nonnegative indexes allowed"
            )
        return value

    @property
    def sha256(self):
        return digest(self)


class NormalizedExternalProposal(Contract):
    """Format-normalized *untrusted data*, including instruction-looking content."""

    schema_version: Literal["mineagent.normalized-external-proposal.v1"] = (
        "mineagent.normalized-external-proposal.v1"
    )
    origin: Literal["normalized_external_proposal"] = "normalized_external_proposal"
    normalizer_version: Literal["mineagent.external-json-normalizer.v1"] = (
        "mineagent.external-json-normalizer.v1"
    )
    raw_input_sha256: Sha256
    mapping_sha256: Sha256
    item_pointer: str = Field(min_length=1, max_length=2048)
    item_ordinal: int = Field(strict=True, ge=0, le=31)
    kind: ProposalKind
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=2048)
    trust: Literal["untrusted_external_data"] = "untrusted_external_data"
    validation_scope: Literal["format_and_bounds_only"] = "format_and_bounds_only"
    instruction_authority: Literal["none"] = "none"
    approval_authority: Literal["none"] = "none"

    @field_validator("text", "title")
    @classmethod
    def bounded_plain_data(cls, value, info):
        if not value.strip() or len(value.encode("utf-8")) > (
            8192 if info.field_name == "text" else 800
        ):
            raise ValueError("Bounded nonempty UTF-8 proposal data required")
        if any(
            (ord(c) < 32 and (info.field_name == "title" or c not in "\n\t"))
            or ord(c) == 127
            for c in value
        ):
            raise ValueError("Unsupported control characters in proposal data")
        return value

    @property
    def sha256(self):
        return digest(self)


class SourceOutputRef(Contract):
    """Identity only, not evidence that a stage completed or release is allowed.

    The host must join the exact owner/workflow/stage/child run and typed retained
    output, then independently recheck development eligibility and release ledger.
    No caller boolean may certify success, disclosure, sealed status or eligibility.
    """

    workflow_id: UUID
    stage_id: UUID
    child_run_id: UUID
    output_kind: Literal["direct_analysis", "structured_execution"]
    output_id: UUID
    content_sha256: Sha256
