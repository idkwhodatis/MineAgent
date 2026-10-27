"""024 bounded checkpoint transport documents; no execution/publication authority."""

import base64
import hashlib
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.openevolve_adapter.checkpoint_native import canonical
from runtimes.openevolve_operational.checkpoint_transfer import (
    CHUNK_BYTES,
    FIXED,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_TOTAL_BYTES,
    _name,
)

SHA = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class File(Closed):
    name: str
    size_bytes: int = Field(ge=1, le=MAX_FILE_BYTES)
    sha256: SHA

    @model_validator(mode="after")
    def named(self):
        _name(self.name)
        return self


class Begin(Closed):
    token: UUID
    files: tuple[File, ...] = Field(min_length=4, max_length=MAX_FILES)

    @model_validator(mode="after")
    def inventory(self):
        names = [f.name for f in self.files]
        if names != sorted(set(names)) or not FIXED <= set(names):
            raise ValueError("closed ordered checkpoint inventory")
        if self.total_bytes > MAX_TOTAL_BYTES:
            raise ValueError("checkpoint total bound")
        return self

    @property
    def total_bytes(self):
        return sum(f.size_bytes for f in self.files)

    @property
    def chunks(self):
        return tuple(
            (f.name, offset, min(CHUNK_BYTES, f.size_bytes - offset))
            for f in self.files
            for offset in range(0, f.size_bytes, CHUNK_BYTES)
        )

    @property
    def transfer_peak_bytes(self):
        # Additive conservative raw assembly/workspace hold. Retained chunk/file
        # objects and their journals have separate reservations before publication.
        return (
            2 * self.total_bytes
            + 2 * 1048576
            + 2 * 4096 * (len(self.files) + len(self.chunks) + 1)
        )


class Chunk(Closed):
    token: UUID
    sequence: int = Field(ge=0, le=87)
    name: str
    offset: int = Field(ge=0, le=MAX_FILE_BYTES - 1)
    sha256: SHA
    data_base64: str = Field(min_length=4, max_length=87384)

    @model_validator(mode="after")
    def exact(self):
        _name(self.name)
        if self.offset % CHUNK_BYTES:
            raise ValueError("checkpoint aligned offset")
        self.bytes()
        return self

    def bytes(self):
        raw = base64.b64decode(self.data_base64, validate=True)
        if (
            not 1 <= len(raw) <= CHUNK_BYTES
            or base64.b64encode(raw).decode("ascii") != self.data_base64
            or hashlib.sha256(raw).hexdigest() != self.sha256
        ):
            raise ValueError("checkpoint chunk bytes")
        return raw

    def require_inventory(self, begin):
        if type(begin) is not Begin or self.token != begin.token:
            raise ValueError("checkpoint upload token")
        if self.sequence >= len(begin.chunks) or begin.chunks[self.sequence] != (
            self.name,
            self.offset,
            len(self.bytes()),
        ):
            raise ValueError("checkpoint exact chunk position")


class ChunkDocument(Closed):
    schema_version: Literal["mineagent.oe-checkpoint-chunk.v1"] = (
        "mineagent.oe-checkpoint-chunk.v1"
    )
    owner_id: UUID
    run_id: UUID
    attempt_id: UUID
    generation: int = Field(ge=1)
    epoch_id: UUID
    upload_id: UUID
    chunk: Chunk


def parse(model, value):
    """JSON mode intentionally permits canonical UUIDs/tuple arrays, never bool ints."""
    return model.model_validate_json(canonical(value))
