"""Bounded024 retained domain rows and exact references, not complete acceptance.

The caller must separately validate physical research-v8 and OE typed closure.
This module never grants a process launch, worker ticket, or checkpoint resume.
"""

import math
from uuid import UUID

from sqlalchemy import text

from backend.app.openevolve_backup_inventory import (
    BIGINT_COLUMNS,
    FIELDS,
    FLOAT_COLUMNS,
    FOREIGN_KEYS,
    INTEGER_COLUMNS,
    NONNULL_COLUMNS,
    PRIMARY_KEYS,
    SCHEMA,
    TEXT_COLUMNS,
    UNIQUE_KEYS,
    UUID_COLUMNS,
)
from packages.openevolve_adapter.checkpoint_native import canonical

EXTERNAL_FIELDS = {
    "runs": "id owner_id request_sha256 config purpose engine dataset_version_id strategy_version_id experiment_id split_version_id final_selection_id status generation active_attempt_id research_image_digest portable_policy_image_digest submission_binding latest_checkpoint_id resume_checkpoint_id",
    "dataset_versions": "id owner_id content_sha256 storage_key manifest start_date end_date market",
    "strategy_versions": "id owner_id content_sha256 code task mode manifest parameters",
}


def _limits(rows, size):
    if type(rows) is not int or rows < 1 or type(size) is not int or size < 1:
        raise ValueError("Positive exact OE closure limits required")


def read_closure(
    connection, maximum_rows, *, maximum_bytes=32_000_000, research_schema=None
):
    _limits(maximum_rows, maximum_bytes)
    total = size = 0

    def read(name, fields, order, where=""):
        nonlocal total, size
        rows = []
        size += 2
        if size > maximum_bytes:
            raise ValueError("OE closure exceeds bounds")
        statement = text(
            f"SELECT to_jsonb(t) FROM (SELECT {','.join(fields.split())} FROM {name}{where} ORDER BY {order} LIMIT :maximum) t"
        ).execution_options(stream_results=True, yield_per=1)
        result = connection.execute(statement, {"maximum": maximum_rows + 1})
        try:
            for row in result.scalars():
                total += 1
                size += len(canonical(row)) + bool(rows)
                if total > maximum_rows or size > maximum_bytes:
                    raise ValueError("OE closure exceeds bounds")
                rows.append(row)
        finally:
            result.close()
        return rows

    tables = {
        name: read(name, fields, ",".join(PRIMARY_KEYS[name]))
        for name, fields in FIELDS.items()
    }
    from backend.app.openevolve_backup_partition import (
        NORMAL,
        V11,
        V12,
        require_read_family,
    )

    run_where = " WHERE engine='openevolve'"
    if research_schema in {V11, V12}:
        # Exclude only the explicitly versioned normal family. Missing/unknown
        # config schemas remain selected and are rejected below, not omitted.
        run_where += f" AND config->>'schema_version' IS DISTINCT FROM '{NORMAL}'"
    external = {
        "runs": read("research_runs", EXTERNAL_FIELDS["runs"], "id", run_where),
        "dataset_versions": read(
            "dataset_versions",
            EXTERNAL_FIELDS["dataset_versions"],
            "id",
            " WHERE id IN (SELECT dataset_id FROM strategy_search_specs)",
        ),
        "strategy_versions": read(
            "strategy_versions",
            EXTERNAL_FIELDS["strategy_versions"],
            "id",
            " WHERE id IN (SELECT original_strategy_id FROM strategy_search_specs UNION SELECT prepared_strategy_id FROM strategy_search_specs)",
        ),
    }
    require_read_family(external["runs"], research_schema)
    return {"schema_version": SCHEMA, "tables": tables, "external_bindings": external}


def _scalar_key(value):
    if type(value) is float and not math.isfinite(value):
        raise ValueError("Nonfinite OE metadata")
    if isinstance(value, (dict, list)):
        raise TypeError("Scalar reference required")
    return value


