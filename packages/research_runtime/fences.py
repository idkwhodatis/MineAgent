"""Typed, non-exportable execution authority for neutral internal operations."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class AttemptFence:
    owner_id: UUID
    run_id: UUID
    attempt_id: UUID
    generation: int
    lease_token: UUID

    def __post_init__(self):
        for name in ("owner_id", "run_id", "attempt_id", "lease_token"):
            if type(getattr(self, name)) is not UUID:
                raise ValueError("Canonical UUID execution authority required")
        if type(self.generation) is not int or self.generation < 1:
            raise ValueError("Positive execution generation required")
