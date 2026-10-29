"""026 immutable steer versions and historical run selections, not active grants.

Current head/disabled state is retained as mutable UI state. It never changes the
meaning or validity of a previously accepted run's exact ordered snapshot.
"""

from collections import defaultdict
from datetime import datetime
from uuid import UUID

from sqlalchemy import text

from packages.factors.contracts import canonical_bytes, digest

SCHEMA = "mineagent.rd-steer-closure.v1"
FIELDS = {
    "rd_steers": "id owner_id current_version_id disabled revision created_at updated_at",
    "rd_steer_versions": "id owner_id steer_id ordinal kind text text_sha256 content_canonical content_sha256 created_at",
}


def read_closure(connection, maximum_rows, *, maximum_bytes=32_000_000):
    if (
        type(maximum_rows) is not int
        or maximum_rows < 1
        or type(maximum_bytes) is not int
        or maximum_bytes < 1
    ):
        raise ValueError("Steer closure bound")
    tables, total, size = {}, 0, 0
    for name, fields in {**FIELDS, "users": "id"}.items():
        where = (
            " WHERE id IN (SELECT owner_id FROM rd_steers)" if name == "users" else ""
        )
        result = connection.execute(
            text(
                f"SELECT to_jsonb(t) FROM (SELECT {','.join(fields.split())} FROM {name}{where} ORDER BY id LIMIT :limit) t"
            ).execution_options(stream_results=True, yield_per=1),
            {"limit": maximum_rows + 1},
        )
        rows = []
        try:
            for row in result.scalars():
                total += 1
                size += len(canonical_bytes(row)) + 1
                if total > maximum_rows or size > maximum_bytes:
                    raise ValueError("Steer closure bound")
                rows.append(row)
        finally:
            result.close()
        tables[name] = rows
    value = {
        "schema_version": SCHEMA,
        "tables": {k: tables[k] for k in FIELDS},
        "owners": tables["users"],
    }
    if len(canonical_bytes(value)) > maximum_bytes:
        raise ValueError("Steer closure bound")
    return value


