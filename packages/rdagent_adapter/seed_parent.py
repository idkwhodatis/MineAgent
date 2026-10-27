"""Versioned seed input only: no adoption, execution or detached authority."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.factors.contracts import (
    Contract,
    FactorDefinition,
    Sha256,
    canonical_bytes,
    digest,
    source_sha256,
)


class SeedParentReference(Contract):
    schema_version: Literal["mineagent.rdagent-seed-parent.v1"] = (
        "mineagent.rdagent-seed-parent.v1"
    )
    owner_id: UUID
    factor_id: UUID
    factor_version_id: UUID
    ordinal: int = Field(strict=True, ge=1)
    code_object_id: UUID
    code_sha256: Sha256
    code_size_bytes: int = Field(strict=True, ge=1, le=131072)
    definition_sha256: Sha256
    source_provenance_sha256: Sha256

    @property
    def sha256(self):
        return digest(self)


class ResolvedSeedParent(Contract):
    """Ephemeral exact reread, never independently trusted caller input or DB copy."""

    reference: SeedParentReference
    definition: FactorDefinition
    source: str = Field(min_length=1, max_length=131072)

    @model_validator(mode="after")
    def exact_bytes(self):
        if (
            self.definition.sha256 != self.reference.definition_sha256
            or self.definition.code_sha256 != self.reference.code_sha256
            or source_sha256(self.source) != self.reference.code_sha256
            or len(self.source.encode("utf-8")) != self.reference.code_size_bytes
        ):
            raise ValueError("Seed source/definition differs from immutable reference")
        return self

    def prompt_section(self):
        # No fake successful knowledge, previous feedback, same-run source ID or
        # adoption decision. The parent is explicitly just an input to refinement.
        raw = canonical_bytes(
            {
                "schema_version": "mineagent.rdagent-seed-prompt.v1",
                "reference": self.reference.model_dump(mode="json"),
                "definition": self.definition.model_dump(mode="json"),
                "source": self.source,
                "meaning": "owned_parent_input_only_not_new_evaluation_or_adoption",
            }
        )
        if len(raw) > 6144:
            raise ValueError(
                "Seed exceeds initial prompt section limit; never truncate"
            )
        return raw.decode("ascii")
