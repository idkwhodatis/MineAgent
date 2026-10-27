"""026 seed byte reconstruction before inherited physical/domain validators."""

from types import MappingProxyType

from backend.app.rdagent_seed_backup import SCHEMA, read_closure, validate_closure
from packages.factors.contracts import canonical_bytes


def seed_closure(snapshot, capabilities):
    schema = getattr(capabilities, "rd_seed_schema", None)
    if schema not in (None, SCHEMA) or (schema is not None) != (
        "rd_seed_closure" in snapshot
    ):
        raise ValueError("Seed closure capability differs")
    if schema is None:
        return None
    value = snapshot["rd_seed_closure"]
    if type(value) is not dict or value.get("schema_version") != SCHEMA:
        raise ValueError("Seed closure schema differs")
    return value


def read_seed_closure(connection, capabilities, limits):
    if getattr(capabilities, "rd_seed_schema", None) is None:
        return None
    value = read_closure(
        connection, limits.max_files, maximum_bytes=limits.max_manifest_bytes
    )
    seed_closure({"rd_seed_closure": value}, capabilities)
    if len(canonical_bytes(value)) > limits.max_manifest_bytes:
        raise ValueError("Seed closure metadata bound")
    return value


def prepare_seed_snapshot(snapshot, capabilities, read_bytes, maximum_rows):
    """No independent restore success: all following closure validators must pass."""
    value = seed_closure(snapshot, capabilities)
    if value is None:
        return MappingProxyType({})
    return validate_closure(
        value,
        rd_source_closure=snapshot["rd_source_closure"],
        research_closure=snapshot["research_closure"],
        factor_closure=snapshot["factor_closure"],
        direct_analysis_closure=snapshot["direct_analysis_closure"],
        rd_submission_closure=snapshot["rd_submission_closure"],
        read_bytes=read_bytes,
        maximum_rows=maximum_rows,
    )
