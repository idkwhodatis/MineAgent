"""Explicit v11 run-family partition; never relabel a research closure version."""

V11 = "mineagent.research-restore-closure.v11"
V12 = "mineagent.research-restore-closure.v13"
LEGACY = "mineagent.strategy-search.v1"
NORMAL = "mineagent.oe-structured-provider-run.v1"


def legacy_shared_runs(research):
    rows = [
        r for r in research["external_bindings"]["runs"] if r["engine"] == "openevolve"
    ]
    if research.get("schema_version") not in {V11, V12}:
        return rows
    result = []
    seen = set()
    for row in rows:
        if row["id"] in seen:
            raise ValueError("Duplicate OE shared run")
        seen.add(row["id"])
        schema = row.get("config_schema_version")
        if schema not in (LEGACY, NORMAL):
            raise ValueError("Exact v11 OE config schema required")
        if schema == LEGACY:
            result.append(row)
    return result


def validate_partition(full_runs, research):
    shared = legacy_shared_runs(research)
    if research.get("schema_version") not in {V11, V12}:
        return shared
    expected = {r["id"] for r in shared}
    if len(full_runs) != len(expected) or {r["id"] for r in full_runs} != expected:
        raise ValueError("Legacy OE/v11 run partition differs")
    for row in full_runs:
        if (
            type(row.get("config")) is not dict
            or row["config"].get("schema_version") != LEGACY
        ):
            raise ValueError("Legacy OE full config schema differs")
    return shared


def require_read_family(rows, research_schema):
    if research_schema in {V11, V12}:
        for row in rows:
            if (
                type(row.get("config")) is not dict
                or row["config"].get("schema_version") != LEGACY
            ):
                raise ValueError("Unknown or misplaced OE run in legacy reader")
