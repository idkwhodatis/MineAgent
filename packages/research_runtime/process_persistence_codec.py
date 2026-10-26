"""Offline versioned row codec; never an issuer, registry or authentication API.

Only explicit selected columns may enter this codec. V2 grammar can be inspected,
but no v2 profile is registered: require_persistable rejects every v2 declaration.
V1 commitment bytes exclude the new DB-only version/policy columns exactly.
"""

from collections.abc import Mapping

from packages.research_runtime.process_contracts import (
    ProcessAdmission,
    ProcessError,
    canonical,
)
from packages.research_runtime.process_declarations_v2 import (
    ProcessDeclarationV2,
    declaration_bytes,
    declared_ticket_sha256,
)

V1 = "mineagent.process-admission.v1"
V2 = "mineagent.process-admission.v2"
FIELDS = frozenset(ProcessAdmission.__dataclass_fields__)
ROW_FIELDS = FIELDS | {"schema_version", "ipc_policy"}


def decode_row(row: Mapping) -> ProcessAdmission | ProcessDeclarationV2:
    """Inspect an exact typed DB projection, not JSON or an admission grant."""
    if not isinstance(row, Mapping) or set(row) != ROW_FIELDS:
        raise ProcessError("process_persistence_shape")
    value = dict(row)
    version = value.pop("schema_version")
    policy = value.pop("ipc_policy")
    if version == V1:
        if policy != "host_ticket":
            raise ProcessError("process_persistence_v1_policy")
        return ProcessAdmission(**value)
    if version == V2:
        ticket = value.pop("ticket_sha256")
        return ProcessDeclarationV2.model_validate(
            {
                **value,
                "schema_version": V2,
                "ipc": {"ipc_policy": policy, "ticket_sha256": ticket},
            }
        )
    raise ProcessError("process_persistence_version")


def encode_row(value: ProcessAdmission | ProcessDeclarationV2) -> dict:
    """Produce an inspection projection only; call require_persistable to write."""
    if type(value) is ProcessAdmission:
        # Revalidate instead of trusting a modified/constructed frozen object.
        value = ProcessAdmission(**value.__dict__)
        return {**value.__dict__, "schema_version": V1, "ipc_policy": "host_ticket"}
    if type(value) is ProcessDeclarationV2:
        value = ProcessDeclarationV2.model_validate(value)
        fields = value.model_dump(mode="python")
        fields.update(fields.pop("ipc"))
        return fields
    raise ProcessError("process_persistence_type")


def commitment_bytes(row: Mapping) -> bytes:
    value = decode_row(row)
    if type(value) is ProcessAdmission:
        return canonical(value.public())
    return declaration_bytes(value)


def declared_ipc_digest(row: Mapping) -> str:
    """Reject no-IPC before any ticket parsing/authentication; this authenticates nothing."""
    value = decode_row(row)
    if type(value) is ProcessAdmission:
        return value.ticket_sha256
    return declared_ticket_sha256(value)


def require_persistable(row: Mapping) -> ProcessAdmission:
    value = decode_row(row)
    if type(value) is not ProcessAdmission:
        raise ProcessError("process_v2_profile_not_registered")
    return value
