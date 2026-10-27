"""026 supplementary seed declaration and retained-parent closure, never resume.

This component runs before the research/RD validators to reconstruct their
expected seeded bootstraps. Its frozen return value is data only: whole-backup
acceptance still requires every independent physical/domain/final-link validator.
No caller bootstrap, historical evaluation status or current lease grants seed
identity. Parent code is opaque UTF-8, never imported or executed.
"""

import hashlib
import re
from dataclasses import dataclass
from types import MappingProxyType, SimpleNamespace
from uuid import UUID

from sqlalchemy import text

from backend.app.rdagent_seed_services import read_saved_seed
from backend.app.rdagent_seed_submission import validate_saved_seed_binding
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.rdagent_adapter.seed_parent import ResolvedSeedParent

SCHEMA = "mineagent.rd-seed-restore-closure.v1"
RESEARCH = "mineagent.research-restore-closure.v10"
RD = "mineagent.rd-source-restore-closure.v5"
UUID_FIELDS = frozenset(
    {
        "id",
        "owner_id",
        "run_id",
        "spec_id",
        "analysis_plan_id",
        "plan_id",
        "parent_factor_id",
        "parent_factor_version_id",
        "parent_code_object_id",
        "dataset_version_id",
        "strategy_version_id",
        "experiment_id",
        "split_version_id",
        "final_selection_id",
        "active_attempt_id",
        "latest_checkpoint_id",
        "resume_checkpoint_id",
        "label_source_dataset_id",
    }
)
FIELDS = {
    "factor_seeded_submissions": "id owner_id run_id spec_id analysis_plan_id idempotency_key request_canonical request_sha256 preflight_sha256 config_sha256 runtime_profile_sha256 parent_factor_id parent_factor_version_id parent_ordinal parent_code_object_id parent_code_sha256 parent_code_size_bytes parent_definition_sha256 parent_provenance_sha256 parent_provenance_canonical parent_binding_sha256 created_at",
    "outbox_events": "id run_id generation created_at dispatched_at job_id",
}


@dataclass(frozen=True)
class PreparedSeed:
    submission_id: UUID
    parent: ResolvedSeedParent


