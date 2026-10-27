"""Exact024 operation envelope and bounded retained-byte inventory dispatch.

The research closure performs the actual shared physical/domain validation once.
These helpers neither validate a process twice nor convert native binary exports
into JSON. Historical schemas cannot acquire an OE closure by adding a field.
"""

import json
from uuid import UUID

SCHEMA = "mineagent.openevolve-restore-closure.v1"


def _schema(capabilities):
    schema = getattr(capabilities, "openevolve_closure_schema", None)
    if schema not in (None, SCHEMA):
        raise ValueError("Unsupported OE closure capability")
    return schema


def openevolve_closure(snapshot, capabilities):
    schema = _schema(capabilities)
    if (schema is not None) != ("openevolve_closure" in snapshot):
        raise ValueError("OE closure capability differs")
    if schema is None:
        return None
    closure = snapshot["openevolve_closure"]
    if type(closure) is not dict or closure.get("schema_version") != schema:
        raise ValueError("OE closure version differs")
    return closure


def read_openevolve_closure(connection, capabilities, limits):
    if _schema(capabilities) is None:
        return None
    from backend.app.openevolve_backup_shape import read_closure

    closure = read_closure(
        connection, limits.max_files, maximum_bytes=limits.max_manifest_bytes
    )
    openevolve_closure({"openevolve_closure": closure}, capabilities)
    if (
        len(
            json.dumps(
                closure, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        )
        > limits.max_manifest_bytes
    ):
        raise ValueError("OE closure exceeds metadata budget")
    return closure


def openevolve_manifest_keys(snapshot, capabilities):
    """Additional byte buffers, not JSON readers; all remain globally bounded.

    Registered research objects and control journals are already inventoried by
    research.manifest_keys. Only native binary outputs need this additive set.
    Exact process receipt/physical authority is verified after the pinned scan.
    """
    if openevolve_closure(snapshot, capabilities) is None:
        return set()
    from runtimes.openevolve_evaluator.journal import NativeExtraction

    tables = snapshot["research_closure"]["tables"]
    admissions = {row["id"]: row for row in tables["research_process_admissions"]}
    if len(admissions) != len(tables["research_process_admissions"]):
        raise ValueError("Duplicate process admission")
    keys = set()
    for row in tables["research_process_receipts"]:
        if row["kind"] != "extraction":
            continue
        admission = admissions.get(row["admission_id"])
        if admission is None:
            raise ValueError("Extraction without admission")
        if admission["profile"] != "mineagent.openevolve-native-evaluator.v1":
            continue
        for field in ("owner_id", "run_id", "attempt_id", "generation"):
            if row[field] != admission[field]:
                raise ValueError("OE extraction scope differs")
        owner, identity = admission["owner_id"], admission["id"]
        if str(UUID(owner)) != owner or str(UUID(identity)) != identity:
            raise ValueError("OE canonical process path identity required")
        extraction = NativeExtraction.model_validate_json(
            json.dumps(row["payload"], allow_nan=False)
        )
        key = f"research-processes/{owner}/{identity}/output/{extraction.basename}"
        if key in keys:
            raise ValueError("Duplicate OE extraction")
        keys.add(key)
    return keys
