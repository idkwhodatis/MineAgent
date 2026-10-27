"""Pure validation of registered JSON-wrapped native checkpoint objects.

Object ownership/registration, lease, ancestry and objective-receipt closure must
be checked by the calling service. This module neither reads nor writes files.
"""

import hashlib
import math
import re
from uuid import UUID

from .checkpoint_native import _json, _normalize, canonical, state_sha256
from .host_contracts import CheckpointFileEnvelope, CheckpointInventory

MAX_NATIVE_BYTES = 8 * 1024 * 1024
MAX_ENVELOPE_BYTES = 700000
REQUIRED = frozenset({"metadata.json", "best_program.py", "best_program_info.json"})
PROGRAM_NAME = re.compile(r"programs/([0-9a-f-]{36})\.json\Z")


def _finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def validate_checkpoint_evidence(
    inventory: CheckpointInventory,
    files: dict[str, bytes],
    expected_state_sha256: str,
) -> dict:
    """Validate exact JSON object bytes and return the normalized native state.

    ``files`` maps each inventory name to its registered file-envelope object's
    exact serialized bytes, allowing verification of both envelope and native
    file hashes. The inventory object's own hash is checked by its caller.
    """
    if type(inventory) is not CheckpointInventory or type(files) is not dict:
        raise ValueError("Typed inventory and exact object mapping required")
    # Reparse even typed models: model_construct/model_copy must not bypass limits.
    inventory = CheckpointInventory.model_validate_json(
        canonical(inventory.model_dump(mode="json"))
    )
    if (
        type(expected_state_sha256) is not str
        or re.fullmatch(r"[0-9a-f]{64}", expected_state_sha256) is None
    ):
        raise ValueError("Invalid expected native state hash")
    names = {entry.name for entry in inventory.files}
    if set(files) != names or not REQUIRED <= names:
        raise ValueError("Native object inventory differs")
    if len({entry.object.id for entry in inventory.files}) != len(inventory.files):
        raise ValueError("Duplicate file object identity")
    raw = {}
    total = 0
    for entry in inventory.files:
        envelope_bytes = files[entry.name]
        if (
            type(envelope_bytes) is not bytes
            or not 1 <= len(envelope_bytes) <= MAX_ENVELOPE_BYTES
            or hashlib.sha256(envelope_bytes).hexdigest() != entry.object.sha256
        ):
            raise ValueError("Registered envelope bytes differ")
        # Reject duplicate JSON keys and nonfinite constants before model parsing.
        envelope = CheckpointFileEnvelope.model_validate_json(
            canonical(_json(envelope_bytes))
        )
        if (
            envelope.name != entry.name
            or envelope.sha256 != entry.file_sha256
            or envelope.size_bytes != entry.size_bytes
        ):
            raise ValueError("Envelope/native inventory binding differs")
        data = envelope.bytes()
        total += len(data)
        if total > MAX_NATIVE_BYTES:
            raise ValueError("Native checkpoint exceeds byte bound")
        raw[entry.name] = data
    programs = {}
    for name, data in raw.items():
        if name in REQUIRED:
            continue
        match = PROGRAM_NAME.fullmatch(name)
        if match is None or str(UUID(match[1])) != match[1]:
            raise ValueError("Unexpected native program filename")
        pid = match[1]
        if pid in programs:
            raise ValueError("Duplicate native program identity")
        program = _json(data)
        if type(program) is not dict or program.get("id") != pid:
            raise ValueError("Native program file identity differs")
        programs[pid] = program
    state = _normalize(_json(raw["metadata.json"]), programs)
    best = programs[state["metadata"]["best_program_id"]]
    if raw["best_program.py"] != best["code"].encode("utf-8"):
        raise ValueError("Native best source differs")
    info = _json(raw["best_program_info.json"])
    expected = {
        "id": best["id"],
        "generation": best["generation"],
        "iteration": best["iteration_found"],
        "current_iteration": state["metadata"]["last_iteration"],
        "metrics": best["metrics"],
        "language": best["language"],
        "timestamp": best["timestamp"],
    }
    if (
        type(info) is not dict
        or set(info) != set(expected) | {"saved_at"}
        # Canonical comparison distinguishes bool from int and avoids coercion.
        or canonical({key: info[key] for key in expected}) != canonical(expected)
        or not _finite_number(info["saved_at"])
    ):
        raise ValueError("Native best information differs")
    if state_sha256(state) != expected_state_sha256:
        raise ValueError("Native checkpoint state digest differs")
    return state
