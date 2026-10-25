"""Bounded immutable requests for the standalone factor workbench."""

from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from packages.factors.contracts import FactorDefinition, canonical_bytes

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class FactorVersionCreate(Contract):
    parent_version_id: UUID | None = None
    definition: FactorDefinition
    code: str = Field(min_length=1, max_length=131072)
    change_note: str | None = Field(default=None, max_length=2000)

    @field_validator("code")
    @classmethod
    def source_bytes(cls, value):
        if len(value.encode("utf-8")) > 131072:
            raise ValueError("Source exceeds 128 KiB")
        return value

    @model_validator(mode="after")
    def exact_source(self):
        self.definition.validate_source(self.code)
        return self


class FactorCreate(FactorVersionCreate):
    name: str = Field(min_length=1, max_length=240)
    category: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def root(self):
        if self.parent_version_id is not None:
            raise ValueError("New factors cannot have another factor as parent")
        if not self.name.strip():
            raise ValueError("Name must contain visible characters")
        return self


class FactorPatch(Contract):
    expected_presentation_revision: int = Field(ge=1, strict=True)
    name: str | None = Field(default=None, min_length=1, max_length=240)
    category: str | None = Field(default=None, max_length=120)
    bookmarked: bool | None = None


class BundleMember(Contract):
    factor_version_id: UUID
    definition_sha256: Sha256


class FactorBundleCreate(Contract):
    name: str = Field(min_length=1, max_length=240)
    members: list[BundleMember] = Field(min_length=1, max_length=100)
    usage_constraints: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique(self):
        if len(canonical_bytes(self.usage_constraints)) > 262144:
            raise ValueError("Usage constraints exceed byte budget")
        if len({m.factor_version_id for m in self.members}) != len(self.members):
            raise ValueError("Duplicate factor version")
        return self


class ComparisonSide(Contract):
    version_id: UUID
    snapshot_id: UUID | None = None


class FactorComparisonCreate(Contract):
    left: ComparisonSide
    right: ComparisonSide


class FactorExportCreate(Contract):
    snapshot_ids: list[UUID] = Field(default_factory=list, max_length=10)
    evaluation_ids: list[UUID] = Field(default_factory=list, max_length=20)
    include_values: bool = False


class FixedFixtureCreate(Contract):
    kind: Annotated[
        str, Field(pattern=r"^(momentum|risk_adjusted_momentum|cross_sectional_rank)$")
    ] = "momentum"
    name: str = Field(default="Hand-checkable momentum", min_length=1, max_length=240)
    factor_id: UUID | None = None
    parent_version_id: UUID | None = None
    lookback_sessions: int = Field(default=2, ge=1, le=5, strict=True)
    lag_sessions: int = Field(default=0, ge=0, le=2, strict=True)

    @model_validator(mode="after")
    def lineage(self):
        if (self.factor_id is None) != (self.parent_version_id is None):
            raise ValueError("Fixture extension needs exact factor and parent")
        return self