def validate_references(
    closure, research_closure, *, maximum_rows, maximum_bytes=32_000_000
):
    """Exact frozen schema/FKs only; no caller may interpret this as full restore."""
    _limits(maximum_rows, maximum_bytes)
    if (
        type(closure) is not dict
        or set(closure) != {"schema_version", "tables", "external_bindings"}
        or closure["schema_version"] != SCHEMA
        or type(closure["tables"]) is not dict
        or set(closure["tables"]) != set(FIELDS)
        or type(closure["external_bindings"]) is not dict
        or set(closure["external_bindings"]) != set(EXTERNAL_FIELDS)
    ):
        raise ValueError("Exact OE closure inventory required")
    indexes, total, size = {}, 0, 0
    for name, fields in {**FIELDS, **EXTERNAL_FIELDS}.items():
        rows = closure["tables" if name in FIELDS else "external_bindings"][name]
        if type(rows) is not list:
            raise ValueError("OE rows must be lists")
        size += 2
        primary = PRIMARY_KEYS.get(name, ("id",))
        seen = set()
        for ordinal, row in enumerate(rows):
            if type(row) is not dict or set(row) != set(fields.split()):
                raise ValueError("Exact OE row fields required")
            if any(row[k] is None for k in NONNULL_COLUMNS.get(name, ())):
                raise ValueError("Null mandatory OE field")
            for key in INTEGER_COLUMNS.get(name, ()):
                bits = 63 if key in BIGINT_COLUMNS.get(name, ()) else 31
                if row[key] is not None and (
                    type(row[key]) is not int or not -(2**bits) <= row[key] < 2**bits
                ):
                    raise ValueError("Exact PostgreSQL OE integer required")
            for key, limit in TEXT_COLUMNS.get(name, {}).items():
                if row[key] is not None and (
                    type(row[key]) is not str
                    or (limit is not None and len(row[key]) > limit)
                ):
                    raise ValueError("Bounded OE text required")
            for key in FLOAT_COLUMNS.get(name, ()):
                if row[key] is not None and (
                    type(row[key]) not in (int, float) or not math.isfinite(row[key])
                ):
                    raise ValueError("Finite OE float required")
            total += 1
            size += len(canonical(row)) + bool(ordinal)
            if total > maximum_rows or size > maximum_bytes:
                raise ValueError("OE closure exceeds bounds")
            identity = tuple(_scalar_key(row[k]) for k in primary)
            if identity in seen or any(value is None for value in identity):
                raise ValueError("Duplicate or missing OE identity")
            seen.add(identity)
            for key in UUID_COLUMNS.get(name, ("id", "owner_id")):
                value = row[key]
                if value is not None and (
                    type(value) is not str or str(UUID(value)) != value
                ):
                    raise ValueError("Canonical OE UUID required")
        for columns in UNIQUE_KEYS.get(name, ()):
            keys = set()
            for row in rows:
                key = tuple(_scalar_key(row[k]) for k in columns)
                # PostgreSQL UNIQUE permits repeated tuples containing NULL.
                if any(v is None for v in key):
                    continue
                if key in keys:
                    raise ValueError("Duplicate OE unique tuple")
                keys.add(key)
        indexes[name] = rows
    from backend.app.openevolve_backup_partition import V11, V12, validate_partition

    legacy_runs = validate_partition(
        closure["external_bindings"]["runs"], research_closure
    )
    # The shared closure must already have passed its own typed/budget/physical
    # checks. This consumer never supplies omitted rows or coerces scope types.
    indexes.update(research_closure["tables"])
    indexes["research_runs"] = (
        legacy_runs
        if research_closure.get("schema_version") in {V11, V12}
        else research_closure["external_bindings"]["runs"]
    )
    indexes["execution_attempts"] = research_closure["external_bindings"]["attempts"]
    references = {}
    for name, local, remote, columns in FOREIGN_KEYS:
        cache_key = (remote, columns)
        if cache_key not in references:
            references[cache_key] = {
                tuple(_scalar_key(row[k]) for k in columns) for row in indexes[remote]
            }
        for row in indexes[name]:
            values = tuple(_scalar_key(row[k]) for k in local)
            # PostgreSQL MATCH SIMPLE: optional compound references may be NULL;
            # conditional completeness is enforced by the typed semantic layer.
            if any(value is None for value in values):
                continue
            if values not in references[cache_key]:
                raise ValueError("OE retained reference differs")
    return indexes
