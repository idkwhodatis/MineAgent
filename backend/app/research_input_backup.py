"""029 retained input bytes and derivation closure, never a release/launch grant.

Requires independently validated RD, execution and direct-analysis closures.
Workflow validation separately closes source-stage/child/checkpoint eligibility.
No Session emulation, current-head authorization or new exposure is performed.
"""

import hashlib
import json
import re
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import text

from packages.factors.contracts import canonical_bytes, digest
from packages.qlib_adapter.objective import DirectObjectiveMeasurement
from packages.research_inputs.derivation import (
    DerivationEvent,
    ExternalNormalizationDefinition,
    validate_event_prefix,
)
from packages.research_inputs.feedback import (
    ExperimentFeedbackProposal,
    FeedbackProjectionDefinition,
    project_direct_feedback,
)
from packages.research_inputs.normalization import (
    NormalizationError,
    normalize_external_json,
)

SCHEMA = "mineagent.research-input-restore-closure.v1"
FIELDS = {
    "research_input_objects": "id owner_id kind storage_key sha256 size_bytes created_at",
    "research_input_derivations": "id owner_id workflow_id stage_id operation source_canonical source_sha256 mapping_canonical mapping_sha256 raw_object_id raw_object_sha256 source_workflow_id source_stage_id source_child_run_id created_at",
    "research_input_records": "id owner_id origin current_version_id revision disabled created_at",
    "research_input_versions": "id owner_id record_id ordinal derivation_id item_ordinal object_id content_canonical content_sha256 created_at",
    "research_input_derivation_events": "id owner_id workflow_id stage_id derivation_id ordinal state error_code outputs_canonical outputs_sha256 created_at",
}
EXTERNAL_FIELDS = {"owners": "id", "listings": "id symbol"}
LIMITS = {
    "raw_external": 65536,
    "normalized_external": 32768,
    "normalized_feedback": 32768,
}
SUFFIX = {
    "raw_external": ".raw",
    "normalized_external": ".json",
    "normalized_feedback": ".feedback.json",
}


def require(value, reason):
    if not value:
        raise ValueError("Research input closure: " + reason)


def bounds(maximum_rows, maximum_bytes):
    require(type(maximum_rows) is int and 0 < maximum_rows <= 100000, "row bound")
    require(
        type(maximum_bytes) is int and 0 < maximum_bytes <= 512 * 1024**2, "byte bound"
    )


def read_closure(connection, maximum_rows, *, maximum_bytes=32_000_000):
    bounds(maximum_rows, maximum_bytes)
    tables, external = {}, {}
    count = size = 0
    owners = " UNION ".join("SELECT owner_id FROM " + name for name in FIELDS)
    # No cast of an arbitrary manifest value to UUID, and no whole catalog dump.
    listing_where = """EXISTS (
      SELECT 1 FROM research_input_derivations d
      JOIN research_runs r ON r.id=d.source_child_run_id AND r.owner_id=d.owner_id
      JOIN factor_analysis_plans p ON p.run_id=r.id AND p.owner_id=d.owner_id
      JOIN dataset_versions v ON v.id IN (r.dataset_version_id,p.label_source_dataset_id) AND v.owner_id=d.owner_id
      CROSS JOIN LATERAL jsonb_array_elements(
        CASE WHEN jsonb_typeof(v.manifest->'listings')='array'
          THEN v.manifest->'listings' ELSE '[]'::jsonb END) item
      WHERE d.operation='experiment_feedback_projection'
        AND item->>'listing_id'=listings.id::text)"""
    queries = {
        name: f"SELECT {','.join(fields.split())} FROM {name}"
        for name, fields in FIELDS.items()
    }
    queries.update(
        owners=f"SELECT id FROM users WHERE id IN ({owners})",
        listings="SELECT id,symbol FROM listings WHERE " + listing_where,
    )
    for name, query in queries.items():
        cursor = connection.execute(
            text(
                f"SELECT to_jsonb(t) FROM ({query} ORDER BY id LIMIT :limit) t"
            ).execution_options(stream_results=True, yield_per=1),
            {"limit": maximum_rows + 1},
        )
        rows = []
        try:
            for row in cursor.scalars():
                count += 1
                size += len(canonical_bytes(row)) + 1
                require(count <= maximum_rows and size <= maximum_bytes, "stream bound")
                rows.append(row)
        finally:
            cursor.close()
        (tables if name in FIELDS else external)[name] = rows
    result = {"schema_version": SCHEMA, "tables": tables, "external_bindings": external}
    indexes(result, maximum_rows, maximum_bytes=maximum_bytes)
    return result


