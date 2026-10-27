"""Immutable user steer declarations, never a live edit or output approval.

Natural-language constraints are prompt guidance. Recording consumption never
proves model compliance; only independently enforced platform rules are rules.
"""

from hashlib import sha256
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.factors.contracts import Contract, Sha256, canonical_bytes, digest

SteerKind = Literal["direction", "hypothesis", "constraints"]


class SteerVersion(Contract):
    schema_version: Literal["mineagent.rdagent-steer-version.v1"] = (
        "mineagent.rdagent-steer-version.v1"
    )
    steer_id: UUID
    version_id: UUID
    ordinal: int = Field(strict=True, ge=1, le=64)
    kind: SteerKind
    text: str = Field(min_length=1, max_length=2048)
    text_sha256: Sha256

    @model_validator(mode="after")
    def exact_text(self):
        raw = self.text.encode("utf-8")
        if (
            not self.text.strip()
            or "\0" in self.text
            or len(raw) > 8192
            or sha256(raw).hexdigest() != self.text_sha256
        ):
            raise ValueError("Exact bounded steer text commitment required")
        return self

    @property
    def sha256(self):
        return digest(self)


class SteerSnapshot(Contract):
    schema_version: Literal["mineagent.rdagent-steer-snapshot.v1"] = (
        "mineagent.rdagent-steer-snapshot.v1"
    )
    owner_id: UUID
    versions: tuple[SteerVersion, ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def bounded_distinct(self):
        if len({row.version_id for row in self.versions}) != len(self.versions) or len(
            {row.steer_id for row in self.versions}
        ) != len(self.versions):
            raise ValueError("Select distinct steer roots and immutable versions")
        if len(canonical_bytes(self)) > 32768:
            raise ValueError("Selected steer snapshot exceeds byte bound")
        return self

    @property
    def sha256(self):
        return digest(self)
