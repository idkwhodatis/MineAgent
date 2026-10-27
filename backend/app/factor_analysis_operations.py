"""Exact direct-analysis operation dispatch, not live execution authority."""

import json

SCHEMA = "mineagent.direct-analysis-restore-closure.v1"


def _schema(capabilities):
    schema = getattr(capabilities, "direct_analysis_schema", None)
    if schema not in (None, SCHEMA):
        raise ValueError("Unsupported direct analysis closure")
    return schema


def _closure(snapshot, capabilities):
    schema = _schema(capabilities)
    if (schema is not None) != ("direct_analysis_closure" in snapshot):
        raise ValueError("Direct analysis closure capability differs")
    if schema is None:
        return None
    closure = snapshot["direct_analysis_closure"]
    if type(closure) is not dict or closure.get("schema_version") != schema:
        raise ValueError("Direct analysis closure version differs")
    return closure


def read_direct_analysis_closure(connection, capabilities, limits):
    if _schema(capabilities) is None:
        return None
    from backend.app.factor_analysis_backup import read_closure

    closure = read_closure(
        connection, limits.max_files, maximum_bytes=limits.max_manifest_bytes
    )
    _closure({"direct_analysis_closure": closure}, capabilities)
    if (
        len(
            json.dumps(
                closure, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        )
        > limits.max_manifest_bytes
    ):
        raise ValueError("Direct analysis closure exceeds metadata budget")
    return closure


def direct_analysis_manifest_keys(snapshot, capabilities):
    closure = _closure(snapshot, capabilities)
    if closure is None:
        return set()
    from backend.app.factor_analysis_backup import manifest_keys

    return manifest_keys(closure)


def validate_direct_analysis_snapshot(snapshot, capabilities, read_bytes, maximum_rows):
    closure = _closure(snapshot, capabilities)
    if closure is None:
        return None
    from backend.app.factor_analysis_backup import validate_closure

    return validate_closure(
        closure,
        research_closure=snapshot["research_closure"],
        rd_source_closure=snapshot["rd_source_closure"],
        execution_closure=snapshot["execution_closure"],
        factor_closure=snapshot["factor_closure"],
        read_bytes=read_bytes,
        maximum_rows=maximum_rows,
    )