def indexes(closure, maximum_rows=100000, *, maximum_bytes=32_000_000):
    bounds(maximum_rows, maximum_bytes)
    require(
        type(closure) is dict
        and set(closure) == {"schema_version", "tables", "external_bindings"}
        and closure["schema_version"] == SCHEMA,
        "schema",
    )
    require(
        type(closure["tables"]) is dict and set(closure["tables"]) == set(FIELDS),
        "tables",
    )
    require(
        type(closure["external_bindings"]) is dict
        and set(closure["external_bindings"]) == set(EXTERNAL_FIELDS),
        "external bindings",
    )
    require(len(canonical_bytes(closure)) <= maximum_bytes, "envelope bound")
    result, count = {}, 0
    for name, fields in {**FIELDS, **EXTERNAL_FIELDS}.items():
        rows = closure["tables"].get(name, closure["external_bindings"].get(name))
        require(type(rows) is list, "rows")
        count += len(rows)
        require(count <= maximum_rows, "row bound")
        out = {}
        for row in rows:
            require(type(row) is dict and set(row) == set(fields.split()), "row fields")
            for key in ("id", "owner_id"):
                if key in row:
                    require(
                        type(row[key]) is str and str(UUID(row[key])) == row[key],
                        "required canonical UUID",
                    )
            for key, value in row.items():
                if (key == "id" or key.endswith("_id")) and value is not None:
                    require(
                        type(value) is str and str(UUID(value)) == value,
                        "canonical UUID",
                    )
            require(row["id"] not in out, "duplicate id")
            if "created_at" in row:
                require(
                    type(row["created_at"]) is str
                    and datetime.fromisoformat(row["created_at"]).utcoffset()
                    is not None,
                    "aware timestamp",
                )
            out[row["id"]] = row
        result[name] = out
    owners = set(result["owners"])
    require(
        owners == {r["owner_id"] for name in FIELDS for r in result[name].values()},
        "exact input owners",
    )
    return result


def _index(rows, key="id"):
    require(type(rows) is list, "dependency rows")
    out = {r[key]: r for r in rows}
    require(len(out) == len(rows), "dependency duplicate")
    return out


def _canonical(raw, sha, maximum):
    require(type(raw) is str and 0 < len(raw.encode()) <= maximum, "canonical bound")
    value = json.loads(raw)
    require(
        canonical_bytes(value).decode() == raw and digest(value) == sha,
        "canonical hash",
    )
    return value


def manifest_keys(closure):
    own = indexes(closure)
    keys = set()
    for row in own["research_input_objects"].values():
        require(row["kind"] in LIMITS, "object kind")
        require(
            type(row["sha256"]) is str
            and re.fullmatch("[a-f0-9]{64}", row["sha256"]) is not None,
            "object digest",
        )
        key = f"factors/{row['owner_id']}/input-objects/{row['sha256']}{SUFFIX[row['kind']]}"
        require(row["storage_key"] == key and key not in keys, "object key")
        keys.add(key)
    return keys


