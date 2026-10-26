"""Disabled admission-v2 grammar with explicit absence of candidate IPC.

This declaration grants nothing and is not accepted by the v1 supervisor/issuer.
Activation needs a version-discriminated DB row, closed registered profile,
launcher projection, independent deadline and exact-head containment acceptance.
V1 ticket fields and serialized admission hashes must remain byte-identical.
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.research_runtime.process_contracts import ProcessError, canonical

SHA = Annotated[str, Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]+$")]


class Closed(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )


class NoIPC(Closed):
    ipc_policy: Literal["none"]
    ticket_sha256: None


class HostTicketIPC(Closed):
    ipc_policy: Literal["host_ticket"]
    ticket_sha256: SHA


class ProcessDeclarationV2(Closed):
    schema_version: Literal["mineagent.process-admission.v2"]
    id: UUID
    owner_id: UUID
    run_id: UUID
    attempt_id: UUID
    generation: int = Field(ge=1, le=2**31 - 1)
    role: Literal[
        "rd_orchestrator",
        "openevolve_orchestrator",
        "factor_cutoff_candidate",
        "factor_normalizer",
        "native_evaluator",
    ]
    logical_operation_id: UUID
    launch_ordinal: int = Field(ge=1, le=2**31 - 1)
    # A declaration is deliberately not a runtime registry lookup. The actual
    # future issuer must bind an accepted exact image/profile and all input bytes.
    profile: str = Field(min_length=1, max_length=60, pattern=r"^[a-z][a-z0-9_.-]+$")
    input_sha256: SHA
    reservation_id: UUID
    image_id: str = Field(min_length=71, max_length=71, pattern=r"^sha256:[0-9a-f]+$")
    ipc: Annotated[NoIPC | HostTicketIPC, Field(discriminator="ipc_policy")]

    @model_validator(mode="after")
    def exact_role_ipc(self):
        controller = self.role in {"rd_orchestrator", "openevolve_orchestrator"}
        if controller != isinstance(self.ipc, HostTicketIPC):
            raise ValueError("Role cannot gain or silently lose IPC authority")
        return self


def declaration_bytes(value: ProcessDeclarationV2) -> bytes:
    value = ProcessDeclarationV2.model_validate(value)
    return canonical(value.model_dump(mode="json"))


def declared_ticket_sha256(value: ProcessDeclarationV2) -> str:
    """Read a declared digest only; not peer authentication or launch authority."""
    value = ProcessDeclarationV2.model_validate(value)
    if not isinstance(value.ipc, HostTicketIPC):
        raise ProcessError("process_has_no_ipc")
    return value.ipc.ticket_sha256


def require_v2_launch_authority(*_args, **_kwargs):
    raise ProcessError("process_v2_not_activated")