def _uuid(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError("Canonical steer UUID required")
    return value


def _time(value):
    if type(value) is not str:
        raise ValueError("Steer timestamp required")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Steer timestamp timezone required")
    return parsed


def validate_closure(closure, *, rd_source_closure, maximum_rows):
    from packages.rdagent_adapter.steer import SteerSnapshot, SteerVersion

    if (
        type(closure) is not dict
        or set(closure) != {"schema_version", "tables", "owners"}
        or closure["schema_version"] != SCHEMA
        or type(closure["tables"]) is not dict
        or set(closure["tables"]) != set(FIELDS)
        or rd_source_closure.get("schema_version")
        not in {
            "mineagent.rd-source-restore-closure.v7",
            "mineagent.rd-source-restore-closure.v11",
            "mineagent.rd-source-restore-closure.v5",
            "mineagent.rd-source-restore-closure.v8",
            "mineagent.rd-source-restore-closure.v9",
            "mineagent.rd-source-restore-closure.v6",
            "mineagent.rd-source-restore-closure.v10",
        }
        or type(maximum_rows) is not int
        or maximum_rows < 1
        or len(canonical_bytes(closure)) > 32_000_000
    ):
        raise ValueError("Steer exact closure grammar differs")
    from backend.app.rdagent_profile_backup import require_config_revision

    for run in rd_source_closure.get("external_bindings", {}).get("runs", []):
        require_config_revision(
            run["config"], source_schema=rd_source_closure["schema_version"]
        )
    indexes, total = {}, 0
    for name, fields in {**FIELDS, "owners": "id"}.items():
        rows = closure["owners"] if name == "owners" else closure["tables"][name]
        if type(rows) is not list:
            raise ValueError("Steer row inventory required")
        total += len(rows)
        if total > maximum_rows:
            raise ValueError("Steer closure bound")
        index = {}
        for row in rows:
            if type(row) is not dict or set(row) != set(fields.split()):
                raise ValueError("Steer exact row grammar differs")
            for key in {
                "id",
                "owner_id",
                "steer_id",
                "current_version_id",
            } & row.keys():
                _uuid(row[key])
            if row["id"] in index:
                raise ValueError("Duplicate steer row")
            index[row["id"]] = row
        indexes[name] = index
    roots, versions, owners = (
        indexes[k] for k in ("rd_steers", "rd_steer_versions", "owners")
    )
    grouped, documents = defaultdict(list), {}
    for row in versions.values():
        root = roots.get(row["steer_id"])
        if root is None or row["owner_id"] != root["owner_id"]:
            raise ValueError("Steer version owner/root differs")
        document = SteerVersion.model_validate_json(row["content_canonical"])
        if (
            canonical_bytes(document).decode() != row["content_canonical"]
            or digest(document) != row["content_sha256"]
            or str(document.version_id) != row["id"]
            or str(document.steer_id) != row["steer_id"]
            or (document.ordinal, document.kind, document.text, document.text_sha256)
            != (row["ordinal"], row["kind"], row["text"], row["text_sha256"])
            or type(row["ordinal"]) is not int
        ):
            raise ValueError("Steer actual immutable content differs")
        _time(row["created_at"])
        grouped[row["steer_id"]].append(row)
        documents[row["id"]] = document
    for root in roots.values():
        rows = sorted(grouped[root["id"]], key=lambda row: row["ordinal"])
        if (
            root["owner_id"] not in owners
            or not rows
            or type(root["disabled"]) is not bool
            or type(root["revision"]) is not int
            or not len(rows) <= root["revision"] <= 1_000_000
            or [row["ordinal"] for row in rows] != list(range(1, len(rows) + 1))
            or root["current_version_id"] != rows[-1]["id"]
            or _time(root["updated_at"]) < _time(root["created_at"])
        ):
            raise ValueError("Steer current inventory differs")
    if set(owners) != {row["owner_id"] for row in roots.values()}:
        raise ValueError("Steer referenced owner inventory differs")
    for run in rd_source_closure["external_bindings"]["runs"]:
        config = run["config"]
        if config.get("schema_version") == "mineagent.factor-research.v1":
            if "steer_snapshot" in config or "steer_snapshot_sha256" in config:
                raise ValueError("Historical config cannot carry steer snapshot")
            continue
        if (
            rd_source_closure["schema_version"]
            in {
                "mineagent.rd-source-restore-closure.v11",
                "mineagent.rd-source-restore-closure.v9",
                "mineagent.rd-source-restore-closure.v10",
            }
            and config.get("schema_version") == "mineagent.factor-research.v3"
        ):
            from packages.rdagent_adapter.workflow_config import (
                WorkflowFactorResearchConfig,
            )

            workflow = WorkflowFactorResearchConfig.model_validate_json(
                canonical_bytes(config)
            )
            if (
                str(workflow.workflow_child_run_id) != run["id"]
                or str(workflow.workflow_inputs.binding.owner_id) != run["owner_id"]
                or workflow.steer_snapshot.versions
            ):
                raise ValueError(
                    "Workflow manual origin must use exact combined snapshot"
                )
            # The final workflow closure independently resolves every selected
            # manual/source version. Never relabel this as a user submission.
            continue
        if config.get("schema_version") != "mineagent.factor-research.v2":
            raise ValueError("Unknown steer research config")
        snapshot = SteerSnapshot.model_validate_json(
            canonical_bytes(config["steer_snapshot"])
        )
        if (
            str(snapshot.owner_id) != run["owner_id"]
            or snapshot.sha256 != config["steer_snapshot_sha256"]
        ):
            raise ValueError("Steer historical run snapshot differs")
        for version in snapshot.versions:
            row = versions.get(str(version.version_id))
            if (
                row is None
                or row["owner_id"] != run["owner_id"]
                or documents[row["id"]] != version
            ):
                raise ValueError("Steer historical selected version differs")
