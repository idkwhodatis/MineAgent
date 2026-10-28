"""022 complete declared-analysis cold closure, never live launch authority.

Research/process, RD source, native and execution snapshot closures must already
have passed their explicit new-version checks. This final layer reconstructs
real snapshot rows plus retained forward labels and checks the stopped result
and predeclared objective. A relocated inode never becomes a launch grant.
"""

import hashlib
import re
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text

from backend.app.factor_analysis_canonical import read_objective
from backend.app.factor_analysis_reconstruction import reconstruct_direct_input
from backend.app.rdagent_configured_backup import validate_configured_input_closure
from backend.app.rdagent_configured_source import derive_configured_labels
from backend.app.rdagent_snapshot_config import parse_snapshot_config
from packages.factors.contracts import canonical_bytes, digest
from packages.qlib_adapter.direct_wire import read_result
from packages.qlib_adapter.objective import (
    measure_direct_objective,
)
from packages.rdagent_adapter.configured_source import (
    ConfiguredSourcePolicy,
    parse_retained_source,
)

SCHEMA = "mineagent.direct-analysis-restore-closure.v1"
SCOPE = ("owner_id", "run_id", "attempt_id", "generation")
FIELDS = {
    "factor_analysis_plans": "id owner_id run_id spec_id config_sha256 input_sha256 objective objective_canonical objective_sha256 source_policy source_policy_sha256 label_source_dataset_id label_source_manifest_sha256 qlib_image_id qlib_installed_parent_image_id analysis_dependency_lock_sha256 created_at",
    "factor_direct_analysis_intents": "id owner_id run_id attempt_id generation plan_id snapshot_id label_source_id label_manifest label_manifest_sha256 label_recipe_sha256 protocol protocol_sha256 analysis_input_sha256 analysis_input_size_bytes created_at",
    "factor_direct_analysis_outcomes": "analysis_id owner_id run_id attempt_id generation admission_id launch_identity_receipt_id launch_identity_receipt_sha256 launch_identity_kind stop_receipt_id stop_receipt_sha256 stop_kind extraction_receipt_id extraction_receipt_sha256 extraction_kind result result_canonical result_sha256 result_size_bytes measurement measurement_canonical measurement_sha256 created_at",
    "factor_configured_input_witnesses": "execution_id admission_id plan_id source_read_witness_id dataset_id dataset_manifest_sha256 availability_revision_sha256 calendar_sha256 input_sha256 projection_sha256 document witness_sha256 attempt_id generation id owner_id run_id created_at",
    "dataset_versions": "id owner_id storage_key content_sha256 manifest start_date end_date market",
    "data_usage_exposures": "id owner_id experiment_id split_version_id run_id dataset_version_id source_key identities start_date end_date purpose provenance created_at",
}


def read_closure(connection, maximum_rows, *, maximum_bytes=32_000_000):
    if (
        type(maximum_rows) is not int
        or maximum_rows < 1
        or type(maximum_bytes) is not int
        or maximum_bytes < 1
    ):
        raise ValueError("Direct analysis closure bound")
    tables = {}
    count = size = 0
    for name, fields in FIELDS.items():
        columns = ",".join(fields.split())
        where = (
            " WHERE id IN (SELECT label_source_dataset_id FROM factor_analysis_plans)"
            if name == "dataset_versions"
            else " WHERE owner_id IN (SELECT owner_id FROM factor_analysis_plans)"
            if name == "data_usage_exposures"
            else ""
        )
        key = "analysis_id" if name == "factor_direct_analysis_outcomes" else "id"
        # Raw commitments can be large. Stream before charging each row rather
        # than buffering the complete result and checking the byte cap afterward.
        rows = []
        size += 2  # The encoded table array brackets.
        if size > maximum_bytes:
            raise ValueError("Direct analysis closure bound")
        result = connection.execute(
            text(
                f"SELECT to_jsonb(t) FROM (SELECT {columns} FROM {name}{where} ORDER BY {key} LIMIT :limit) t"
            ).execution_options(stream_results=True, yield_per=1),
            {"limit": maximum_rows + 1},
        )
        try:
            for row in result.scalars():
                count += 1
                size += len(canonical_bytes(row)) + bool(rows)
                if count > maximum_rows or size > maximum_bytes:
                    raise ValueError("Direct analysis closure bound")
                rows.append(row)
        finally:
            result.close()
        tables[name] = rows
    return {"schema_version": SCHEMA, "tables": tables}


