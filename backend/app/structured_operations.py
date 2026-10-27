"""Exact structured-strategy operation dispatch, not live execution authority."""

import json

SCHEMA = "mineagent.structured-strategy-restore-closure.v1"


def _schema(capabilities):
    schema = getattr(capabilities, "structured_strategy_schema", None)
    if schema not in (None, SCHEMA):
        raise ValueError("Unsupported structured strategy closure")
    return schema


def _closure(snapshot, capabilities):
    schema = _schema(capabilities)
    if (schema is not None) != ("structured_strategy_closure" in snapshot):
        raise ValueError("Structured strategy closure capability differs")
    if schema is None:
        return None
    closure = snapshot["structured_strategy_closure"]
    if type(closure) is not dict or closure.get("schema_version") != schema:
        raise ValueError("Structured strategy closure version differs")
    return closure


def read_structured_strategy_closure(connection, capabilities, limits):
    if _schema(capabilities) is None:
        return None
    from backend.app.structured_factor_backup import read_closure

    closure = read_closure(
        connection, limits.max_files, maximum_bytes=limits.max_manifest_bytes
    )
    _closure({"structured_strategy_closure": closure}, capabilities)
    if (
        len(
            json.dumps(
                closure, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        )
        > limits.max_manifest_bytes
    ):
        raise ValueError("Structured strategy closure exceeds metadata budget")
    return closure


def structured_strategy_manifest_keys(snapshot, capabilities):
    closure = _closure(snapshot, capabilities)
    if closure is None:
        return set()
    from backend.app.structured_factor_backup import manifest_keys

    return manifest_keys(closure)


def validate_structured_strategy_snapshot(
    snapshot, capabilities, read_bytes, maximum_rows
):
    closure = _closure(snapshot, capabilities)
    if closure is None:
        return None
    from backend.app.structured_factor_backup import validate_closure

    return validate_closure(
        closure,
        research_closure=snapshot["research_closure"],
        execution_closure=snapshot["execution_closure"],
        direct_analysis_closure=snapshot["direct_analysis_closure"],
        read_bytes=read_bytes,
        maximum_rows=maximum_rows,
    )