def read_closure(connection, maximum_rows, *, maximum_bytes=32_000_000):
    if (
        type(maximum_rows) is not int
        or maximum_rows < 1
        or type(maximum_bytes) is not int
        or maximum_bytes < 1
    ):
        raise ValueError("Seed closure bound")
    tables, total, size = {}, 0, 0
    for name, fields in FIELDS.items():
        where = (
            " WHERE run_id IN (SELECT run_id FROM factor_seeded_submissions)"
            if name == "outbox_events"
            else ""
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
                    raise ValueError("Seed closure bound")
                rows.append(row)
        finally:
            result.close()
        tables[name] = rows
    value = {"schema_version": SCHEMA, "tables": tables}
    if len(canonical_bytes(value)) > maximum_bytes:
        raise ValueError("Seed closure bound")
    return value


def _uuid(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError("Canonical seed UUID required")
    return UUID(value)


def _object(row):
    return SimpleNamespace(
        **{
            key: _uuid(value) if key in UUID_FIELDS and value is not None else value
            for key, value in row.items()
        }
    )


def _index(rows, maximum_rows):
    if type(rows) is not list or len(rows) > maximum_rows:
        raise ValueError("Seed related inventory bound")
    result = {}
    for row in rows:
        if type(row) is not dict or row.get("id") in result:
            raise ValueError("Seed related duplicate row")
        _uuid(row["id"])
        result[row["id"]] = row
    return result


def validate_closure(
    closure,
    *,
    rd_source_closure,
    research_closure,
    factor_closure,
    direct_analysis_closure,
    rd_submission_closure,
    read_bytes,
    maximum_rows,
):
    """Return exact run→parent data after real code-byte and declaration checks."""
    if (
        type(closure) is not dict
        or set(closure) != {"schema_version", "tables"}
        or closure["schema_version"] != SCHEMA
        or type(closure["tables"]) is not dict
        or set(closure["tables"]) != set(FIELDS)
        or rd_source_closure.get("schema_version") != RD
        or research_closure.get("schema_version") != RESEARCH
        or factor_closure.get("schema_version") != "mineagent.factor-restore-closure.v2"
        or direct_analysis_closure.get("schema_version")
        != "mineagent.direct-analysis-restore-closure.v1"
        or rd_submission_closure.get("schema_version")
        != "mineagent.rd-submission-restore-closure.v1"
    ):
        raise ValueError("Seed explicit closure tuple differs")
    if (
        type(maximum_rows) is not int
        or maximum_rows < 1
        or len(canonical_bytes(closure)) > 32_000_000
    ):
        raise ValueError("Seed closure bound")
    total = 0
    for name, fields in FIELDS.items():
        rows = closure["tables"][name]
        _index(rows, maximum_rows)
        total += len(rows)
        for row in rows:
            if set(row) != set(fields.split()):
                raise ValueError("Seed exact row inventory differs")
            for key, value in row.items():
                if key in UUID_FIELDS and value is not None:
                    _uuid(value)
        if total > maximum_rows:
            raise ValueError("Seed closure bound")
    rd = rd_source_closure["tables"]
    runs = _index(rd_source_closure["external_bindings"]["runs"], maximum_rows)
    specs = _index(rd["factor_research_specs"], maximum_rows)
    plans = _index(
        direct_analysis_closure["tables"]["factor_analysis_plans"], maximum_rows
    )
    factors = _index(factor_closure["tables"]["factors"], maximum_rows)
    versions = _index(factor_closure["tables"]["factor_versions"], maximum_rows)
    objects = _index(factor_closure["tables"]["factor_objects"], maximum_rows)
    budgets = {
        row["run_id"]: row
        for row in research_closure["tables"]["research_budget_controls"]
    }
    fresh = rd_submission_closure["tables"]["factor_research_submissions"]
    keys = {(row["owner_id"], row["idempotency_key"]) for row in fresh}
    used_runs = {row["run_id"] for row in fresh}
    used_plans = {row["plan_id"] for row in fresh}
    fresh_outboxes = {
        row["id"] for row in rd_submission_closure["tables"]["outbox_events"]
    }
    outboxes = {}
    for row in closure["tables"]["outbox_events"]:
        if (
            row["run_id"] in outboxes
            or row["id"] in fresh_outboxes
            or type(row["generation"]) is not int
            or row["generation"] != 1
        ):
            raise ValueError("Seed exact independent fresh outbox differs")
        outboxes[row["run_id"]] = row
    prepared = {}
    for row in closure["tables"]["factor_seeded_submissions"]:
        key = (row["owner_id"], row["idempotency_key"])
        if (
            key in keys
            or row["run_id"] in used_runs
            or row["analysis_plan_id"] in used_plans
            or type(row["idempotency_key"]) is not str
            or re.fullmatch(r"[!-~]{1,128}", row["idempotency_key"]) is None
            or type(row["parent_ordinal"]) is not int
            or row["parent_ordinal"] < 1
            or type(row["parent_code_size_bytes"]) is not int
            or not 1 <= row["parent_code_size_bytes"] <= 131072
        ):
            raise ValueError("Seed cross-family identity differs")
        run, spec, plan = (
            runs[row["run_id"]],
            specs[row["spec_id"]],
            plans[row["analysis_plan_id"]],
        )
        try:
            value = _object(row)
            validate_saved_seed_binding(
                value,
                _object(run),
                _object(spec),
                _object(plan),
                _uuid(row["owner_id"]),
                row["idempotency_key"],
            )
            _, reference = read_saved_seed(value)
        except (DomainError, ValueError, TypeError, AttributeError) as exc:
            raise ValueError("Seed canonical declaration graph differs") from exc
        parent = versions[row["parent_factor_version_id"]]
        factor = factors[row["parent_factor_id"]]
        obj = objects[row["parent_code_object_id"]]
        if (
            any(item["owner_id"] != row["owner_id"] for item in (parent, factor, obj))
            or (
                parent["factor_id"],
                parent["ordinal"],
                parent["code_object_id"],
                parent["code_sha256"],
                parent["definition_sha256"],
            )
            != (
                row["parent_factor_id"],
                row["parent_ordinal"],
                row["parent_code_object_id"],
                row["parent_code_sha256"],
                row["parent_definition_sha256"],
            )
            or canonical_bytes(parent["source_provenance"]).decode()
            != row["parent_provenance_canonical"]
            or digest(parent["source_provenance"]) != row["parent_provenance_sha256"]
            or (
                obj["object_kind"],
                obj["media_type"],
                obj["sha256"],
                obj["size_bytes"],
                obj["storage_key"],
            )
            != (
                "code",
                "text/plain",
                row["parent_code_sha256"],
                row["parent_code_size_bytes"],
                f"factors/{row['owner_id']}/objects/{row['parent_code_sha256']}.py",
            )
        ):
            raise ValueError("Seed stored parent graph differs")
        raw = read_bytes(obj["storage_key"], obj["sha256"], 131072)
        if (
            type(raw) is not bytes
            or len(raw) != obj["size_bytes"]
            or hashlib.sha256(raw).hexdigest() != obj["sha256"]
            or raw.decode("utf-8") != parent["code"]
        ):
            raise ValueError("Seed actual retained parent code differs")
        seed = ResolvedSeedParent(
            reference=reference,
            definition=parent["definition"],
            source=raw.decode("utf-8"),
        )
        seed.prompt_section()
        if (
            row["run_id"] not in outboxes
            or budgets[row["run_id"]]["owner_id"] != row["owner_id"]
            or budgets[row["run_id"]]["limits"] != run["config"]["budget"]
        ):
            raise ValueError("Seed budget/outbox differs")
        for start in rd["factor_generation_starts"]:
            if start["run_id"] == row["run_id"] and (
                start["owner_id"] != row["owner_id"]
                or type(start["logical_step"]) is not int
                or start["logical_step"] != 0
                or start["parent_factor_version_id"] != parent["id"]
            ):
                raise ValueError("Seed first generation parent differs")
        for source in rd["factor_generation_sources"]:
            if source["run_id"] == row["run_id"]:
                child = versions[source["factor_version_id"]]
                if (
                    child["owner_id"] != row["owner_id"]
                    or child["factor_id"] != factor["id"]
                    or child["parent_version_id"] != parent["id"]
                    or child["ordinal"] <= parent["ordinal"]
                ):
                    raise ValueError("Seed generated source lineage differs")
        prepared[row["run_id"]] = PreparedSeed(_uuid(row["id"]), seed)
        keys.add(key)
        used_runs.add(row["run_id"])
        used_plans.add(row["analysis_plan_id"])
    if set(outboxes) != set(prepared):
        raise ValueError("Unreferenced seed outbox")
    return MappingProxyType(prepared)


def seeded_bootstrap(base, seed_context):
    """Pure expected bootstrap reconstruction; no physical/launch authority."""
    prepared = seed_context.get(str(base.run_id))
    if prepared is None:
        return base
    if type(prepared) is not PreparedSeed:
        raise ValueError("Exact prepared seed data required")
    values = base.model_dump(exclude={"schema_version", "continuation"})
    return build_expected_bootstrap(seed_context=seed_context, **values)


def build_expected_bootstrap(*, seed_context, **values):
    """026-only versioned constructor from independently reconstructed inputs."""
    from packages.rdagent_adapter.provider_bootstrap import build_operational_bootstrap

    prepared = seed_context.get(str(values["run_id"]))
    if prepared is not None and type(prepared) is not PreparedSeed:
        raise ValueError("Exact prepared seed data required")
    return build_operational_bootstrap(
        **values,
        seed=prepared.parent if prepared else None,
        seeded_submission_id=prepared.submission_id if prepared else None,
    )
