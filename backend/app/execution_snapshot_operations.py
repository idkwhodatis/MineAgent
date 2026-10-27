"""Exact versioned backup hooks; invoke after source/native dependency validation."""

from backend.app import execution_snapshot_backup as execution


def read_execution_snapshot_closure(connection, capabilities, limits):
    schema = getattr(capabilities, "execution_snapshot_schema", None)
    if schema is None:
        return None, []
    if schema not in {execution.SCHEMA, execution.SCHEMA_V2}:
        raise ValueError("Unsupported execution snapshot schema")
    closure = execution.read_closure(
        connection,
        limits.max_files,
        maximum_bytes=limits.max_manifest_bytes,
        schema=schema,
    )
    refs = {}
    for row in closure["tables"]["factor_execution_snapshot_objects"]:
        value = {
            "path": row["storage_key"],
            "sha256": row["sha256"],
            "size_bytes": row["size_bytes"],
        }
        if refs.setdefault(value["path"], value) != value:
            raise ValueError("Conflicting execution physical reference")
    return closure, list(refs.values())


def execution_manifest_keys(snapshot, capabilities):
    schema = getattr(capabilities, "execution_snapshot_schema", None)
    if (schema is not None) != ("execution_closure" in snapshot):
        raise ValueError("Execution closure capability differs")
    if schema is None:
        return set()
    if schema not in {execution.SCHEMA, execution.SCHEMA_V2}:
        raise ValueError("Unsupported execution closure")
    return execution.manifest_keys(snapshot["execution_closure"])


def validate_execution_snapshot(
    snapshot, capabilities, check, read_json, maximum_rows, *, inventory
):
    schema = getattr(capabilities, "execution_snapshot_schema", None)
    if (schema is not None) != ("execution_closure" in snapshot):
        raise ValueError("Execution closure capability differs")
    if schema is None:
        return None
    if schema not in {execution.SCHEMA, execution.SCHEMA_V2}:
        raise ValueError("Unsupported execution closure")
    return execution.validate_closure(
        snapshot["execution_closure"],
        check,
        read_json,
        maximum_rows,
        inventory_rows=inventory,
        research_closure=snapshot["research_closure"],
        rd_source_closure=snapshot["rd_source_closure"],
        native_closure=snapshot["native_closure"],
        **(
            {"direct_analysis_closure": snapshot.get("direct_analysis_closure")}
            if schema == execution.SCHEMA_V2
            else {}
        ),
    )
