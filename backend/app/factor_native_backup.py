"""Versioned native-HDF cold closure: opaque bytes, never native parsing/replay.

Call only after the supplied research and RD source closures pass their own
versioned validators. Pending reservations and orphan/temp bytes are retained as
uncertainty; they cannot become completed normalizer input on restore.
"""

import re
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import text

from backend.app.factor_native_services import decode_intent
from backend.app.rdagent_snapshot_config import parse_snapshot_config
from packages.factors.contracts import SnapshotInput, canonical_bytes, digest
from packages.factors.native_publication import NativePublicationIntent
from packages.rdagent_adapter.assembly import validate_projection_binding
from packages.rdagent_adapter.host_evidence import research_snapshot_input
from packages.rdagent_adapter.prefix import PrefixManifest
from packages.research_runtime.numeric_process_journal import NumericIntent
from packages.research_runtime.process_persistence_codec import ROW_FIELDS, decode_row

SCHEMA = "mineagent.native-hdf-restore-closure.v1"
SCHEMA_V2 = "mineagent.native-hdf-restore-closure.v2"
SCHEMA_V3 = "mineagent.native-hdf-restore-closure.v3"
SCHEMAS = frozenset({SCHEMA, SCHEMA_V2, SCHEMA_V3})
FIELDS = {
    "factor_native_publications": "id owner_id run_id attempt_id generation cutoff_id admission_id reservation_id source_id source_object_id source_sha256 stop_receipt_id extraction_receipt_id stop_kind extraction_kind intent intent_sha256 object_id sha256 size_bytes storage_key journal_key peak_bytes peak_entries created_at",
    "factor_native_normalizer_inputs": "admission_id owner_id run_id attempt_id generation publication_id reservation_id object_id sha256 size_bytes storage_key input_sha256 intent_sha256 created_at",
    "factor_native_objects": "id owner_id object_kind sha256 size_bytes storage_key media_type created_at",
    "factor_native_outcomes": "publication_id owner_id object_id sha256 size_bytes storage_key journal_sha256 physical_receipt_sha256 created_at",
    "engine_object_reservation_consumers": "reservation_id owner_id run_id attempt_id generation consumer_kind consumer_id",
}
PK = {
    "factor_native_outcomes": "publication_id",
    "factor_native_normalizer_inputs": "admission_id",
    "engine_object_reservation_consumers": "reservation_id",
}
SCOPE = ("owner_id", "run_id", "attempt_id", "generation")


def read_closure(connection, maximum_rows, *, maximum_bytes=32_000_000, schema=SCHEMA):
    if schema not in SCHEMAS:
        raise ValueError("Unsupported native closure version")
    tables, count, size = {}, 0, 0
    for name in FIELDS:
        where = (
            " WHERE consumer_kind='native_hdf'"
            if name == "engine_object_reservation_consumers"
            else ""
        )
        query = f"SELECT to_jsonb(t) FROM {name} t{where} ORDER BY {PK.get(name, 'id')} LIMIT :limit"
        stream = connection.execute(
            text(query).execution_options(stream_results=True, yield_per=1),
            {"limit": maximum_rows + 1},
        ).scalars()
        rows = []
        try:
            for row in stream:
                count += 1
                size += len(canonical_bytes(row))
                if count > maximum_rows or size > maximum_bytes:
                    raise ValueError("Native closure exceeds bound")
                rows.append(row)
        finally:
            stream.close()
        tables[name] = rows
    return {"schema_version": schema, "tables": tables}


def _indexes(closure, maximum_rows):
    if (
        type(closure) is not dict
        or set(closure) != {"schema_version", "tables"}
        or closure["schema_version"] not in SCHEMAS
    ):
        raise ValueError("Unsupported native closure")
    tables = closure["tables"]
    if type(tables) is not dict or set(tables) != set(FIELDS):
        raise ValueError("Incomplete native table inventory")
    count = 0
    result = {}
    for name, rows in tables.items():
        if type(rows) is not list:
            raise ValueError("Invalid native rows")
        count += len(rows)
        if count > maximum_rows:
            raise ValueError("Native row bound")
        index = {}
        for row in rows:
            if type(row) is not dict or set(row) != set(FIELDS[name].split()):
                raise ValueError("Unexpected native row fields")
            for key in row:
                if (key == "id" or key.endswith("_id")) and (
                    type(row[key]) is not str or str(UUID(row[key])) != row[key]
                ):
                    raise ValueError("Noncanonical native identity")
            identity = row[PK.get(name, "id")]
            if identity in index:
                raise ValueError("Duplicate native row")
            index[identity] = row
        result[name] = index
    return result


