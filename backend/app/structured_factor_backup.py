"""025 stopped structured graph closure; not a launch or public disclosure grant."""

import hashlib
import json
from collections import defaultdict
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import text

from backend.app.structured_factor_reconstruction import reconstruct_input
from backend.app.structured_factor_results import result_graph
from packages.factors.contracts import canonical_bytes, digest
from packages.structured_factor_strategy.contracts import StructuredProgram
from packages.structured_factor_strategy.run import StructuredRunConfig
from packages.structured_factor_strategy.wire import read_result
from runtimes.structured_factor_engine.journal import StructuredIntent

SCHEMA = "mineagent.structured-strategy-restore-closure.v1"
RESEARCH_SCHEMA = "mineagent.research-restore-closure.v9"
RUN_FIELDS = "id owner_id generation purpose engine status strategy_version_id dataset_version_id config request_sha256"
FIELDS = {
    "structured_strategy_versions": "id owner_id parent_version_id program program_canonical program_sha256 created_at",
    "structured_strategy_specs": "run_id owner_id structured_strategy_version_id config config_canonical config_sha256 created_at",
    "factor_strategy_executions": "id owner_id run_id attempt_id generation ordinal structured_strategy_version_id snapshot_id factor_version_id strategy_input strategy_input_canonical strategy_input_sha256 strategy_input_size_bytes market_sha256 market_size_bytes created_at",
    "factor_strategy_execution_outcomes": "execution_id owner_id run_id attempt_id generation admission_id launch_identity_receipt_id launch_identity_kind launch_identity_sha256 stop_receipt_id stop_kind stop_sha256 extraction_receipt_id extraction_kind extraction_sha256 result result_canonical result_sha256 result_size_bytes decision_count created_at",
    "factor_strategy_decisions": "execution_id event_seq owner_id run_id attempt_id generation observation_sha256 eligibility_sha256 score_sha256 allocation_sha256 reply_sha256 signal_id record record_canonical record_sha256",
    "factor_strategy_orders": "execution_id native_order_id owner_id event_seq logical_order_id listing_id record record_canonical record_sha256",
    "factor_strategy_fills": "execution_id native_fill_id owner_id native_order_id record record_canonical record_sha256",
}
KEYS = {
    "structured_strategy_versions": ("id",),
    "structured_strategy_specs": ("run_id",),
    "factor_strategy_executions": ("id",),
    "factor_strategy_execution_outcomes": ("execution_id",),
    "factor_strategy_decisions": ("execution_id", "event_seq"),
    "factor_strategy_orders": ("execution_id", "native_order_id"),
    "factor_strategy_fills": ("execution_id", "native_fill_id"),
}


def read_closure(connection, maximum_rows, *, maximum_bytes=32_000_000):
    if (
        type(maximum_rows) is not int
        or not 0 < maximum_rows <= 100000
        or type(maximum_bytes) is not int
        or not 0 < maximum_bytes <= 512 * 1024**2
    ):
        raise ValueError("Structured closure bound")
    tables = {}
    count = size = 0
    external = {}
    for name, fields in {**FIELDS, "research_runs": RUN_FIELDS}.items():
        rows = []
        where = " WHERE engine='structured'" if name == "research_runs" else ""
        order = "id" if name == "research_runs" else ",".join(KEYS[name])
        cursor = connection.execute(
            text(
                f"SELECT to_jsonb(t) FROM (SELECT {','.join(fields.split())} FROM {name}{where} ORDER BY {order} LIMIT :limit) t"
            ).execution_options(stream_results=True, yield_per=1),
            {"limit": maximum_rows + 1},
        )
        try:
            for row in cursor.scalars():
                count += 1
                size += len(canonical_bytes(row)) + 1
                if count > maximum_rows or size > maximum_bytes:
                    raise ValueError("Structured closure bound")
                rows.append(row)
        finally:
            cursor.close()
        (external if name == "research_runs" else tables)[name] = rows
    result = {"schema_version": SCHEMA, "tables": tables, "external_bindings": external}
    if len(canonical_bytes(result)) > maximum_bytes:
        raise ValueError("Structured closure envelope bound")
    return result


