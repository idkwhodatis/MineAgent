"""026 steer envelope dispatch, independent of current selection eligibility."""

from backend.app.rdagent_steer_backup import SCHEMA, read_closure, validate_closure


def steer_closure(snapshot, capabilities):
    schema = getattr(capabilities, "rd_steer_schema", None)
    if schema not in (None, SCHEMA) or (schema is not None) != (
        "rd_steer_closure" in snapshot
    ):
        raise ValueError("Steer closure capability differs")
    if schema is None:
        return None
    value = snapshot["rd_steer_closure"]
    if type(value) is not dict or value.get("schema_version") != SCHEMA:
        raise ValueError("Steer closure schema differs")
    return value


def read_steer_closure(connection, capabilities, limits):
    if getattr(capabilities, "rd_steer_schema", None) is None:
        return None
    value = read_closure(
        connection, limits.max_files, maximum_bytes=limits.max_manifest_bytes
    )
    steer_closure({"rd_steer_closure": value}, capabilities)
    return value


def validate_steer_snapshot(snapshot, capabilities, maximum_rows):
    value = steer_closure(snapshot, capabilities)
    if value is not None:
        validate_closure(
            value,
            rd_source_closure=snapshot["rd_source_closure"],
            maximum_rows=maximum_rows,
        )
