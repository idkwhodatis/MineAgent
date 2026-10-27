"""023 immutable submission closure, supplementary to validated source/analysis.

No filesystem parser or live lease is created. Source/plan physical closure must
already have been checked by the revision022 validators before this reader runs.
"""

from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import text

from backend.app.rdagent_submission_receipts import validate_saved_binding
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes

SCHEMA = "mineagent.rd-submission-restore-closure.v1"
FIELDS = {
    "factor_research_submissions": "id owner_id run_id plan_id idempotency_key request_canonical request_sha256 preflight_sha256 config_sha256 runtime_profile_sha256 catalog_revision created_at",
    "outbox_events": "id run_id generation created_at dispatched_at job_id",
}


def read_closure(connection, maximum_rows, *, maximum_bytes=32000000):
    if (
        type(maximum_rows) is not int
        or maximum_rows < 1
        or type(maximum_bytes) is not int
        or maximum_bytes < 1
    ):
        raise ValueError("Submission closure bound")
    tables = {}
    total = size = 0
    for name, fields in FIELDS.items():
        where = (
            " WHERE run_id IN (SELECT run_id FROM factor_research_submissions)"
            if name == "outbox_events"
            else ""
        )
        rows = []
        size += 2
        if size > maximum_bytes:
            raise ValueError("Submission closure bound")
        result = connection.execute(
            text(
                f"SELECT to_jsonb(t) FROM (SELECT {','.join(fields.split())} FROM {name}{where} ORDER BY id LIMIT :limit) t"
            ).execution_options(stream_results=True, yield_per=1),
            {"limit": maximum_rows + 1},
        )
        try:
            for row in result.scalars():
                total += 1
                size += len(canonical_bytes(row)) + bool(rows)
                if total > maximum_rows or size > maximum_bytes:
                    raise ValueError("Submission closure bound")
                rows.append(row)
        finally:
            result.close()
        tables[name] = rows
    return {"schema_version": SCHEMA, "tables": tables}


def _namespace(row, ids):
    return SimpleNamespace(
        **{k: UUID(v) if k in ids and v is not None else v for k, v in row.items()}
    )


def validate_closure(
    closure,
    *,
    rd_source_closure,
    direct_analysis_closure,
    research_closure,
    maximum_rows,
):
    if (
        type(closure) is not dict
        or set(closure) != {"schema_version", "tables"}
        or closure["schema_version"] != SCHEMA
        or type(closure["tables"]) is not dict
        or set(closure["tables"]) != set(FIELDS)
    ):
        raise ValueError("Submission closure grammar")
    tables = closure["tables"]
    total = 0
    for name, fields in FIELDS.items():
        rows = tables[name]
        if type(rows) is not list:
            raise ValueError("Submission table rows")
        total += len(rows)
        if total > maximum_rows or len(canonical_bytes(rows)) > 32000000:
            raise ValueError("Submission closure bound")
        ids = set()
        for row in rows:
            if (
                type(row) is not dict
                or set(row) != set(fields.split())
                or type(row["id"]) is not str
                or str(UUID(row["id"])) != row["id"]
                or row["id"] in ids
            ):
                raise ValueError("Submission exact row inventory")
            ids.add(row["id"])
    runs = {row["id"]: row for row in rd_source_closure["external_bindings"]["runs"]}
    plans = {
        row["id"]: row
        for row in direct_analysis_closure["tables"]["factor_analysis_plans"]
    }
    budgets = {
        row["run_id"]: row
        for row in research_closure["tables"]["research_budget_controls"]
    }
    outboxes = {}
    for row in tables["outbox_events"]:
        if (
            row["run_id"] in outboxes
            or type(row["generation"]) is not int
            or row["generation"] != 1
        ):
            raise ValueError("Submission fresh outbox differs")
        outboxes[row["run_id"]] = row
    keys = set()
    used_runs = set()
    used_plans = set()
    for row in tables["factor_research_submissions"]:
        key = (row["owner_id"], row["idempotency_key"])
        if key in keys or row["run_id"] in used_runs or row["plan_id"] in used_plans:
            raise ValueError("Submission uniqueness differs")
        keys.add(key)
        used_runs.add(row["run_id"])
        used_plans.add(row["plan_id"])
        run = runs[row["run_id"]]
        plan = plans[row["plan_id"]]
        try:
            validate_saved_binding(
                _namespace(row, {"id", "owner_id", "run_id", "plan_id"}),
                _namespace(
                    run,
                    {
                        "id",
                        "owner_id",
                        "dataset_version_id",
                        "strategy_version_id",
                        "experiment_id",
                        "split_version_id",
                        "final_selection_id",
                        "active_attempt_id",
                        "latest_checkpoint_id",
                        "resume_checkpoint_id",
                    },
                ),
                _namespace(
                    plan,
                    {"id", "owner_id", "run_id", "spec_id", "label_source_dataset_id"},
                ),
                UUID(row["owner_id"]),
                row["idempotency_key"],
            )
        except (DomainError, ValueError, TypeError, AttributeError) as exc:
            raise ValueError("Submission exact canonical graph differs") from exc
        if (
            row["run_id"] not in outboxes
            or budgets[row["run_id"]]["limits"] != run["config"]["budget"]
            or budgets[row["run_id"]]["owner_id"] != row["owner_id"]
        ):
            raise ValueError("Submission outbox/budget closure differs")
    if set(outboxes) != used_runs:
        raise ValueError("Unreferenced submission outbox")