def indexes(closure, maximum_rows):
    if (
        type(closure) is not dict
        or set(closure) != {"schema_version", "tables"}
        or closure["schema_version"] != SCHEMA
        or type(closure["tables"]) is not dict
        or set(closure["tables"]) != set(FIELDS)
    ):
        raise ValueError("Direct analysis closure grammar")
    result = {}
    count = 0
    for name, fields in FIELDS.items():
        rows = closure["tables"][name]
        idx = {}
        if type(rows) is not list:
            raise ValueError("Direct analysis table grammar")
        count += len(rows)
        if count > maximum_rows:
            raise ValueError("Direct analysis row bound")
        for row in rows:
            if type(row) is not dict or set(row) != set(fields.split()):
                raise ValueError("Direct analysis columns differ")
            for key, value in row.items():
                if name == "factor_analysis_plans" and key in {
                    "qlib_image_id",
                    "qlib_installed_parent_image_id",
                }:
                    if (
                        type(value) is not str
                        or re.fullmatch(r"sha256:[a-f0-9]{64}", value) is None
                    ):
                        raise ValueError("Direct analysis image identity grammar")
                elif key == "id" or key.endswith("_id"):
                    if (
                        name == "data_usage_exposures"
                        and key
                        in {
                            "experiment_id",
                            "split_version_id",
                            "run_id",
                            "dataset_version_id",
                        }
                        and value is None
                    ):
                        continue
                    if type(value) is not str or str(UUID(value)) != value:
                        raise ValueError("Direct analysis UUID grammar")
                elif key in {
                    "generation",
                    "analysis_input_size_bytes",
                    "result_size_bytes",
                }:
                    if type(value) is not int:
                        raise ValueError("Direct analysis integer grammar")
                elif key == "created_at":
                    dt = datetime.fromisoformat(value)
                    if dt.utcoffset() != UTC.utcoffset(dt):
                        raise ValueError("Direct analysis UTC grammar")
            pk = row[
                "analysis_id" if name == "factor_direct_analysis_outcomes" else "id"
            ]
            if pk in idx:
                raise ValueError("Duplicate direct analysis row")
            idx[pk] = row
        result[name] = idx
    return result


def _same(a, b, fields=SCOPE):
    if any(a[k] != b[k] for k in fields):
        raise ValueError("Analysis scope differs")


