"""Explicit native snapshot hooks; historical capabilities retain opaque orphans."""

from backend.app import factor_native_backup as native


def read_native_snapshot(connection, capabilities, limits):
    schema = getattr(capabilities, "native_closure_schema", None)
    if schema is None:
        return None, []
    if schema != native.SCHEMA:
        raise ValueError("Unsupported native snapshot capability")
    closure = native.read_closure(
        connection, limits.max_files, maximum_bytes=limits.max_manifest_bytes
    )
    references = {}
    for row in closure["tables"]["factor_native_objects"]:
        ref = {
            "path": row["storage_key"],
            "sha256": row["sha256"],
            "size_bytes": row["size_bytes"],
        }
        old = references.setdefault(ref["path"], ref)
        if old != ref:
            raise ValueError("Conflicting native physical registration")
    return closure, list(references.values())


def validate_native_snapshot(
    snapshot, capabilities, check, read_json, maximum_rows, *, inventory
):
    schema = getattr(capabilities, "native_closure_schema", None)
    if (schema is not None) != ("native_closure" in snapshot):
        raise ValueError("Native snapshot capability differs")
    if schema is None:
        # Old generic backup may retain .h5 orphans, but cannot register them.
        return
    if (
        schema != native.SCHEMA
        or snapshot["native_closure"].get("schema_version") != schema
    ):
        raise ValueError("Native closure version differs")
    native.validate_closure(
        snapshot["native_closure"],
        check,
        read_json,
        maximum_rows,
        inventory=inventory,
        research_closure=snapshot["research_closure"],
        rd_source_closure=snapshot["rd_source_closure"],
    )


def native_manifest_keys(snapshot, capabilities):
    schema = getattr(capabilities, "native_closure_schema", None)
    if (schema is not None) != ("native_closure" in snapshot):
        raise ValueError("Native snapshot capability differs")
    if schema is None:
        return set()
    if schema != native.SCHEMA:
        raise ValueError("Native closure version differs")
    return native.manifest_keys(snapshot["native_closure"])