def validate_closure(
    closure,
    *,
    workflow_closure,
    direct_analysis_closure,
    rd_source_closure,
    execution_closure,
    read_bytes,
    inventory,
    maximum_rows=100000,
):
    """Recompute bytes after dependency checks; return data for final workflow joins."""
    require(
        (
            workflow_closure.get("schema_version"),
            rd_source_closure.get("schema_version"),
            execution_closure.get("schema_version"),
            direct_analysis_closure.get("schema_version"),
        )
        in (
            (
                "mineagent.research-workflow-restore-closure.v1",
                "mineagent.rd-source-restore-closure.v9",
                "mineagent.execution-snapshot-restore-closure.v2",
                "mineagent.direct-analysis-restore-closure.v1",
            ),
            (
                "mineagent.research-workflow-restore-closure.v1",
                "mineagent.rd-source-restore-closure.v10",
                "mineagent.execution-snapshot-restore-closure.v2",
                "mineagent.direct-analysis-restore-closure.v1",
            ),
            (
                "mineagent.research-workflow-restore-closure.v2",
                "mineagent.rd-source-restore-closure.v11",
                "mineagent.execution-snapshot-restore-closure.v2",
                "mineagent.direct-analysis-restore-closure.v1",
            ),
        ),
        "exact workflow/source dependencies",
    )
    require(callable(read_bytes), "actual reader")
    own = indexes(closure, maximum_rows)
    physical = {r["path"]: r for r in inventory}
    require(len(physical) == len(inventory), "physical duplicates")
    expected_keys = manifest_keys(closure)
    require(
        {key for key in physical if "/input-objects/" in key} == expected_keys,
        "exact physical input inventory",
    )
    objects, payloads = own["research_input_objects"], {}
    for identity, row in objects.items():
        require(
            type(row["size_bytes"]) is int
            and (0 if row["kind"] == "raw_external" else 1)
            <= row["size_bytes"]
            <= LIMITS[row["kind"]],
            "object size",
        )
        item = physical[row["storage_key"]]
        require(
            (item["sha256"], item["size_bytes"]) == (row["sha256"], row["size_bytes"]),
            "physical commitment",
        )
        raw = read_bytes(row["storage_key"], row["sha256"], LIMITS[row["kind"]])
        require(
            type(raw) is bytes
            and len(raw) == row["size_bytes"]
            and hashlib.sha256(raw).hexdigest() == row["sha256"],
            "actual object bytes",
        )
        from backend.app.research_input_storage import verify_payload

        require(verify_payload(row["kind"], raw) == row["sha256"], "typed payload")
        payloads[identity] = raw
    stages = _index(workflow_closure["tables"]["research_workflow_stages"])
    feedback_rows = [
        r
        for r in own["research_input_derivations"].values()
        if r["operation"] == "experiment_feedback_projection"
    ]
    referenced_listings = set()
    if feedback_rows:
        runs = _index(rd_source_closure["external_bindings"]["runs"])
        datasets = _index(direct_analysis_closure["tables"]["dataset_versions"])
        plans = direct_analysis_closure["tables"]["factor_analysis_plans"]
        for row in feedback_rows:
            run = runs.get(row["source_child_run_id"])
            prior = stages.get(row["source_stage_id"])
            require(
                run is not None
                and prior is not None
                and run["owner_id"] == row["owner_id"]
                and (prior["owner_id"], prior["workflow_id"], prior["child_run_id"])
                == (row["owner_id"], row["source_workflow_id"], run["id"]),
                "feedback source foreign keys",
            )
            dataset_ids = {run["dataset_version_id"]} | {
                p["label_source_dataset_id"]
                for p in plans
                if p["run_id"] == run["id"] and p["owner_id"] == row["owner_id"]
            }
            for identity in dataset_ids:
                dataset = datasets.get(identity)
                require(
                    dataset is not None and dataset["owner_id"] == row["owner_id"],
                    "referenced feedback dataset",
                )
                referenced_listings.update(
                    str(item.get("listing_id"))
                    for item in dataset["manifest"].get("listings", [])
                )
    require(
        set(own["listings"]) <= referenced_listings
        and all(
            type(row["symbol"]) is str and 0 < len(row["symbol"]) <= 200
            for row in own["listings"].values()
        ),
        "referenced listing inventory",
    )
    versions, records = own["research_input_versions"], own["research_input_records"]
    events, derivations = (
        own["research_input_derivation_events"],
        own["research_input_derivations"],
    )
    used_versions, used_events, used_objects, stage_ids, facts = (
        set(),
        set(),
        set(),
        set(),
        {},
    )
    for row in derivations.values():
        stage = stages.get(row["stage_id"])
        require(
            stage is not None
            and (
                stage["owner_id"],
                stage["workflow_id"],
                stage["kind"],
                stage["definition_canonical"],
                stage["definition_sha256"],
            )
            == (
                row["owner_id"],
                row["workflow_id"],
                row["operation"],
                row["source_canonical"],
                row["source_sha256"],
            ),
            "actual stage definition",
        )
        require(row["stage_id"] not in stage_ids, "one derivation per stage")
        stage_ids.add(row["stage_id"])
        _canonical(row["source_canonical"], row["source_sha256"], 8192)
        selected = sorted(
            (e for e in events.values() if e["derivation_id"] == row["id"]),
            key=lambda e: e["ordinal"],
        )
        parsed = []
        for event in selected:
            require(
                all(
                    event[k] == row[k] for k in ("owner_id", "workflow_id", "stage_id")
                ),
                "event scope",
            )
            outputs = _canonical(
                event["outputs_canonical"], event["outputs_sha256"], 16384
            )
            doc = DerivationEvent.model_validate_json(
                canonical_bytes(
                    {
                        "id": event["id"],
                        "derivation_id": row["id"],
                        "ordinal": event["ordinal"],
                        "state": event["state"],
                        "error_code": event["error_code"],
                        "outputs": outputs,
                        "recorded_at": event["created_at"],
                    }
                )
            )
            parsed.append(doc)
            used_events.add(event["id"])
        state = validate_event_prefix(parsed)
        actual_versions = sorted(
            (v for v in versions.values() if v["derivation_id"] == row["id"]),
            key=lambda v: v["item_ordinal"],
        )
        if row["operation"] == "external_normalization":
            definition = ExternalNormalizationDefinition.model_validate_json(
                row["source_canonical"]
            )
            require(
                (
                    row["raw_object_id"],
                    row["raw_object_sha256"],
                    row["mapping_canonical"],
                    row["mapping_sha256"],
                    row["source_workflow_id"],
                    row["source_stage_id"],
                    row["source_child_run_id"],
                )
                == (
                    str(definition.raw_object_id),
                    definition.raw_sha256,
                    canonical_bytes(definition.mapping).decode(),
                    definition.mapping.sha256,
                    None,
                    None,
                    None,
                ),
                "external definition",
            )
            raw_object = objects.get(row["raw_object_id"])
            require(
                raw_object is not None
                and (raw_object["owner_id"], raw_object["kind"], raw_object["sha256"])
                == (row["owner_id"], "raw_external", definition.raw_sha256),
                "raw source",
            )
            normalized = None
            if state in {"succeeded", "rejected"}:
                try:
                    normalized = normalize_external_json(
                        payloads[raw_object["id"]], definition.mapping
                    ).outputs
                except NormalizationError as exc:
                    require(
                        state == "rejected" and parsed[-1].error_code == exc.code,
                        "actual normalization rejection",
                    )
                else:
                    require(state == "succeeded", "false normalization rejection")
            expected = normalized if state == "succeeded" else ()
        elif row["operation"] == "experiment_feedback_projection":
            definition = FeedbackProjectionDefinition.model_validate_json(
                row["source_canonical"]
            )
            require(
                (
                    row["raw_object_id"],
                    row["raw_object_sha256"],
                    row["mapping_canonical"],
                    row["mapping_sha256"],
                    row["source_workflow_id"],
                    row["source_stage_id"],
                    row["source_child_run_id"],
                )
                == (
                    None,
                    None,
                    None,
                    None,
                    str(definition.source.workflow_id),
                    str(definition.source.stage_id),
                    str(definition.source.child_run_id),
                ),
                "feedback definition",
            )
            expected = ()
            if state == "succeeded":
                require(len(actual_versions) == 1, "single feedback output")
                version = actual_versions[0]
                proposal = ExperimentFeedbackProposal.model_validate_json(
                    version["content_canonical"]
                )
                expected = (
                    _feedback(
                        definition,
                        proposal,
                        row["owner_id"],
                        own,
                        direct_analysis_closure,
                        rd_source_closure,
                        execution_closure,
                        read_bytes,
                    ),
                )
        else:
            raise ValueError("Research input closure: unsupported derivation")
        require(
            len(actual_versions) == len(expected), "exact derivation output inventory"
        )
        refs = []
        for ordinal, (version, value) in enumerate(
            zip(actual_versions, expected, strict=True)
        ):
            record, obj = (
                records.get(version["record_id"]),
                objects.get(version["object_id"]),
            )
            raw = canonical_bytes(value)
            require(
                record is not None
                and obj is not None
                and all(
                    r["owner_id"] == row["owner_id"] for r in (version, record, obj)
                ),
                "output owner",
            )
            require(
                version["item_ordinal"] == ordinal
                and type(version["item_ordinal"]) is int
                and type(version["ordinal"]) is int
                and version["ordinal"] == 1
                and record["origin"] == value.origin
                and obj["kind"]
                == (
                    "normalized_external"
                    if value.origin == "normalized_external_proposal"
                    else "normalized_feedback"
                ),
                "output identity",
            )
            require(
                version["content_canonical"].encode() == raw == payloads[obj["id"]]
                and version["content_sha256"] == obj["sha256"] == digest(value),
                "recomputed output bytes",
            )
            refs.append(
                {
                    "origin": value.origin,
                    "record_id": version["record_id"],
                    "version_id": version["id"],
                    "content_sha256": version["content_sha256"],
                }
            )
            used_versions.add(version["id"])
            used_objects.add(obj["id"])
        require(
            [v.model_dump(mode="json") for v in parsed[-1].outputs] == refs,
            "actual event outputs",
        )
        facts[row["id"]] = {
            "state": state,
            "outputs": tuple(expected),
            "events": tuple(parsed),
        }
    require(
        used_events == set(events) and used_versions == set(versions),
        "orphan event/version",
    )
    require(
        used_objects
        == {r["id"] for r in objects.values() if r["kind"] != "raw_external"},
        "orphan normalized object",
    )
    for record in records.values():
        rows = sorted(
            (v for v in versions.values() if v["record_id"] == record["id"]),
            key=lambda v: v["ordinal"],
        )
        require(
            rows
            and len(rows) <= 64
            and [v["ordinal"] for v in rows] == list(range(1, len(rows) + 1))
            and all(type(v["ordinal"]) is int for v in rows),
            "contiguous versions",
        )
        require(
            record["current_version_id"] == rows[-1]["id"]
            and type(record["disabled"]) is bool
            and type(record["revision"]) is int
            and 1 <= record["revision"] <= 1000000,
            "record state",
        )
    return facts


