"""027 public selection metadata; no values, credentials or execution grants."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.factors.contracts import DateWindow
from packages.openevolve_structured.candidate import Closed
from packages.openevolve_structured.contracts import SHA
from packages.openevolve_structured.provider_contracts import OEChatBinding


class StructuredModelOption(Closed):
    binding: OEChatBinding
    binding_sha256: SHA
    label: str = Field(min_length=1, max_length=160)
    operator_available: bool
    unavailable_reason: str | None = Field(default=None, min_length=1, max_length=80)
    qualification: Literal["public_operator_metadata_not_submission_authority"] = (
        "public_operator_metadata_not_submission_authority"
    )

    @model_validator(mode="after")
    def consistent(self):
        if self.binding_sha256 != self.binding.sha256:
            raise ValueError("Exact public model binding digest required")
        if self.operator_available != (self.unavailable_reason is None):
            raise ValueError("Exact operator availability reason required")
        return self


class StructuredSnapshotOption(Closed):
    snapshot_id: UUID
    factor_id: UUID
    factor_version_id: UUID
    source_run_id: UUID
    dataset_version_id: UUID
    signal_window: DateWindow
    snapshot_manifest_sha256: SHA
    release_receipt_id: UUID
    qualification: Literal["released_historical_metadata_requires_fresh_preflight"] = (
        "released_historical_metadata_requires_fresh_preflight"
    )


class StructuredSnapshotPage(Closed):
    items: tuple[StructuredSnapshotOption, ...] = Field(max_length=50)
    offset: int = Field(ge=0, le=10000)
    limit: int = Field(ge=1, le=50)
    has_more: bool


class StructuredSearchCapabilities(Closed):
    routes_available: bool
    can_preflight: bool
    reason_code: Literal["runtime_schema_unavailable"] | None = None
    qualification: Literal["metadata_not_submission_authority"] = (
        "metadata_not_submission_authority"
    )

    @model_validator(mode="after")
    def consistent(self):
        if self.routes_available != self.can_preflight or self.routes_available != (
            self.reason_code is None
        ):
            raise ValueError(
                "Exact installed normal structured route availability required"
            )
        return self
