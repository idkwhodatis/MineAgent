"""Explicit023 supplementary closure dispatch; historical snapshots stay exact."""

from backend.app.rdagent_submission_backup import SCHEMA, read_closure, validate_closure
from packages.factors.contracts import canonical_bytes


def _closure(snapshot, capabilities):
    schema = getattr(capabilities, "rd_submission_schema", None)
    if schema not in (None, SCHEMA) or (schema is not None) != (
        "rd_submission_closure" in snapshot
    ):
        raise ValueError("Submission closure capability mismatch")
    if schema is None:
        return None
    closure = snapshot["rd_submission_closure"]
    if type(closure) is not dict or closure.get("schema_version") != schema:
        raise ValueError("Submission closure version mismatch")
    return closure


def read_submission_closure(connection, capabilities, limits):
    if getattr(capabilities, "rd_submission_schema", None) is None:
        return None
    closure = read_closure(
        connection, limits.max_files, maximum_bytes=limits.max_manifest_bytes
    )
    _closure({"rd_submission_closure": closure}, capabilities)
    if len(canonical_bytes(closure)) > limits.max_manifest_bytes:
        raise ValueError("Submission closure size bound")
    return closure


def validate_submission_snapshot(snapshot, capabilities, maximum_rows):
    closure = _closure(snapshot, capabilities)
    if closure is not None:
        validate_closure(
            closure,
            rd_source_closure=snapshot["rd_source_closure"],
            direct_analysis_closure=snapshot["direct_analysis_closure"],
            research_closure=snapshot["research_closure"],
            maximum_rows=maximum_rows,
        )