def _feedback(definition, proposal, owner, own, direct, rd, execution, read_bytes):
    from backend.app.experiment_services import dataset_identities
    from packages.factors.execution_snapshot import ExecutionSnapshotManifest

    source = definition.source
    require(proposal.source == source, "feedback source reference")
    run = _index(rd["external_bindings"]["runs"]).get(str(source.child_run_id))
    intent = _index(direct["tables"]["factor_direct_analysis_intents"]).get(
        str(source.output_id)
    )
    outcome = _index(
        direct["tables"]["factor_direct_analysis_outcomes"], "analysis_id"
    ).get(str(source.output_id))
    require(
        run is not None and intent is not None and outcome is not None,
        "actual feedback result",
    )
    require(
        run["owner_id"] == owner
        and (run["purpose"], run["engine"]) == ("factor_research", "rdagent"),
        "feedback child owner/type",
    )
    require(
        all(
            r["owner_id"] == owner and r["run_id"] == run["id"]
            for r in (intent, outcome)
        )
        and all(intent[k] == outcome[k] for k in ("attempt_id", "generation")),
        "analysis scope",
    )
    plan = _index(direct["tables"]["factor_analysis_plans"]).get(intent["plan_id"])
    publication = _index(
        execution["tables"]["factor_execution_snapshot_publications"]
    ).get(intent["snapshot_id"])
    snapshot = _index(
        execution["tables"]["factor_execution_snapshot_outcomes"], "publication_id"
    ).get(intent["snapshot_id"])
    require(
        plan is not None and publication is not None and snapshot is not None,
        "analysis snapshot/plan",
    )
    require(
        all(
            r["owner_id"] == owner and r["run_id"] == run["id"]
            for r in (plan, publication, snapshot)
        ),
        "snapshot scope",
    )
    require(
        all(
            publication[k] == outcome[k] == snapshot[k]
            for k in ("attempt_id", "generation")
        ),
        "snapshot attempt",
    )
    measurement = DirectObjectiveMeasurement.model_validate_json(
        outcome["measurement_canonical"]
    )
    require(
        canonical_bytes(measurement).decode() == outcome["measurement_canonical"]
        and digest(measurement)
        == outcome["measurement_sha256"]
        == proposal.measurement_sha256,
        "actual measurement commitment",
    )
    require(
        measurement.model_dump(mode="json") == outcome["measurement"]
        and measurement.result_sha256
        == outcome["result_sha256"]
        == source.content_sha256,
        "measurement/result binding",
    )
    result = _canonical(outcome["result_canonical"], outcome["result_sha256"], 1048576)
    require(
        result == outcome["result"]
        and len(outcome["result_canonical"].encode()) == outcome["result_size_bytes"],
        "analysis result commitment",
    )
    path = f"research-processes/{owner}/{outcome['admission_id']}/output/analysis.json"
    raw = read_bytes(path, outcome["result_sha256"], 1048576)
    require(
        type(raw) is bytes and raw == outcome["result_canonical"].encode(),
        "actual retained analysis result",
    )
    manifest = ExecutionSnapshotManifest.model_validate_json(
        canonical_bytes(publication["manifest"])
    )
    require(
        digest(manifest)
        == publication["manifest_sha256"]
        == snapshot["manifest_sha256"]
        and str(manifest.owner_id) == owner
        and str(manifest.run_id) == run["id"]
        and str(manifest.snapshot_id) == publication["id"]
        and manifest.input_sha256 == snapshot["input_sha256"]
        and manifest.output_rows_sha256 == snapshot["output_sha256"],
        "snapshot manifest binding",
    )
    require(
        str(manifest.input.dataset.dataset_version_id) == run["dataset_version_id"]
        and intent["label_source_id"] == plan["label_source_dataset_id"],
        "release dataset identities",
    )
    datasets = _index(direct["tables"]["dataset_versions"])
    factor_dataset, label_dataset = (
        datasets.get(run["dataset_version_id"]),
        datasets.get(intent["label_source_id"]),
    )
    require(
        factor_dataset is not None
        and label_dataset is not None
        and factor_dataset["owner_id"] == label_dataset["owner_id"] == owner
        and factor_dataset["content_sha256"] == manifest.input.dataset.content_sha256
        and label_dataset["content_sha256"] == plan["label_source_manifest_sha256"],
        "actual release datasets",
    )
    evidence = {
        "run_id": run["id"],
        "execution_snapshot_id": publication["id"],
        "manifest_sha256": publication["manifest_sha256"],
        "input_sha256": snapshot["input_sha256"],
        "output_sha256": snapshot["output_sha256"],
        "extent": "conservative_full_retained_dataset",
        "qualification": "historical_disclosure_not_live_execution_authority",
        "analysis_id": intent["id"],
        "plan_id": plan["id"],
        "plan_config_sha256": plan["config_sha256"],
        "plan_input_sha256": plan["input_sha256"],
        "objective_sha256": plan["objective_sha256"],
        "source_policy_sha256": plan["source_policy_sha256"],
        "label_manifest_sha256": intent["label_manifest_sha256"],
        "label_recipe_sha256": intent["label_recipe_sha256"],
        "protocol_sha256": intent["protocol_sha256"],
        "analysis_input_sha256": intent["analysis_input_sha256"],
        "result_sha256": outcome["result_sha256"],
        "measurement_sha256": outcome["measurement_sha256"],
    }
    exposures = _index(direct["tables"]["data_usage_exposures"])
    symbols = {identity: r["symbol"] for identity, r in own["listings"].items()}
    for identity, (role, dataset) in zip(
        proposal.exposure_ids,
        (("factor", factor_dataset), ("label", label_dataset)),
        strict=True,
    ):
        exposure = exposures.get(str(identity))
        key = f"engine-result:direct_analysis:{intent['id']}:{role}:v1"
        matches = [
            e
            for e in exposures.values()
            if e["owner_id"] == owner and e["source_key"] == key
        ]
        require(
            exposure is not None and matches == [exposure],
            "actual unique release exposure",
        )
        expected = {
            "owner_id": owner,
            "run_id": run["id"],
            "experiment_id": run["experiment_id"],
            "split_version_id": run["split_version_id"],
            "dataset_version_id": dataset["id"],
            "source_key": key,
            "start_date": dataset["start_date"],
            "end_date": dataset["end_date"],
            "purpose": "engine_direct_analysis_release",
            "identities": dataset_identities(
                SimpleNamespace(**dataset), listing_symbols=symbols
            ),
            "provenance": {
                **evidence,
                "role": role,
                "dataset_version_id": dataset["id"],
                "dataset_content_sha256": dataset["content_sha256"],
            },
        }
        require(
            all(exposure[k] == v for k, v in expected.items()),
            "exact release provenance",
        )
    return project_direct_feedback(definition, measurement, proposal.exposure_ids)