def indexes(closure, maximum_rows):
    if type(maximum_rows) is not int or not 0 < maximum_rows <= 100000:
        raise ValueError("Structured row budget")
    if (
        type(closure) is not dict
        or set(closure) != {"schema_version", "tables", "external_bindings"}
        or closure["schema_version"] != SCHEMA
        or type(closure["tables"]) is not dict
        or set(closure["tables"]) != set(FIELDS)
        or type(closure["external_bindings"]) is not dict
        or set(closure["external_bindings"]) != {"research_runs"}
    ):
        raise ValueError("Structured closure grammar")
    result = {}
    count = 0
    for name, fields in {**FIELDS, "research_runs": RUN_FIELDS}.items():
        rows = (
            closure["external_bindings"]
            if name == "research_runs"
            else closure["tables"]
        )[name]
        if type(rows) is not list:
            raise ValueError("Structured table inventory")
        out = {}
        for row in rows:
            count += 1
            if (
                count > maximum_rows
                or type(row) is not dict
                or set(row) != set(fields.split())
            ):
                raise ValueError("Structured row bound/fields")
            for key, value in row.items():
                if (
                    (key == "id" or key.endswith("_id"))
                    and key
                    not in {
                        "native_order_id",
                        "native_fill_id",
                        "logical_order_id",
                        "signal_id",
                    }
                    and value is not None
                    and (type(value) is not str or str(UUID(value)) != value)
                ):
                    raise ValueError("Structured canonical UUID")
                if (
                    key
                    in {
                        "generation",
                        "ordinal",
                        "event_seq",
                        "decision_count",
                        "strategy_input_size_bytes",
                        "market_size_bytes",
                        "result_size_bytes",
                    }
                    and type(value) is not int
                ):
                    raise ValueError("Structured strict integer")
                if key == "created_at" and (
                    type(value) is not str
                    or datetime.fromisoformat(value).tzinfo is None
                ):
                    raise ValueError("Structured aware timestamp")
            key = tuple(
                row[k] for k in (("id",) if name == "research_runs" else KEYS[name])
            )
            if key in out:
                raise ValueError("Duplicate structured key")
            out[key] = row
        result[name] = out
    return result


def manifest_keys(closure):
    idx = indexes(closure, 100000)
    keys = set()
    for row in idx["structured_strategy_specs"].values():
        config = StructuredRunConfig.model_validate_json(row["config_canonical"])
        if (
            config.model_dump(mode="json") != row["config"]
            or canonical_bytes(config).decode("ascii") != row["config_canonical"]
            or digest(config) != row["config_sha256"]
        ):
            raise ValueError("Structured config commitment")
        prefix = f"datasets/{row['owner_id']}/{config.dataset_version_id}/"
        keys.update(
            prefix + name
            for name in (
                "manifest.json",
                "preview.json",
                "sessions.json",
                "actions.json",
                "corporate-action-coverage.json",
            )
        )
    for row in idx["factor_strategy_execution_outcomes"].values():
        keys.add(
            f"research-processes/{row['owner_id']}/{row['admission_id']}/output/result.json"
        )
    return keys


def _table(closure, name, key="id"):
    rows = closure["tables"][name]
    idx = {row[key]: row for row in rows}
    if len(idx) != len(rows):
        raise ValueError("Duplicate predecessor identity")
    return idx


