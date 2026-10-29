"""Exact028 operation hooks after all predecessor physical/domain validators.

No activation, live grant, or inferred completion. New closure presence and its
research/RD pairing must agree; rejected executions remain no-snapshot failures.
"""

import json

SCHEMA = "mineagent.oe-structured-restore-closure.v1"
SCHEMA_V2 = "mineagent.oe-structured-restore-closure.v2"
PAIR_V2 = (
    "mineagent.research-restore-closure.v16",
    "mineagent.rd-source-restore-closure.v11",
)
RESEARCH = "mineagent.research-restore-closure.v13"
RD = "mineagent.rd-source-restore-closure.v8"
PAIRS = {
    (RESEARCH, RD),
    (
        "mineagent.research-restore-closure.v14",
        "mineagent.rd-source-restore-closure.v9",
    ),
    (
        "mineagent.research-restore-closure.v15",
        "mineagent.rd-source-restore-closure.v10",
    ),
}


def _schema(capabilities):
    schema = getattr(capabilities, "oe_structured_closure_schema", None)
    if schema not in (None, SCHEMA, SCHEMA_V2):
        raise ValueError("Unsupported normal OE closure capability")
    pair = (
        getattr(capabilities, "research_closure_schema", None),
        getattr(capabilities, "rd_source_closure_schema", None),
    )
    if schema is None:
        if any(value in pair for allowed in PAIRS | {PAIR_V2} for value in allowed):
            raise ValueError("Normal OE closure capability pair differs")
    elif not (
        (schema == SCHEMA and pair in PAIRS)
        or (schema == SCHEMA_V2 and pair == PAIR_V2)
    ):
        raise ValueError("Normal OE closure capability pair differs")
    return schema


def closure_for(snapshot, capabilities):
    schema = _schema(capabilities)
    if (schema is not None) != ("oe_structured_closure" in snapshot):
        raise ValueError("Normal OE closure presence differs")
    if schema is None:
        return None
    closure = snapshot["oe_structured_closure"]
    if type(closure) is not dict or closure.get("schema_version") != schema:
        raise ValueError("Normal OE closure schema differs")
    return closure


def read_closure(connection, capabilities, limits):
    if _schema(capabilities) is None:
        return None
    from backend.app.oe_structured_backup import read_closure as read

    closure = read(
        connection,
        limits.max_files,
        maximum_bytes=limits.max_manifest_bytes,
        schema=_schema(capabilities),
    )
    closure_for({"oe_structured_closure": closure}, capabilities)
    if (
        len(
            json.dumps(
                closure, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        )
        > limits.max_manifest_bytes
    ):
        raise ValueError("Normal OE closure metadata exceeds bound")
    return closure


def manifest_keys(snapshot, capabilities):
    closure = closure_for(snapshot, capabilities)
    if closure is None:
        return set()
    from backend.app.oe_structured_backup import manifest_keys as keys

    return keys(closure)


def validate_snapshot(
    snapshot, capabilities, read_bytes, maximum_rows, *, inventory, process_records
):
    closure = closure_for(snapshot, capabilities)
    if closure is None:
        return None
    if (
        snapshot["research_closure"].get("schema_version"),
        snapshot["rd_source_closure"].get("schema_version"),
    ) not in PAIRS | {PAIR_V2}:
        raise ValueError("Normal OE actual predecessor pair differs")
    if type(process_records) is not dict or set(process_records) != {
        "legacy_oe",
        "structured",
        "rejections",
    }:
        raise ValueError("Actual shared physical process records required")
    from backend.app.oe_structured_backup import validate_closure
    from backend.app.rdagent_numeric_failure_backup import validate_failure_links

    # Both are mandatory, even when either new table inventory is empty.
    validate_failure_links(
        snapshot["rd_source_closure"],
        snapshot["research_closure"],
        snapshot["native_closure"],
        snapshot["execution_closure"],
        process_records["rejections"],
        read_bytes,
        max_rows=maximum_rows,
    )
    context = None
    if _schema(capabilities) == SCHEMA_V2:
        from backend.app.workflow_oe_backup import build_context

        context = build_context(snapshot, maximum_rows)
    return validate_closure(
        closure,
        research_closure=snapshot["research_closure"],
        execution_closure=snapshot["execution_closure"],
        direct_analysis_closure=snapshot["direct_analysis_closure"],
        structured_closure=snapshot["structured_strategy_closure"],
        inventory=inventory,
        read_bytes=read_bytes,
        maximum_rows=maximum_rows,
        **({"workflow_context": context} if context is not None else {}),
    )
