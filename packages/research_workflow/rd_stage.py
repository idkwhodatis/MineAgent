"""Fixed next RD child definition; IDs are commitments, never live authority."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.factors.contracts import Contract, Sha256, digest


class RDStageDefinition(Contract):
    schema_version: Literal["mineagent.workflow-rd-stage.v1"] = (
        "mineagent.workflow-rd-stage.v1"
    )
    plan_sha256: Sha256
    research_iteration: int = Field(strict=True, ge=0, le=7)
    predecessor_checkpoint_id: UUID | None
    parent_factor_version_id: UUID | None
    parent_source_stage_id: UUID | None

    @model_validator(mode="after")
    def shape(self):
        if self.research_iteration == 0:
            if self.parent_source_stage_id is not None:
                raise ValueError(
                    "Initial declared parent is not a fictional workflow stage"
                )
        elif any(
            value is None
            for value in (
                self.predecessor_checkpoint_id,
                self.parent_factor_version_id,
                self.parent_source_stage_id,
            )
        ):
            raise ValueError(
                "Later child needs actual prior stage, factor version and checkpoint"
            )
        return self

    @property
    def sha256(self):
        return digest(self)