def manifest_keys(closure):
    indexes = _indexes(closure, 100_000)
    # HDF must NEVER enter the JSON-manifest reader.
    return {p["journal_key"] for p in indexes["factor_native_publications"].values()}


def validate_closure(
    closure,
    check,
    read_json,
    maximum_rows,
    *,
    inventory,
    research_closure,
    rd_source_closure,
):
    indexes = _indexes(closure, maximum_rows)
    if (
        closure["schema_version"] in {SCHEMA_V2, SCHEMA_V3}
        and research_closure.get("schema_version")
        not in {
            SCHEMA_V2: {"mineagent.research-restore-closure.v6"},
            SCHEMA_V3: {
                "mineagent.research-restore-closure.v7",
                "mineagent.research-restore-closure.v8",
                "mineagent.research-restore-closure.v9",
                "mineagent.research-restore-closure.v10",
                "mineagent.research-restore-closure.v11",
            },
        }[closure["schema_version"]]
    ):
        raise ValueError("Native report closure requires research v6")
    if (
        research_closure.get("schema_version")
        in {
            "mineagent.research-restore-closure.v10",
            "mineagent.research-restore-closure.v11",
        }
        or rd_source_closure.get("schema_version")
        == "mineagent.rd-source-restore-closure.v6"
    ) and (
        research_closure.get("schema_version"),
        rd_source_closure.get("schema_version"),
    ) not in {
        (
            "mineagent.research-restore-closure.v10",
            "mineagent.rd-source-restore-closure.v5",
        ),
        (
            "mineagent.research-restore-closure.v11",
            "mineagent.rd-source-restore-closure.v6",
        ),
    }:
        raise ValueError("Seeded native closure requires exact RD v5")
    physical = {row["path"]: row for row in inventory}
    if len(physical) != len(inventory):
        raise ValueError("Duplicate physical paths")
    research = {
        name: {row["id"]: row for row in rows}
        for name, rows in research_closure["tables"].items()
        if rows and "id" in rows[0]
    }
    source = {
        name: {row["id"]: row for row in rows}
        for name, rows in rd_source_closure["tables"].items()
        if rows and "id" in rows[0]
    }
    publications = indexes["factor_native_publications"]
    objects = indexes["factor_native_objects"]
    outcomes = indexes["factor_native_outcomes"]
    claims = indexes["engine_object_reservation_consumers"]
    seen_reservations = set()
    seen_objects = set()
    journals = {}
    for p in publications.values():
        values = {
            key: (UUID(value) if key == "id" or key.endswith("_id") else value)
            for key, value in p.items()
        }
        intent = decode_intent(SimpleNamespace(**values))
        admission = research.get("research_process_admissions", {}).get(
            p["admission_id"]
        )
        if admission is None:
            raise ValueError("Missing native process admission")
        raw = {key: admission[key] for key in ROW_FIELDS}
        for key, value in raw.items():
            if key == "id" or key.endswith("_id") and key != "image_id":
                raw[key] = UUID(value)
        if decode_row(raw) != intent.admission:
            raise ValueError("Native admission differs")
        receipts = [
            r
            for r in research.get("research_process_receipts", {}).values()
            if r["admission_id"] == p["admission_id"]
        ]
        by_kind = {r["kind"]: r for r in receipts}
        if len(by_kind) != len(receipts) or set(by_kind) != {
            "launch_intent",
            "launch_identity",
            "stop",
            "extraction",
        }:
            raise ValueError("Native stop/extraction missing")
        launch = NumericIntent.model_validate_json(
            canonical_bytes(by_kind["launch_intent"]["payload"])
        )
        st, ex = by_kind["stop"], by_kind["extraction"]
        observed = datetime.fromisoformat(st["payload"]["observed_at"])
        if (
            st["payload"].get("deadline_bound_met") is not True
            or observed.tzinfo is None
            or not (
                launch.issued_at <= observed <= launch.not_after + timedelta(seconds=1)
            )
        ):
            raise ValueError("Native result lacks timely whole-unit stop")
        if any(
            any(r[k] != p[k] for k in SCOPE)
            or digest(r["payload"]) != r["payload_sha256"]
            for r in receipts
        ):
            raise ValueError("Native receipt scope/hash differs")
        if (
            st["id"],
            st["payload_sha256"],
            ex["id"],
            ex["payload_sha256"],
            ex["payload"]["basename"],
            ex["payload"]["sha256"],
            ex["payload"]["size_bytes"],
            launch.admission,
            launch.inputs.source_object_id,
            launch.inputs.source_sha256,
            launch.inputs.prefix_sha256,
            launch.inputs.cutoff,
        ) != (
            p["stop_receipt_id"],
            intent.stop_receipt_sha256,
            p["extraction_receipt_id"],
            intent.extraction_receipt_sha256,
            "result.h5",
            p["sha256"],
            p["size_bytes"],
            intent.admission,
            intent.source_object_id,
            intent.source_sha256,
            intent.prefix_sha256,
            intent.cutoff,
        ):
            raise ValueError("Native protected extraction differs")
        c = source.get("factor_cutoff_executions", {}).get(p["cutoff_id"])
        s = source.get("factor_generation_sources", {}).get(p["source_id"])
        ev = (
            source.get("factor_evaluation_starts", {}).get(c["evaluation_id"])
            if c
            else None
        )
        if (
            not c
            or not s
            or not ev
            or any(any(row[k] != p[k] for k in SCOPE) for row in (c, s, ev))
        ):
            raise ValueError("Native source/cutoff lineage missing")
        if (
            c["candidate_admission_id"],
            c["evaluated_source_id"],
            c["projection_sha256"],
            s["source_sha256"],
            s["factor_version_id"],
            ev["input_sha256"],
            c["start_id"],
        ) != (
            p["admission_id"],
            p["source_id"],
            intent.prefix_sha256,
            intent.source_sha256,
            str(intent.factor_version_id),
            intent.snapshot_input_sha256,
            str(intent.generation_start_id),
        ):
            raise ValueError("Native source/cutoff lineage differs")
        prefix = PrefixManifest.model_validate(c["projection"])
        snapshot = SnapshotInput.model_validate(ev["snapshot_input"])
        validate_projection_binding(snapshot, prefix)
        if not (0 <= c["cutoff_ordinal"] < ev["expected_cutoffs"]) or not (
            snapshot.window.start <= prefix.output_session < snapshot.window.end
        ):
            raise ValueError("Native cutoff lies outside frozen evaluation")
        specs = [
            row
            for row in source.get("factor_research_specs", {}).values()
            if row["owner_id"] == p["owner_id"] and row["run_id"] == p["run_id"]
        ]
        if len(specs) != 1:
            raise ValueError("Native research spec missing")
        from backend.app.rdagent_profile_backup import require_config_revision

        require_config_revision(
            specs[0]["config"],
            research_schema=research_closure.get("schema_version"),
            source_schema=rd_source_closure.get("schema_version"),
        )
        config = parse_snapshot_config(
            specs[0]["config"],
            owner_id=specs[0]["owner_id"],
            provider_config=(
                closure["schema_version"] == SCHEMA_V3
                and (
                    research_closure.get("schema_version"),
                    rd_source_closure.get("schema_version"),
                )
                in {
                    (
                        "mineagent.research-restore-closure.v10",
                        "mineagent.rd-source-restore-closure.v5",
                    ),
                    (
                        "mineagent.research-restore-closure.v11",
                        "mineagent.rd-source-restore-closure.v6",
                    ),
                }
            ),
            composed_controller=research_closure.get("schema_version")
            == "mineagent.research-restore-closure.v11",
        )
        expected = research_snapshot_input(
            config,
            factor_version_id=intent.factor_version_id,
            code_sha256=intent.source_sha256,
            exposure_history=snapshot.exposure_history,
        )
        if (
            config.sha256 != specs[0]["config_sha256"]
            or snapshot != expected
            or (
                snapshot.sha256,
                prefix.snapshot_input_sha256,
                digest(prefix),
                prefix.host_cutoff,
                prefix.candidate_image,
                prefix.candidate_profile,
            )
            != (
                intent.snapshot_input_sha256,
                intent.snapshot_input_sha256,
                intent.prefix_sha256,
                intent.cutoff,
                intent.admission.image_id,
                intent.admission.profile,
            )
        ):
            raise ValueError("Native frozen snapshot/prefix differs")
        if (
            prefix.candidate_image,
            prefix.candidate_profile,
            prefix.normalizer_image,
            prefix.normalizer_profile,
        ) != (
            config.runtime.candidate_image,
            config.runtime.candidate_profile,
            config.runtime.normalizer_image,
            config.runtime.normalizer_profile,
        ):
            raise ValueError("Native prefix runtime pins differ from research spec")
        if p["size_bytes"] > prefix.max_output_bytes:
            raise ValueError("Native extraction exceeds prefix bound")
        r = research.get("research_budget_reservations", {}).get(p["reservation_id"])
        claim = claims.get(p["reservation_id"])
        if (
            not r
            or not claim
            or any(r[k] != p[k] or claim[k] != p[k] for k in SCOPE)
            or (
                r["purpose"],
                r["logical_key"],
                claim["consumer_kind"],
                claim["consumer_id"],
            )
            != ("object_storage", "native:" + p["id"], "native_hdf", p["id"])
        ):
            raise ValueError("Native reservation/claim differs")
        if (
            r["amounts"]["storage_bytes"] != p["peak_bytes"]
            or any(v != 0 for k, v in r["amounts"].items() if k != "storage_bytes")
            or r["id"] in seen_reservations
        ):
            raise ValueError("Native peak reservation differs")
        seen_reservations.add(r["id"])
        journals[p["journal_key"]] = p
        if p["journal_key"] in physical:
            if read_json(p["journal_key"]) != intent.model_dump(mode="json"):
                raise ValueError("Native physical journal differs")
            check(p["journal_key"], p["intent_sha256"], len(canonical_bytes(intent)))
        o = outcomes.get(p["id"])
        if o is None:
            if r["state"] not in {"reserved", "uncertain"}:
                raise ValueError("Pending native reservation was released")
            continue
        obj = objects.get(p["object_id"])
        if (
            not obj
            or any(
                o[k] != p[k] or obj[k] != p[k]
                for k in ("owner_id", "sha256", "size_bytes", "storage_key")
            )
            or o["object_id"] != p["object_id"]
        ):
            raise ValueError("Native outcome object differs")
        if (
            obj["object_kind"] != "native_hdf"
            or obj["media_type"] != "application/x-hdf5"
            or o["journal_sha256"] != p["intent_sha256"]
        ):
            raise ValueError("Native outcome kind/journal differs")
        expected = digest(
            {
                "storage_key": p["storage_key"],
                "sha256": p["sha256"],
                "size_bytes": p["size_bytes"],
                "journal_key": p["journal_key"],
                "journal_sha256": p["intent_sha256"],
            }
        )
        settlements = [
            x
            for x in research.get("research_budget_settlements", {}).values()
            if x["reservation_id"] == r["id"]
        ]
        if (
            o["physical_receipt_sha256"] != expected
            or r["state"] != "settled"
            or len(settlements) != 1
            or settlements[0]["charge"] != r["amounts"]
            or settlements[0]["outcome"] != "conservative_unknown"
            or p["journal_key"] not in physical
        ):
            raise ValueError("Native outcome charge/physical receipt differs")
        check(p["storage_key"], p["sha256"], p["size_bytes"])
        seen_objects.add(p["object_id"])
    if (
        set(outcomes) - set(publications)
        or set(objects) != seen_objects
        or set(claims) != seen_reservations
    ):
        raise ValueError("Unbound native evidence")
    consumed = set()
    for claim in indexes["factor_native_normalizer_inputs"].values():
        p = publications.get(claim["publication_id"])
        if not p or p["id"] not in outcomes or p["id"] in consumed:
            raise ValueError(
                "Native normalizer input lacks unique completed publication"
            )
        consumed.add(p["id"])
        if any(
            claim[k] != p[k]
            for k in (*SCOPE, "object_id", "sha256", "size_bytes", "storage_key")
        ):
            raise ValueError("Native normalizer claim scope/object differs")
        admission = research.get("research_process_admissions", {}).get(
            claim["admission_id"]
        )
        receipts = [
            r
            for r in research.get("research_process_receipts", {}).values()
            if r["admission_id"] == claim["admission_id"]
            and r["kind"] == "launch_intent"
        ]
        if (
            admission is None
            or len(receipts) != 1
            or any(admission[k] != p[k] for k in SCOPE)
        ):
            raise ValueError("Native normalizer admission missing")
        normalizer = NumericIntent.model_validate_json(
            canonical_bytes(receipts[0]["payload"])
        )
        raw_admission = {key: admission[key] for key in ROW_FIELDS}
        for key, value in raw_admission.items():
            if key == "id" or key.endswith("_id") and key != "image_id":
                raw_admission[key] = UUID(value)
        if (
            decode_row(raw_admission) != normalizer.admission
            or str(normalizer.admission.id) != claim["admission_id"]
        ):
            raise ValueError("Native normalizer persisted admission differs")
        candidate = NativePublicationIntent.model_validate_json(
            canonical_bytes(p["intent"])
        )
        c = source["factor_cutoff_executions"][p["cutoff_id"]]
        prefix = PrefixManifest.model_validate(c["projection"])
        from packages.factors.native_normalizer_binding import (
            normalizer_inventory,
            normalizer_report_selection,
        )

        report = normalizer_report_selection(
            prefix,
            candidate.output,
            normalizer.inputs.files,
            allow_reports=closure["schema_version"] in {SCHEMA_V2, SCHEMA_V3},
        )
        expected = normalizer_inventory(
            prefix, candidate.output, primitive_report=report
        )
        if (
            normalizer.admission.role,
            normalizer.admission.profile,
            normalizer.admission.image_id,
            str(normalizer.admission.logical_operation_id),
            str(normalizer.admission.reservation_id),
            normalizer.admission.input_sha256,
            digest(normalizer),
            normalizer.inputs.source_object_id,
            normalizer.inputs.source_sha256,
            normalizer.inputs.prefix_sha256,
            normalizer.inputs.cutoff,
            tuple(
                (x.destination, x.sha256, x.size_bytes) for x in normalizer.inputs.files
            ),
        ) != (
            "factor_normalizer",
            prefix.normalizer_profile,
            prefix.normalizer_image,
            p["cutoff_id"],
            claim["reservation_id"],
            claim["input_sha256"],
            claim["intent_sha256"],
            candidate.source_object_id,
            candidate.source_sha256,
            candidate.prefix_sha256,
            candidate.cutoff,
            tuple((x.destination, x.sha256, x.size_bytes) for x in expected),
        ):
            raise ValueError("Native normalizer full input differs")
    for key, row in physical.items():
        if not key.startswith("factors/"):
            continue
        if "/native-journals/" in key and not key.endswith("/"):
            if key not in journals:
                # Interrupted temp journals retain their known publication charge.
                match = re.fullmatch(
                    r"(factors/[a-f0-9-]{36}/native-journals)/\.([a-f0-9-]{36}\.json)\.[a-f0-9]{32}\.tmp",
                    key,
                )
                if match is None or f"{match[1]}/{match[2]}" not in journals:
                    raise ValueError("Unadmitted native journal")
            if row["size_bytes"] > 65536:
                raise ValueError("Oversized native journal")
        if (key.endswith(".h5") or ".h5." in key) and (
            not re.fullmatch(
                r"factors/[a-f0-9-]{36}/objects/(?:[a-f0-9]{64}\.h5|\.[a-f0-9]{64}\.h5\.[a-f0-9]{32}\.tmp)",
                key,
            )
            or row["size_bytes"] > 16777216
        ):
            raise ValueError("Invalid opaque native inventory")
        # Unregistered native files remain opaque orphans; no deletion authority.