def validate_closure(
    closure,
    *,
    research_closure,
    execution_closure,
    direct_analysis_closure,
    read_bytes,
    maximum_rows=100000,
    oe_structured_links_pending=False,
):
    """Invoke after exact predecessor validators; reconstruct all owned result bytes."""
    if RESEARCH_SCHEMA is None or research_closure.get("schema_version") not in {
        "mineagent.research-restore-closure.v12",
        "mineagent.research-restore-closure.v16",
        RESEARCH_SCHEMA,
        "mineagent.research-restore-closure.v10",
        "mineagent.research-restore-closure.v13",
        "mineagent.research-restore-closure.v14",
        "mineagent.research-restore-closure.v11",
        "mineagent.research-restore-closure.v15",
    }:
        raise ValueError("Exact structured research closure version required")
    successor = research_closure.get("schema_version") in {
        "mineagent.research-restore-closure.v16",
        "mineagent.research-restore-closure.v13",
        "mineagent.research-restore-closure.v14",
        "mineagent.research-restore-closure.v15",
    }
    if successor != (oe_structured_links_pending is True):
        raise ValueError(
            "Exact normal OE final links must follow structured predecessor"
        )
    idx = indexes(closure, maximum_rows)
    runs = {row["id"]: row for row in idx["research_runs"].values()}
    shared_runs = {r["id"]: r for r in research_closure["external_bindings"]["runs"]}
    if set(runs) != {
        key for key, row in shared_runs.items() if row["engine"] == "structured"
    }:
        raise ValueError("Structured full/shared run inventory differs")
    for key, row in runs.items():
        if any(
            (
                row["config"].get("schema_version")
                if field == "config_schema_version" and successor
                else row[field]
            )
            != value
            for field, value in shared_runs[key].items()
        ):
            raise ValueError("Structured full/shared run binding differs")
    admissions = _table(research_closure, "research_process_admissions")
    receipts = _table(research_closure, "research_process_receipts")
    reservations = _table(research_closure, "research_budget_reservations")
    settlements = _table(
        research_closure, "research_budget_settlements", "reservation_id"
    )
    states = _table(research_closure, "research_process_states", "admission_id")
    publications = _table(execution_closure, "factor_execution_snapshot_publications")
    snapshot_outcomes = _table(
        execution_closure, "factor_execution_snapshot_outcomes", "publication_id"
    )
    datasets = _table(direct_analysis_closure, "dataset_versions")
    exposures = _table(direct_analysis_closure, "data_usage_exposures")
    versions = idx["structured_strategy_versions"]
    specs = idx["structured_strategy_specs"]
    executions = idx["factor_strategy_executions"]
    outcomes = idx["factor_strategy_execution_outcomes"]
    chain_by_admission = defaultdict(list)
    for receipt in receipts.values():
        chain_by_admission[receipt["admission_id"]].append(receipt)
    execution_by_run = defaultdict(list)
    for execution in executions.values():
        execution_by_run[execution["run_id"]].append(execution)
    exposure_by_key = defaultdict(list)
    for exposure in exposures.values():
        exposure_by_key[(exposure["owner_id"], exposure["source_key"])].append(exposure)
    records_by_execution = {}
    for table in (
        "factor_strategy_decisions",
        "factor_strategy_orders",
        "factor_strategy_fills",
    ):
        grouped = defaultdict(list)
        for record in idx[table].values():
            grouped[record["execution_id"]].append(record)
        records_by_execution[table] = grouped
    for version in versions.values():
        program = StructuredProgram.model_validate_json(version["program_canonical"])
        if (
            program.model_dump(mode="json") != version["program"]
            or canonical_bytes(program).decode("ascii") != version["program_canonical"]
            or digest(program) != version["program_sha256"]
        ):
            raise ValueError("Structured version commitment")
        seen = {version["id"]}
        parent = version["parent_version_id"]
        while parent is not None:
            if parent in seen or len(seen) > maximum_rows:
                raise ValueError("Structured version ancestry cycle")
            seen.add(parent)
            ancestor = versions[(parent,)]
            if ancestor["owner_id"] != version["owner_id"]:
                raise ValueError("Structured parent owner differs")
            parent = ancestor["parent_version_id"]
    for spec in specs.values():
        config = StructuredRunConfig.model_validate_json(spec["config_canonical"])
        run = runs[spec["run_id"]]
        version = versions[(spec["structured_strategy_version_id"],)]
        if (
            spec["owner_id"],
            run["owner_id"],
            version["owner_id"],
            run["purpose"],
            run["engine"],
            run["strategy_version_id"],
            run["config"],
            run["request_sha256"],
        ) != (
            spec["owner_id"],
            spec["owner_id"],
            spec["owner_id"],
            "factor_strategy",
            "structured",
            None,
            spec["config"],
            spec["config_sha256"],
        ):
            raise ValueError("Structured cold run identity differs")
        if (
            config.model_dump(mode="json") != spec["config"]
            or canonical_bytes(config).decode("ascii") != spec["config_canonical"]
            or digest(config) != spec["config_sha256"]
            or config.program_sha256 != version["program_sha256"]
            or str(config.structured_strategy_version_id) != version["id"]
            or str(config.dataset_version_id) != run["dataset_version_id"]
        ):
            raise ValueError("Structured cold config differs")
        if run["status"] not in {"succeeded", "failed", "cancelled", "interrupted"}:
            raise ValueError(
                "Active structured run cannot be cold historical authority"
            )
        selected = execution_by_run[spec["run_id"]]
        if len({x["ordinal"] for x in selected}) != len(selected) or len(
            {x["snapshot_id"] for x in selected}
        ) != len(selected):
            raise ValueError("Duplicate structured selection")
        if run["status"] == "succeeded" and (
            len(selected) != len(config.snapshots)
            or any((x["id"],) not in outcomes for x in selected)
        ):
            raise ValueError("Successful structured pair incomplete")
    for execution in executions.values():
        spec = specs[(execution["run_id"],)]
        run = runs[execution["run_id"]]
        publication = publications[execution["snapshot_id"]]
        if execution["snapshot_id"] not in snapshot_outcomes:
            raise ValueError("Structured source snapshot is incomplete")
        source_run = shared_runs[publication["run_id"]]
        if source_run["owner_id"] != execution["owner_id"] or source_run[
            "status"
        ] not in {"succeeded", "failed", "cancelled", "interrupted"}:
            raise ValueError("Structured source not stopped historical owner")
        value, _market, config, program = reconstruct_input(
            run=run,
            spec=spec,
            version=versions[(execution["structured_strategy_version_id"],)],
            execution=execution,
            publication=publication,
            dataset=datasets[
                str(
                    StructuredRunConfig.model_validate(
                        spec["config"]
                    ).dataset_version_id
                )
            ],
            read_bytes=read_bytes,
            declared_upload=research_closure["schema_version"]
            in {
                "mineagent.research-restore-closure.v16",
                "mineagent.research-restore-closure.v15",
            },
        )
        dataset = datasets[str(config.dataset_version_id)]
        owned = exposure_by_key[
            (execution["owner_id"], "structured-factor-source:" + execution["run_id"])
        ]
        expected_provenance = {
            "schema_version": "mineagent.structured-source-exposure.v1",
            "config_sha256": digest(config),
            "program_sha256": digest(program),
            "snapshot_ids": [str(x) for x in config.snapshots],
            "dataset_manifest_sha256": config.dataset_sha256,
            "extent": "whole_retained_dataset",
            "interpretation": "research_input_admission_not_order_or_fill_evidence",
        }
        if len(owned) != 1 or (
            owned[0]["run_id"],
            owned[0]["dataset_version_id"],
            owned[0]["start_date"],
            owned[0]["end_date"],
            owned[0]["purpose"],
            owned[0]["provenance"],
            owned[0]["experiment_id"],
            owned[0]["split_version_id"],
        ) != (
            execution["run_id"],
            dataset["id"],
            dataset["start_date"],
            dataset["end_date"],
            "structured_factor_execution_source",
            expected_provenance,
            # The frozen structured run mode forbids experiment/split binding;
            # these are deliberately absent from the v1 run projection.
            None,
            None,
        ):
            raise ValueError("Structured source exposure differs")
        from backend.app.experiment_services import dataset_identities

        if owned[0]["identities"] != dataset_identities(
            SimpleNamespace(manifest=dataset["manifest"], market=dataset["market"])
        ):
            raise ValueError("Structured exposure listing identities differ")
        outcome = outcomes.get((execution["id"],))
        if outcome is None:
            continue
        scope = {
            k: execution[k] for k in ("owner_id", "run_id", "attempt_id", "generation")
        }
        if any(outcome[k] != v for k, v in scope.items()):
            raise ValueError("Structured outcome scope differs")
        admission = admissions[outcome["admission_id"]]
        state = states[admission["id"]]
        chain = chain_by_admission[admission["id"]]
        by_kind = {r["kind"]: r for r in chain}
        if (
            len(chain) != 4
            or set(by_kind)
            != {"launch_intent", "launch_identity", "stop", "extraction"}
            or state["status"] != "stopped"
            or any(
                row[k] != v
                for row in (admission, state, *chain)
                for k, v in scope.items()
            )
        ):
            raise ValueError("Structured full stopped scope differs")
        intent = StructuredIntent.model_validate_json(
            canonical_bytes(by_kind["launch_intent"]["payload"])
        )
        if (
            intent.inputs.execution_id != UUID(execution["id"])
            or intent.inputs.strategy_input_sha256 != execution["strategy_input_sha256"]
            or intent.inputs.market_sha256 != execution["market_sha256"]
        ):
            raise ValueError("Structured process input differs")
        raw = read_bytes(
            f"research-processes/{execution['owner_id']}/{admission['id']}/output/result.json",
            16 * 1024**2,
        )
        result = read_result(raw, value)
        extraction = by_kind["extraction"]["payload"]
        if (
            raw.decode("ascii") != outcome["result_canonical"]
            or result.model_dump(mode="json") != outcome["result"]
            or hashlib.sha256(raw).hexdigest() != outcome["result_sha256"]
            or len(raw) != outcome["result_size_bytes"]
            or (extraction["sha256"], extraction["size_bytes"])
            != (outcome["result_sha256"], len(raw))
        ):
            raise ValueError("Structured physical result differs")
        if outcome["decision_count"] != len(result.decisions):
            raise ValueError("Structured decision count differs")
        for kind in ("launch_identity", "stop", "extraction"):
            if outcome[kind + "_kind"] != kind:
                raise ValueError("Structured receipt kind differs")
            if (
                outcome[kind + "_receipt_id"] != by_kind[kind]["id"]
                or outcome[kind + "_sha256"] != by_kind[kind]["payload_sha256"]
            ):
                raise ValueError("Structured indexed process receipt differs")
        reservation = reservations[admission["reservation_id"]]
        settled = settlements[reservation["id"]]
        if (
            reservation["state"] != "settled"
            or settled["outcome"] != "conservative_unknown"
            or settled["charge"] != reservation["amounts"]
            or any(
                row[k] != v for row in (reservation, settled) for k, v in scope.items()
            )
        ):
            raise ValueError("Structured full conservative charge missing")
        fence = SimpleNamespace(
            **{k: UUID(v) if k != "generation" else v for k, v in scope.items()}
        )
        for table, expected in zip(
            (
                "factor_strategy_decisions",
                "factor_strategy_orders",
                "factor_strategy_fills",
            ),
            result_graph(fence, result),
            strict=True,
        ):
            actual = records_by_execution[table][execution["id"]]
            if len(actual) != len(expected):
                raise ValueError("Structured actual indexed result count differs")
            actual_by = {tuple(r[k] for k in KEYS[table]): r for r in actual}
            for row in expected:
                # result_graph is a SQL publisher projection with typed UUIDs.
                # Normalize only its declared identity columns for the retained
                # JSON row comparison; record bytes/hashes remain unchanged.
                identity_fields = {
                    "factor_strategy_decisions": (
                        "execution_id",
                        "owner_id",
                        "run_id",
                        "attempt_id",
                    ),
                    "factor_strategy_orders": (
                        "execution_id",
                        "owner_id",
                        "listing_id",
                    ),
                    "factor_strategy_fills": ("execution_id", "owner_id"),
                }[table]
                if any(type(row[key]) is not UUID for key in identity_fields):
                    raise ValueError("Structured projected identity type differs")
                row = {**row, **{key: str(row[key]) for key in identity_fields}}
                row = json.loads(canonical_bytes(row))
                stored = actual_by[tuple(row[k] for k in KEYS[table])]
                if any(stored[k] != v for k, v in row.items()):
                    raise ValueError("Structured actual read/stage/order/fill differs")
    # Reverse checks: no caller can hide an unrelated result or process in a table.
    for run in runs.values():
        if (run["purpose"] == "factor_strategy" or run["engine"] == "structured") and (
            run["id"],
        ) not in specs:
            raise ValueError("Orphan structured run")
    for exposure in exposures.values():
        if exposure["purpose"] == "structured_factor_execution_source":
            spec = specs.get((exposure["run_id"],))
            if (
                spec is None
                or exposure["owner_id"] != spec["owner_id"]
                or exposure["source_key"]
                != "structured-factor-source:" + spec["run_id"]
            ):
                raise ValueError("Orphan structured source exposure")
    if any((row["execution_id"],) not in executions for row in outcomes.values()):
        raise ValueError("Orphan structured outcome")
    for table in (
        "factor_strategy_decisions",
        "factor_strategy_orders",
        "factor_strategy_fills",
    ):
        if any((row["execution_id"],) not in outcomes for row in idx[table].values()):
            raise ValueError("Orphan structured observed record")
    for admission in admissions.values():
        shared_run = shared_runs.get(admission["run_id"])
        if (
            successor
            and shared_run is not None
            and (
                shared_run["purpose"],
                shared_run["engine"],
                shared_run.get("config_schema_version"),
            )
            == (
                "strategy_search",
                "openevolve",
                "mineagent.oe-structured-provider-run.v1",
            )
        ):
            if admission["owner_id"] != shared_run["owner_id"]:
                raise ValueError("Normal OE native process owner differs")
            # Only the mandatory new domain validator may consume this row;
            # never relabel it as a025 acceptance execution.
            continue
        if (admission["run_id"],) in specs or admission[
            "profile"
        ] == "mineagent.factor-strategy-engine.v1":
            execution = executions.get((admission["logical_operation_id"],))
            if execution is None or any(
                admission[k] != execution[k]
                for k in ("owner_id", "run_id", "attempt_id", "generation")
            ):
                raise ValueError("Orphan structured process")
            spec = specs[(execution["run_id"],)]
            config = StructuredRunConfig.model_validate(spec["config"])
            if (
                admission["role"],
                admission["profile"],
                admission["ipc_policy"],
                admission["ticket_sha256"],
                admission["launch_ordinal"],
                admission["image_id"],
            ) != (
                "native_evaluator",
                "mineagent.factor-strategy-engine.v1",
                "none",
                None,
                1,
                config.engine_image_id,
            ):
                raise ValueError("Structured process role/image/replay differs")
            chain = chain_by_admission[admission["id"]]
            intents = [r for r in chain if r["kind"] == "launch_intent"]
            if len(intents) != 1:
                raise ValueError("Structured exact launch intent missing")
            intent = StructuredIntent.model_validate_json(
                canonical_bytes(intents[0]["payload"])
            )
            expected = {
                key: admission[key]
                for key in (
                    "schema_version",
                    "id",
                    "owner_id",
                    "run_id",
                    "attempt_id",
                    "generation",
                    "role",
                    "logical_operation_id",
                    "launch_ordinal",
                    "profile",
                    "input_sha256",
                    "reservation_id",
                    "image_id",
                )
            }
            expected["ipc"] = {"ipc_policy": "none", "ticket_sha256": None}
            if (
                intent.admission.model_dump(mode="json") != expected
                or digest(intents[0]["payload"]) != intents[0]["payload_sha256"]
            ):
                raise ValueError("Structured process declaration differs")
            expected_input = {
                "schema_version": "mineagent.structured-factor-engine-input-binding.v1",
                "execution_id": execution["id"],
                "structured_strategy_version_id": execution[
                    "structured_strategy_version_id"
                ],
                "snapshot_id": execution["snapshot_id"],
                "strategy_input_sha256": execution["strategy_input_sha256"],
                "strategy_input_size_bytes": execution["strategy_input_size_bytes"],
                "market_sha256": execution["market_sha256"],
                "market_size_bytes": execution["market_size_bytes"],
            }
            if (
                intent.inputs.model_dump(mode="json") != expected_input
                or digest(expected_input) != admission["input_sha256"]
            ):
                raise ValueError("Structured retained process input differs")
    return idx
