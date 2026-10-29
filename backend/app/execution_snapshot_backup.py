"""Versioned cold closure for execution snapshots; no native parser or replay.

Dependency order is research v6 + RD source phase + native v2, this closure,
then RD final-generation crosslinks. Pending objects remain uncertainty, never
successful snapshots; old opaque JSON retention does not register this schema.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import text

from backend.app.execution_snapshot_io import inventory
from backend.app.execution_snapshot_services import (
    _physical_receipt,
    decode_publication,
)
from backend.app.rdagent_snapshot_config import parse_snapshot_config
from packages.factors.contracts import (
    canonical_bytes,
    digest,
    rows_digest,
    validate_rows,
)
from packages.factors.native_normalizer_binding import primitive_report_context
from packages.factors.native_publication import NativePublicationIntent
from packages.rdagent_adapter.assembly import (
    assemble_cutoff_rows,
    validate_projection_binding,
)
from packages.rdagent_adapter.prefix import PrefixManifest
from runtimes.factor_normalizer.primitive_report import validate_bound_report

SCHEMA = "mineagent.execution-snapshot-restore-closure.v1"
SCHEMA_V2 = "mineagent.execution-snapshot-restore-closure.v2"
FIELDS = {
    "factor_execution_snapshot_publications": "id evaluation_id source_id start_id factor_version_id reservation_id manifest manifest_sha256 manifest_object_id manifest_size_bytes manifest_storage_key journal journal_sha256 journal_key peak_bytes peak_entries created_at owner_id run_id attempt_id generation",
    "factor_execution_snapshot_partitions": "publication_id ordinal cutoff_id native_publication_id normalizer_admission_id stop_receipt_id extraction_receipt_id stop_kind extraction_kind binding binding_sha256 owner_id run_id attempt_id generation",
    "factor_execution_snapshot_objects": "id owner_id publication_id object_kind sha256 size_bytes storage_key created_at",
    "factor_execution_snapshot_outcomes": "publication_id manifest_sha256 input_sha256 output_sha256 physical_receipt_sha256 created_at owner_id run_id attempt_id generation",
    "factor_execution_evaluation_outcomes": "id evaluation_id generation_start_id controller_launch_id boundary_id snapshot_id status snapshot_input_sha256 snapshot_output_sha256 snapshot_manifest_sha256 feedback_object_id feedback_object_kind feedback_sha256 created_at owner_id run_id attempt_id generation",
    "factor_evaluation_result_consumers": "evaluation_id consumer_kind consumer_id owner_id run_id attempt_id generation",
    "engine_object_reservation_consumers": "reservation_id owner_id run_id attempt_id generation consumer_kind consumer_id",
}
SCOPE = ("owner_id", "run_id", "attempt_id", "generation")
KEYS = {
    "factor_execution_snapshot_partitions": ("publication_id", "ordinal"),
    "factor_execution_snapshot_outcomes": ("publication_id",),
    "factor_evaluation_result_consumers": ("evaluation_id",),
    "engine_object_reservation_consumers": ("reservation_id",),
}


def _key(name, row):
    return tuple(row[k] for k in KEYS.get(name, ("id",)))


def read_closure(connection, maximum_rows, *, maximum_bytes=32_000_000, schema=SCHEMA):
    if schema not in {SCHEMA, SCHEMA_V2}:
        raise ValueError("Unsupported execution closure schema")
    tables = {}
    count = size = 0
    for name in FIELDS:
        where = (
            " WHERE consumer_kind='execution_snapshot'"
            if name == "engine_object_reservation_consumers"
            else ""
        )
        order = ",".join(KEYS.get(name, ("id",)))
        rows = (
            connection.execute(
                text(
                    f"SELECT to_jsonb(t) FROM {name} t{where} ORDER BY {order} LIMIT :limit"
                ),
                {"limit": maximum_rows + 1},
            )
            .scalars()
            .all()
        )
        count += len(rows)
        size += len(canonical_bytes(rows))
        if count > maximum_rows or size > maximum_bytes:
            raise ValueError("Execution snapshot closure bound")
        tables[name] = rows
    return {"schema_version": schema, "tables": tables}


def indexes(closure, maximum_rows):
    if (
        type(closure) is not dict
        or set(closure) != {"schema_version", "tables"}
        or closure["schema_version"] not in {SCHEMA, SCHEMA_V2}
        or type(closure["tables"]) is not dict
        or set(closure["tables"]) != set(FIELDS)
    ):
        raise ValueError("Execution snapshot closure grammar")
    result = {}
    count = 0
    for name, fields in FIELDS.items():
        rows = closure["tables"][name]
        if type(rows) is not list:
            raise ValueError("Execution snapshot table grammar")
        count += len(rows)
        if count > maximum_rows:
            raise ValueError("Execution snapshot row bound")
        idx = {}
        for row in rows:
            if type(row) is not dict or set(row) != set(fields.split()):
                raise ValueError("Execution snapshot row columns differ")
            for key, value in row.items():
                if key == "id" or key.endswith("_id"):
                    if type(value) is not str or str(UUID(value)) != value:
                        raise ValueError("Execution snapshot UUID grammar")
                elif key in {
                    "generation",
                    "ordinal",
                    "size_bytes",
                    "manifest_size_bytes",
                    "peak_bytes",
                    "peak_entries",
                }:
                    if type(value) is not int:
                        raise ValueError("Execution integer grammar")
                elif key == "created_at":
                    parsed = datetime.fromisoformat(value)
                    if parsed.utcoffset() != UTC.utcoffset(parsed):
                        raise ValueError("Execution snapshot UTC grammar")
            key = _key(name, row)
            if key in idx:
                raise ValueError("Repeated execution snapshot row")
            idx[key] = row
        result[name] = idx
    return result


def _typed(row):
    return SimpleNamespace(
        **{k: UUID(v) if k == "id" or k.endswith("_id") else v for k, v in row.items()}
    )


def manifest_keys(closure):
    idx = indexes(closure, 100_000)
    keys = set()
    for row in idx["factor_execution_snapshot_publications"].values():
        journal = decode_publication(_typed(row))
        keys.update(
            (
                row["journal_key"],
                row["manifest_storage_key"],
                journal.manifest.assembled_rows.storage_key,
            )
        )
        for part in journal.manifest.partitions:
            keys.add(
                f"research-processes/{row['owner_id']}/{part.normalizer_admission_id}/output/rows.json"
            )
    return keys


def validate_closure(
    closure,
    check,
    read_json,
    maximum_rows,
    *,
    inventory_rows,
    research_closure,
    rd_source_closure,
    native_closure,
    direct_analysis_closure=None,
):
    expected = {
        SCHEMA: {
            (
                "mineagent.research-restore-closure.v6",
                "mineagent.rd-source-restore-closure.v3",
                "mineagent.native-hdf-restore-closure.v2",
            )
        },
        SCHEMA_V2: {
            (
                "mineagent.research-restore-closure.v12",
                "mineagent.rd-source-restore-closure.v7",
                "mineagent.native-hdf-restore-closure.v3",
            ),
            (
                "mineagent.research-restore-closure.v7",
                "mineagent.rd-source-restore-closure.v4",
                "mineagent.native-hdf-restore-closure.v3",
            ),
            (
                "mineagent.research-restore-closure.v8",
                "mineagent.rd-source-restore-closure.v4",
                "mineagent.native-hdf-restore-closure.v3",
            ),
            (
                "mineagent.research-restore-closure.v9",
                "mineagent.rd-source-restore-closure.v4",
                "mineagent.native-hdf-restore-closure.v3",
            ),
            (
                "mineagent.research-restore-closure.v10",
                "mineagent.rd-source-restore-closure.v5",
                "mineagent.native-hdf-restore-closure.v3",
            ),
            (
                "mineagent.research-restore-closure.v13",
                "mineagent.rd-source-restore-closure.v8",
                "mineagent.native-hdf-restore-closure.v3",
            ),
            (
                "mineagent.research-restore-closure.v14",
                "mineagent.rd-source-restore-closure.v9",
                "mineagent.native-hdf-restore-closure.v3",
            ),
            (
                "mineagent.research-restore-closure.v11",
                "mineagent.rd-source-restore-closure.v6",
                "mineagent.native-hdf-restore-closure.v3",
            ),
            (
                "mineagent.research-restore-closure.v15",
                "mineagent.rd-source-restore-closure.v10",
                "mineagent.native-hdf-restore-closure.v3",
            ),
            (
                "mineagent.research-restore-closure.v16",
                "mineagent.rd-source-restore-closure.v11",
                "mineagent.native-hdf-restore-closure.v3",
            ),
        },
    }.get(closure.get("schema_version"))
    if (
        expected is None
        or (
            research_closure.get("schema_version"),
            rd_source_closure.get("schema_version"),
            native_closure.get("schema_version"),
        )
        not in expected
    ):
        raise ValueError("Execution snapshot dependency versions differ")
    if closure["schema_version"] == SCHEMA_V2 and (
        not isinstance(direct_analysis_closure, dict)
        or direct_analysis_closure.get("schema_version")
        != "mineagent.direct-analysis-restore-closure.v1"
    ):
        raise ValueError("Configured snapshot requires final analysis closure")
    idx = indexes(closure, maximum_rows)
    physical = {item["path"]: item for item in inventory_rows}
    if len(physical) != len(inventory_rows):
        raise ValueError("Execution physical inventory duplicates")
    research = {
        t: {r["id"]: r for r in rows}
        for t, rows in research_closure["tables"].items()
        if all("id" in r for r in rows)
    }
    rd = {
        t: {r["id"]: r for r in rows}
        for t, rows in rd_source_closure["tables"].items()
        if all("id" in r for r in rows)
    }
    native = {
        t: {
            r["id"]
            if "id" in r
            else r.get(
                "admission_id", r.get("publication_id", r.get("reservation_id"))
            ): r
            for r in rows
        }
        for t, rows in native_closure["tables"].items()
    }
    used_parts = set()
    used_objects = set()
    used_outcomes = set()
    used_reservations = set()

    def same_scope(a, b):
        if any(a[k] != b[k] for k in SCOPE):
            raise ValueError("Execution scope differs")

    def checked_json(key, sha, size):
        check(key, sha, size)
        value = read_json(key)
        if digest(value) != sha or len(canonical_bytes(value)) != size:
            raise ValueError("Noncanonical execution JSON")
        return value

    for row in idx["factor_execution_snapshot_publications"].values():
        journal = decode_publication(_typed(row))
        m = journal.manifest
        evaluation = rd["factor_evaluation_starts"][row["evaluation_id"]]
        source = rd["factor_generation_sources"][row["source_id"]]
        same_scope(row, evaluation)
        same_scope(row, source)
        if (
            evaluation["start_id"],
            evaluation["evaluated_source_id"],
            evaluation["factor_version_id"],
            evaluation["snapshot_input"],
            evaluation["input_sha256"],
            evaluation["expected_cutoffs"],
        ) != (
            row["start_id"],
            row["source_id"],
            row["factor_version_id"],
            m.input.model_dump(mode="json"),
            m.input_sha256,
            m.expected_cutoffs,
        ):
            raise ValueError("Execution pre-evaluation differs")
        witness = rd["factor_source_read_witnesses"][str(m.source_read_witness_id)]
        if (
            witness["source_id"] != row["source_id"]
            or witness["witness_sha256"] != m.source_read_witness_sha256
        ):
            raise ValueError("Execution source witness differs")
        start = rd["factor_generation_starts"][row["start_id"]]
        spec = rd["factor_research_specs"][start["spec_id"]]
        from backend.app.rdagent_profile_backup import require_config_revision

        require_config_revision(
            spec["config"],
            research_schema=research_closure["schema_version"],
            source_schema=rd_source_closure["schema_version"],
        )
        config = parse_snapshot_config(
            spec["config"],
            owner_id=spec["owner_id"],
            run_id=spec["run_id"],
            workflow_config=(
                research_closure.get("schema_version"),
                rd_source_closure.get("schema_version"),
            )
            in {
                (
                    "mineagent.research-restore-closure.v16",
                    "mineagent.rd-source-restore-closure.v11",
                ),
                (
                    "mineagent.research-restore-closure.v14",
                    "mineagent.rd-source-restore-closure.v9",
                ),
                (
                    "mineagent.research-restore-closure.v15",
                    "mineagent.rd-source-restore-closure.v10",
                ),
            },
            provider_config=(
                research_closure.get("schema_version"),
                rd_source_closure.get("schema_version"),
            )
            in {
                (
                    "mineagent.research-restore-closure.v10",
                    "mineagent.rd-source-restore-closure.v5",
                ),
                (
                    "mineagent.research-restore-closure.v13",
                    "mineagent.rd-source-restore-closure.v8",
                ),
                (
                    "mineagent.research-restore-closure.v11",
                    "mineagent.rd-source-restore-closure.v6",
                ),
                (
                    "mineagent.research-restore-closure.v12",
                    "mineagent.rd-source-restore-closure.v7",
                ),
            },
            composed_controller=research_closure.get("schema_version")
            in {
                "mineagent.research-restore-closure.v12",
                "mineagent.research-restore-closure.v16",
                "mineagent.research-restore-closure.v11",
                "mineagent.research-restore-closure.v13",
                "mineagent.research-restore-closure.v14",
                "mineagent.research-restore-closure.v15",
            },
            seeded_controller=research_closure.get("schema_version")
            in {
                "mineagent.research-restore-closure.v12",
                "mineagent.research-restore-closure.v13",
                "mineagent.research-restore-closure.v14",
                "mineagent.research-restore-closure.v15",
                "mineagent.research-restore-closure.v16",
            },
        )
        from backend.app.rdagent_complete_evaluation import maintained_calendar

        if closure["schema_version"] == SCHEMA_V2:
            from backend.app.factor_analysis_calendar import cold_calendar

            calendar = cold_calendar(config, row, direct_analysis_closure, read_json)
        else:
            calendar = maintained_calendar(config)
        days = tuple(
            day.session_date
            for day in calendar.sessions
            if m.input.window.start <= day.session_date < m.input.window.end
        )
        if len(days) != m.expected_cutoffs:
            raise ValueError("Execution calendar completeness differs")
        cutoffs = [
            c
            for c in rd["factor_cutoff_executions"].values()
            if c["evaluation_id"] == row["evaluation_id"]
        ]
        if len(cutoffs) != len(days):
            raise ValueError("Execution cutoff set differs")
        combined = []
        for part in m.partitions:
            key = (row["id"], part.ordinal)
            p = idx["factor_execution_snapshot_partitions"][key]
            used_parts.add(key)
            same_scope(p, row)
            if (
                p["binding"] != part.model_dump(mode="json")
                or p["binding_sha256"] != digest(part)
                or (
                    p["cutoff_id"],
                    p["native_publication_id"],
                    p["normalizer_admission_id"],
                    p["stop_receipt_id"],
                    p["extraction_receipt_id"],
                    p["stop_kind"],
                    p["extraction_kind"],
                )
                != (
                    str(part.cutoff_id),
                    str(part.native_publication_id),
                    str(part.normalizer_admission_id),
                    str(part.stop_receipt_id),
                    str(part.extraction_receipt_id),
                    "stop",
                    "extraction",
                )
            ):
                raise ValueError("Execution partition row differs")
            c = rd["factor_cutoff_executions"][str(part.cutoff_id)]
            same_scope(c, row)
            prefix = PrefixManifest.model_validate(c["projection"])
            validate_projection_binding(m.input, prefix)
            if (
                c["evaluation_id"] != row["evaluation_id"]
                or c["cutoff_ordinal"] != part.ordinal
                or prefix.output_session != days[part.ordinal]
                or prefix.sha256 != part.prefix_sha256
            ):
                raise ValueError("Execution cutoff calendar/source differs")
            native_row = native["factor_native_publications"][
                str(part.native_publication_id)
            ]
            same_scope(native_row, row)
            candidate = NativePublicationIntent.model_validate_json(
                canonical_bytes(native_row["intent"])
            )
            if (
                str(candidate.admission.id) != str(part.candidate_admission_id)
                or native_row["cutoff_id"] != str(part.cutoff_id)
                or str(part.native_publication_id)
                not in native["factor_native_outcomes"]
            ):
                raise ValueError("Execution completed native input differs")
            claim = native["factor_native_normalizer_inputs"][
                str(part.normalizer_admission_id)
            ]
            if (
                claim["publication_id"] != str(part.native_publication_id)
                or claim["input_sha256"] != part.normalizer_input_sha256
            ):
                raise ValueError("Execution normalizer claim differs")
            admission = research["research_process_admissions"][
                str(part.normalizer_admission_id)
            ]
            same_scope(admission, row)
            process_budget = research["research_budget_reservations"][
                admission["reservation_id"]
            ]
            same_scope(process_budget, row)
            if process_budget["state"] == "settled":
                charged = [
                    value
                    for value in research["research_budget_settlements"].values()
                    if value["reservation_id"] == process_budget["id"]
                ]
                if (
                    len(charged) != 1
                    or charged[0]["outcome"] != "conservative_unknown"
                    or charged[0]["charge"] != process_budget["amounts"]
                ):
                    raise ValueError("Completed snapshot normalizer charge differs")
            elif process_budget["state"] not in {"reserved", "uncertain"}:
                raise ValueError("Snapshot normalizer reservation state differs")
            stop = research["research_process_receipts"][str(part.stop_receipt_id)]
            ex = research["research_process_receipts"][str(part.extraction_receipt_id)]
            if (
                stop["admission_id"],
                stop["kind"],
                stop["payload_sha256"],
                ex["admission_id"],
                ex["kind"],
                ex["payload_sha256"],
            ) != (
                str(part.normalizer_admission_id),
                "stop",
                part.stop_receipt_sha256,
                str(part.normalizer_admission_id),
                "extraction",
                part.extraction_receipt_sha256,
            ):
                raise ValueError("Execution normalizer receipts differ")
            payload = ex["payload"]
            if (
                payload["schema_version"],
                payload["sha256"],
                payload["size_bytes"],
                payload["report_sha256"],
                payload["report_size_bytes"],
            ) != (
                "mineagent.numeric-process-extraction.v3",
                part.values.sha256,
                part.values.size_bytes,
                part.primitive_report_sha256,
                part.primitive_report_size_bytes,
            ):
                raise ValueError("Execution protected report differs")
            root = f"research-processes/{row['owner_id']}/{part.normalizer_admission_id}/output/"
            check(root + "values.parquet", part.values.sha256, part.values.size_bytes)
            report = checked_json(
                root + "rows.json",
                part.primitive_report_sha256,
                part.primitive_report_size_bytes,
            )
            manifest_raw, listings = primitive_report_context(prefix, candidate.output)
            primitives = validate_bound_report(
                canonical_bytes(report),
                parquet_sha256=part.values.sha256,
                parquet_size_bytes=part.values.size_bytes,
                manifest=manifest_raw,
                listings=listings,
            )
            rows, assembly = assemble_cutoff_rows(
                m.input,
                prefix,
                primitives,
                hdf_sha256=candidate.output.object.sha256,
                normalized_values_sha256=part.values.sha256,
            )
            if (
                digest(assembly),
                assembly.output_rows_sha256,
                assembly.row_count,
                assembly.valid_count,
            ) != (
                part.assembly_sha256,
                part.output_rows_sha256,
                part.row_count,
                part.valid_count,
            ):
                raise ValueError("Execution host assembly differs")
            combined.extend(rows)
        rows = tuple(combined)
        validate_rows(m.input, rows)
        if rows_digest(rows) != m.output_rows_sha256:
            raise ValueError("Execution aggregate row digest differs")
        expected_rows = {
            "schema_version": "mineagent.execution-feature-rows.v1",
            "input_sha256": m.input_sha256,
            "rows": [r.model_dump(mode="json") for r in rows],
        }
        if (
            digest(expected_rows) != m.assembled_rows.sha256
            or len(canonical_bytes(expected_rows)) != m.assembled_rows.size_bytes
        ):
            raise ValueError("Execution row document differs")
        reservation = research["research_budget_reservations"][row["reservation_id"]]
        same_scope(reservation, row)
        used_reservations.add(row["reservation_id"])
        from packages.research_runtime.budget_contracts import BudgetVector

        if (
            reservation["purpose"] != "object_storage"
            or reservation["logical_key"] != "execution-snapshot:" + row["id"]
            or reservation["amounts"]
            != BudgetVector(storage_bytes=row["peak_bytes"]).model_dump()
        ):
            raise ValueError("Execution reservation differs")
        consumer = idx["engine_object_reservation_consumers"][(row["reservation_id"],)]
        same_scope(consumer, row)
        if (consumer["consumer_kind"], consumer["consumer_id"]) != (
            "execution_snapshot",
            row["id"],
        ):
            raise ValueError("Execution storage consumer differs")
        if row["journal_key"] in physical and checked_json(
            row["journal_key"], row["journal_sha256"], len(canonical_bytes(journal))
        ) != journal.model_dump(mode="json"):
            raise ValueError("Execution physical journal differs")
        outcome = idx["factor_execution_snapshot_outcomes"].get((row["id"],))
        registered = [
            obj
            for obj in idx["factor_execution_snapshot_objects"].values()
            if obj["publication_id"] == row["id"]
        ]
        if outcome is None:
            if registered or reservation["state"] not in {"reserved", "uncertain"}:
                raise ValueError("Pending execution snapshot cannot register/settle")
            continue
        used_outcomes.add((row["id"],))
        same_scope(outcome, row)
        if (
            outcome["manifest_sha256"],
            outcome["input_sha256"],
            outcome["output_sha256"],
            outcome["physical_receipt_sha256"],
        ) != (
            digest(m),
            m.input_sha256,
            m.output_rows_sha256,
            _physical_receipt(journal),
        ):
            raise ValueError("Execution outcome differs")
        if row["journal_key"] not in physical:
            raise ValueError("Completed execution journal missing")
        settlements = [
            s
            for s in research["research_budget_settlements"].values()
            if s["reservation_id"] == row["reservation_id"]
        ]
        if (
            len(settlements) != 1
            or reservation["state"] != "settled"
            or settlements[0]["outcome"] != "conservative_unknown"
            or settlements[0]["charge"] != reservation["amounts"]
        ):
            raise ValueError("Execution full charge differs")
        actual = {obj["id"]: obj for obj in registered}
        if set(actual) != {str(obj.object_id) for obj in inventory(journal)}:
            raise ValueError("Execution registered inventory differs")
        for obj in inventory(journal):
            value = actual[str(obj.object_id)]
            used_objects.add((value["id"],))
            if (
                value["owner_id"],
                value["object_kind"],
                value["sha256"],
                value["size_bytes"],
                value["storage_key"],
            ) != (
                str(obj.owner_id),
                obj.kind,
                obj.sha256,
                obj.size_bytes,
                obj.storage_key,
            ):
                raise ValueError("Execution object row differs")
            check(obj.storage_key, obj.sha256, obj.size_bytes)
        if checked_json(
            m.assembled_rows.storage_key,
            m.assembled_rows.sha256,
            m.assembled_rows.size_bytes,
        ) != expected_rows or checked_json(
            row["manifest_storage_key"],
            row["manifest_sha256"],
            row["manifest_size_bytes"],
        ) != m.model_dump(mode="json"):
            raise ValueError("Execution final JSON differs")
    if (
        used_parts != set(idx["factor_execution_snapshot_partitions"])
        or used_objects != set(idx["factor_execution_snapshot_objects"])
        or used_outcomes != set(idx["factor_execution_snapshot_outcomes"])
        or {(rid,) for rid in used_reservations}
        != set(idx["engine_object_reservation_consumers"])
    ):
        raise ValueError("Unbound execution snapshot rows")
    publications = list(idx["factor_execution_snapshot_publications"].values())
    if len({p["evaluation_id"] for p in publications}) != len(publications) or len(
        used_reservations
    ) != len(publications):
        raise ValueError("Repeated execution evaluation/reservation")
    results = {}
    for result in rd.get("factor_evaluation_outcomes", {}).values():
        results[result["evaluation_id"]] = ("legacy_result", result)
    for result in idx["factor_execution_evaluation_outcomes"].values():
        if result["evaluation_id"] in results:
            raise ValueError("Evaluation has competing result families")
        pub = idx["factor_execution_snapshot_publications"][(result["snapshot_id"],)]
        out = idx["factor_execution_snapshot_outcomes"][(result["snapshot_id"],)]
        same_scope(result, pub)
        if (
            result["evaluation_id"],
            result["generation_start_id"],
            result["status"],
            result["snapshot_input_sha256"],
            result["snapshot_output_sha256"],
            result["snapshot_manifest_sha256"],
        ) != (
            pub["evaluation_id"],
            pub["start_id"],
            "valid",
            out["input_sha256"],
            out["output_sha256"],
            out["manifest_sha256"],
        ):
            raise ValueError("Execution valid result snapshot identity differs")
        boundary = rd["factor_generation_boundaries"][result["boundary_id"]]
        same_scope(boundary, result)
        if (boundary["controller_launch_id"], boundary["start_id"]) != (
            result["controller_launch_id"],
            result["generation_start_id"],
        ):
            raise ValueError("Execution valid result actor differs")
        feedback = research["research_objects"][result["feedback_object_id"]]
        if (
            feedback["owner_id"],
            feedback["object_kind"],
            feedback["sha256"],
            result["feedback_object_kind"],
        ) != (
            result["owner_id"],
            "factor_generation_feedback",
            result["feedback_sha256"],
            "factor_generation_feedback",
        ):
            raise ValueError("Execution result feedback differs")
        check(feedback["storage_key"], feedback["sha256"], feedback["size_bytes"])
        results[result["evaluation_id"]] = ("execution_result", result)
    if {(key,) for key in results} != set(idx["factor_evaluation_result_consumers"]):
        raise ValueError("Evaluation result claim inventory differs")
    for evaluation, (kind, result) in results.items():
        consumer = idx["factor_evaluation_result_consumers"][(evaluation,)]
        same_scope(consumer, result)
        if (consumer["consumer_kind"], consumer["consumer_id"]) != (kind, result["id"]):
            raise ValueError("Evaluation result claim differs")
    return idx
