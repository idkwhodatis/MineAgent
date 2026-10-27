"""Closed grant data; possessing a descriptor does not authenticate a worker."""

from typing import Literal
from uuid import UUID

from pydantic import Field

from .contracts import SHA, Contract


class IterationGrant(Contract):
    schema_version: Literal["mineagent.openevolve-iteration-grant.v1"]
    admission_id: UUID
    epoch_id: UUID
    intent_id: UUID
    intent_ticket: SHA = Field(repr=False)
    native_iteration: int = Field(ge=1, le=2)
    parent_candidate_id: UUID
    parent_source_sha256: SHA
    selection_sha256: SHA