def validate_closure(
    closure,
    *,
    research_closure,
    rd_source_closure,
    execution_closure,
    factor_closure,
    read_bytes,
    maximum_rows,
):
    if (
        research_closure.get("schema_version"),
        rd_source_closure.get("schema_version"),
    ) not in {
        (
            "mineagent.research-restore-closure.v7",
            "mineagent.rd-source-restore-closure.v4",
        ),
        (
            "mineagent.research-restore-closure.v8",
            "mineagent.rd-source-restore-closure.v4",
        ),
        (
            "mineagent.research-restore-closure.v9",
            "mineagent.rd-source-restore-closure.v4",
        ),
        (
            "mineagent.research-restore-closure.v10",
            "mineagent.rd-source-restore-closure.v5",
        ),
        (
            "mineagent.research-restore-closure.v11",
            "mineagent.rd-source-restore-closure.v6",
        ),
    } or execution_closure.get(
        "schema_version"
    ) != "mineagent.execution-snapshot-restore-closure.v2":
        raise ValueError("Direct analysis dependency version differs")
    provider_config = (
        research_closure.get("schema_version"),
        rd_source_closure.get("schema_version"),
    ) in {
        (
            "mineagent.research-restore-closure.v10",
            "mineagent.rd-source-restore-closure.v5",
        ),
        (
            "mineagent.research-restore-closure.v11",
            "mineagent.rd-source-restore-closure.v6",
        ),
    }
    own = indexes(closure, maximum_rows)
    tables = {}
    for component in (
        research_closure,
        rd_source_closure,
        execution_closure,
        factor_closure,
    ):
        for name, rows in component["tables"].items():
            if name in tables and tables[name] != rows:
                # Shared reservation consumer registries are filtered by family.
                if name != "engine_object_reservation_consumers":
                    raise ValueError("Analysis duplicate dependency differs")
                continue
            tables[name] = rows
    tables["research_runs"] = rd_source_closure["external_bindings"]["runs"]
    tables.update(closure["tables"])

    def index(name, key="id"):
        rows = tables[name]
        out = {r[key]: r for r in rows}
        if len(out) != len(rows):
            raise ValueError("Analysis dependency duplicates")
        return out

    specs = index("factor_research_specs")
    runs = index("research_runs")
    publications = index("factor_execution_snapshot_publications")
    snapshots = index("factor_execution_snapshot_outcomes", "publication_id")
    admissions = index("research_process_admissions")
    receipts = index("research_process_receipts")
    states = index("research_process_states", "admission_id")
    reservations = index("research_budget_reservations")
    settlements = index("research_budget_settlements", "reservation_id")
    plans = own["factor_analysis_plans"]
    datasets = own["dataset_versions"]
    from backend.app.rdagent_configured_exposure_backup import (
        validate_configured_exposure_links,
    )

    validate_configured_exposure_links(
        list(plans.values()),
        list(datasets.values()),
        closure["tables"]["data_usage_exposures"],
        tables["factor_evaluation_starts"],
        maximum_rows=maximum_rows,
    )
    used_datasets = set()
    used_runs = set()
    for plan in plans.values():
        spec = specs[plan["spec_id"]]
        run = runs[plan["run_id"]]
        from backend.app.rdagent_profile_backup import require_config_revision

        require_config_revision(
            spec["config"],
            research_schema=research_closure["schema_version"],
            source_schema=rd_source_closure["schema_version"],
        )
        config = parse_snapshot_config(
            spec["config"],
            owner_id=spec["owner_id"],
            provider_config=provider_config,
            composed_controller=research_closure["schema_version"]
            == "mineagent.research-restore-closure.v11",
        )
        objective = read_objective(plan)
        policy = ConfiguredSourcePolicy.model_validate(plan["source_policy"])
        ds = datasets[plan["label_source_dataset_id"]]
        _same(plan, spec, ("owner_id", "run_id"))
        _same(plan, run, ("owner_id",))
        locks = {x.role: x.sha256 for x in config.runtime.dependency_locks}
        if (
            plan["run_id"] in used_runs
            or run["engine"] != "rdagent"
            or run["purpose"] != "factor_research"
            or plan["config_sha256"] != config.sha256
            or plan["input_sha256"] != config.input.sha256
            or plan["objective_sha256"] != objective.sha256
            or plan["source_policy_sha256"] != digest(policy)
            or objective.window != config.input.window
            or plan["qlib_image_id"] != config.runtime.analysis_image
            or plan["analysis_dependency_lock_sha256"] != locks["analysis"]
            or ds["owner_id"] != plan["owner_id"]
            or ds["content_sha256"] != plan["label_source_manifest_sha256"]
            or ds["storage_key"] != f"datasets/{plan['owner_id']}/{ds['id']}"
        ):
            raise ValueError("Analysis predeclared plan differs")
        import re

        if (
            re.fullmatch(r"sha256:[a-f0-9]{64}", plan["qlib_installed_parent_image_id"])
            is None
        ):
            raise ValueError("Analysis installed parent pin differs")
        source = parse_retained_source(
            dataset_id=UUID(ds["id"]),
            manifest_sha256=ds["content_sha256"],
            manifest_bytes=read_bytes(
                ds["storage_key"] + "/manifest.json", 2 * 1024**2
            ),
            artifacts={
                n: read_bytes(ds["storage_key"] + "/" + n, 32 * 1024**2)
                for n in ("preview.json", "sessions.json")
            },
            policy=policy,
            input_names=("close",),
        )
        if (
            source.dataset != config.input.dataset
            or source.universe != config.input.calculation_universe
            or source.calendar.sha256 != config.input.calendar_sha256
        ):
            raise ValueError("Analysis declared retained source differs")
        derive_configured_labels(source, objective)
        used_datasets.add(ds["id"])
        used_runs.add(plan["run_id"])
    if used_datasets != set(datasets):
        raise ValueError("Unreferenced analysis dataset")
    validate_configured_input_closure(
        closure["tables"]["factor_configured_input_witnesses"],
        tables=tables,
        datasets=list(datasets.values()),
        read_bytes=read_bytes,
        max_rows=maximum_rows,
        provider_config=provider_config,
        composed_controller=research_closure["schema_version"]
        == "mineagent.research-restore-closure.v11",
    )
    inputs = {}
    pairs = set()
    for intent in own["factor_direct_analysis_intents"].values():
        plan = plans[intent["plan_id"]]
        publication = publications[intent["snapshot_id"]]
        _same(intent, publication)
        _same(intent, plan, ("owner_id", "run_id"))
        snapshot = snapshots[publication["id"]]
        _same(snapshot, intent)
        pair = (plan["id"], publication["id"])
        if pair in pairs:
            raise ValueError("Duplicate snapshot analysis")
        pairs.add(pair)
        inputs[intent["id"]] = reconstruct_direct_input(
            plan=plan,
            intent=intent,
            publication=publication,
            spec=specs[plan["spec_id"]],
            dataset=datasets[plan["label_source_dataset_id"]],
            read_bytes=read_bytes,
            provider_config=provider_config,
            composed_controller=research_closure["schema_version"]
            == "mineagent.research-restore-closure.v11",
        )
    # Every retained analysis admission, including stopped/no-result attempts,
    # must bind a real declared input; outcome-only checks miss pending authority.
    from packages.research_runtime.analysis_process_journal import AnalysisInputBinding

    for admission in admissions.values():
        if admission["profile"] != "mineagent.qlib-direct-analysis.v1":
            continue
        intent = own["factor_direct_analysis_intents"][
            admission["logical_operation_id"]
        ]
        plan = plans[intent["plan_id"]]
        _same(admission, intent)
        li = [
            r
            for r in receipts.values()
            if r["admission_id"] == admission["id"] and r["kind"] == "launch_intent"
        ]
        if len(li) != 1:
            raise ValueError("Analysis exact launch intent required")
        binding = AnalysisInputBinding(
            analysis_id=UUID(intent["id"]),
            snapshot_id=UUID(intent["snapshot_id"]),
            label_source_id=UUID(intent["label_source_id"]),
            protocol_sha256=intent["protocol_sha256"],
            analysis_input_sha256=intent["analysis_input_sha256"],
            analysis_input_size_bytes=intent["analysis_input_size_bytes"],
        )
        if (
            li[0]["payload"]["inputs"] != binding.model_dump(mode="json")
            or admission["input_sha256"] != digest(binding)
            or admission["image_id"] != plan["qlib_image_id"]
        ):
            raise ValueError("Analysis process substitutes declared input")
    used_admissions = set()
    for outcome in own["factor_direct_analysis_outcomes"].values():
        intent = own["factor_direct_analysis_intents"][outcome["analysis_id"]]
        plan = plans[intent["plan_id"]]
        admission = admissions[outcome["admission_id"]]
        _same(outcome, intent)
        _same(outcome, admission)
        state = states[admission["id"]]
        _same(state, outcome)
        if (
            admission["id"] in used_admissions
            or admission["logical_operation_id"] != intent["id"]
            or admission["role"] != "native_evaluator"
            or admission["profile"] != "mineagent.qlib-direct-analysis.v1"
            or admission["image_id"] != plan["qlib_image_id"]
            or state["status"] != "stopped"
        ):
            raise ValueError("Analysis completed process differs")
        used_admissions.add(admission["id"])
        for kind, column in (
            ("launch_identity", "launch_identity"),
            ("stop", "stop"),
            ("extraction", "extraction"),
        ):
            receipt = receipts[outcome[column + "_receipt_id"]]
            _same(receipt, outcome)
            if (
                receipt["kind"] != kind
                or receipt["admission_id"] != admission["id"]
                or receipt["payload_sha256"] != outcome[column + "_receipt_sha256"]
                or digest(receipt["payload"]) != receipt["payload_sha256"]
            ):
                raise ValueError("Analysis outcome receipt differs")
        reservation = reservations[admission["reservation_id"]]
        settled = settlements[reservation["id"]]
        _same(reservation, outcome)
        _same(settled, outcome)
        if (
            reservation["state"] != "settled"
            or settled["charge"] != reservation["amounts"]
            or settled["outcome"] != "conservative_unknown"
            or reservation["amounts"]["evaluator_executions"] < 1
        ):
            raise ValueError("Analysis full charge differs")
        raw = read_bytes(
            f"research-processes/{outcome['owner_id']}/{admission['id']}/output/analysis.json",
            1048576,
        )
        if (
            hashlib.sha256(raw).hexdigest() != outcome["result_sha256"]
            or len(raw) != outcome["result_size_bytes"]
            or raw != outcome["result_canonical"].encode("ascii")
        ):
            raise ValueError("Analysis result bytes differ")
        value, projection, objective = inputs[intent["id"]]
        result = read_result(raw, value)
        measurement = measure_direct_objective(objective, projection, result)
        if (
            outcome["result"] != result.model_dump(mode="json")
            or outcome["measurement"] != measurement.model_dump(mode="json")
            or outcome["measurement_sha256"] != digest(measurement)
            or canonical_bytes(measurement)
            != outcome["measurement_canonical"].encode("ascii")
        ):
            raise ValueError("Analysis measured objective differs")
    return own


def manifest_keys(closure):
    """Only fixed retained names; no caller paths become materialization input."""
    idx = indexes(closure, 100000)
    keys = set()
    for row in idx["dataset_versions"].values():
        expected = f"datasets/{row['owner_id']}/{row['id']}"
        if row["storage_key"] != expected:
            raise ValueError("Analysis dataset key differs")
        keys.update(
            expected + "/" + name
            for name in ("manifest.json", "preview.json", "sessions.json")
        )
    for row in idx["factor_direct_analysis_outcomes"].values():
        intent = idx["factor_direct_analysis_intents"][row["analysis_id"]]
        _same(row, intent)
        keys.add(
            f"research-processes/{row['owner_id']}/{row['admission_id']}/output/analysis.json"
        )
    return keys
