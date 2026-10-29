"""Explicit owned upload composition; no run, release or provider authority."""

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.app.schemas import DatasetPublic
from packages.factors.contracts import Contract

PROFILE = "mineagent.declared-upload-basket.v1"
REVISION = "030_declared_upload_basket"


class DeclaredUploadBasketCreate(Contract):
    source_dataset_ids: tuple[UUID, UUID]
    acknowledge_unverified: Literal[True]
    idempotency_key: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$"
    )

    @field_validator("acknowledge_unverified", mode="before")
    @classmethod
    def explicit(cls, value):
        if value is not True:
            raise ValueError("Explicit unverified-data acknowledgement required")
        return value

    @model_validator(mode="after")
    def ordered(self):
        if self.source_dataset_ids != tuple(
            sorted(set(self.source_dataset_ids), key=str)
        ):
            raise ValueError(
                "Two distinct source identities in canonical order required"
            )
        return self


class DeclaredUploadBasketReceipt(Contract):
    dataset: DatasetPublic
    profile: Literal["mineagent.declared-upload-basket.v1"] = PROFILE
    data_evidence: Literal["user_declaration_unverified"] = (
        "user_declaration_unverified"
    )
    is_synthetic: Literal[False] = False
    point_in_time_proven: Literal[False] = False
    research_submitted: Literal[False] = False
    result_released: Literal[False] = False
