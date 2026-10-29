"""032 independent normal OE note retention; never result/release authority.

The final cross-check must follow the complete normal OE physical validator.
No normal process output is registered as a legacy artifact.
"""

from sqlalchemy import text

from packages.factors.contracts import canonical_bytes

SCHEMA = "mineagent.note-normal-oe-closure.v1"
FIELDS = {
    "dataset_versions": "id owner_id market start_date end_date content_sha256 manifest storage_key",
    "research_runs": "id owner_id experiment_id split_version_id dataset_version_id",
    "listings": "id symbol",
    "data_usage_exposures": "id owner_id experiment_id split_version_id run_id dataset_version_id source_key identities start_date end_date purpose provenance created_at",
    "research_notes": "id owner_id created_at",
    "research_note_revisions": "id note_id owner_id revision revision_sha256 pins_sha256 content evidence_snapshot admission_snapshot title tags status search_text change_note idempotency_key request_sha256 created_at",
    "research_note_evidence_pins": "id revision_id owner_id resource_kind resource_id sha256 details",
    "research_note_normal_oe_pins": "pin_id owner_id run_id attempt_id generation execution_id engine_outcome_id factor_exposure_id market_exposure_id",
}


def _require(condition):
    if not condition:
        raise ValueError("Normal OE note retained closure differs")


def read_closure(connection, maximum_rows, *, maximum_bytes=32_000_000):
    """Capture all revisions, including superseded and mixed-kind inventories."""
    _require(type(maximum_rows) is int and 0 < maximum_rows <= 100000)
    _require(type(maximum_bytes) is int and 0 < maximum_bytes <= 512 * 1024**2)
    tables, count, size = {}, 0, 0
    for table, fields in FIELDS.items():
        key = "pin_id" if table == "research_note_normal_oe_pins" else "id"
        where = (
            " WHERE id IN (SELECT factor_exposure_id FROM research_note_normal_oe_pins UNION SELECT market_exposure_id FROM research_note_normal_oe_pins)"
            if table == "data_usage_exposures"
            else ""
        )
        if table == "dataset_versions":
            where = " WHERE id IN (SELECT dataset_version_id FROM data_usage_exposures WHERE id IN (SELECT factor_exposure_id FROM research_note_normal_oe_pins UNION SELECT market_exposure_id FROM research_note_normal_oe_pins))"
        elif table == "research_runs":
            where = " WHERE id IN (SELECT run_id FROM research_note_normal_oe_pins UNION SELECT p.run_id FROM factor_execution_snapshot_publications p JOIN oe_structured_executions e ON e.snapshot_id=p.id JOIN research_note_normal_oe_pins n ON n.execution_id=e.id)"
        result = connection.execute(
            text(
                f"SELECT to_jsonb(t) FROM (SELECT {','.join(fields.split())} "
                f"FROM {table}{where} ORDER BY {key} LIMIT :limit) t"
            ).execution_options(stream_results=True, yield_per=1),
            {"limit": maximum_rows + 1},
        )
        rows = []
        try:
            for row in result.scalars():
                count += 1
                size += len(canonical_bytes(row)) + 1
                _require(count <= maximum_rows and size <= maximum_bytes)
                rows.append(row)
        finally:
            result.close()
        tables[table] = rows
    value = {"schema_version": SCHEMA, "tables": tables}
    _require(len(canonical_bytes(value)) <= maximum_bytes)
    return value


def closure_for(snapshot, capabilities, maximum_rows):
    _require(type(maximum_rows) is int and 0 < maximum_rows <= 100000)
    schema = getattr(capabilities, "normal_oe_note_schema", None)
    _require(schema in (None, SCHEMA))
    _require((schema is not None) == ("normal_oe_note_closure" in snapshot))
    if schema is None:
        return None
    _require(
        (
            capabilities.research_closure_schema,
            capabilities.rd_source_closure_schema,
            capabilities.oe_structured_closure_schema,
            capabilities.workflow_schema,
        )
        == (
            "mineagent.research-restore-closure.v16",
            "mineagent.rd-source-restore-closure.v11",
            "mineagent.oe-structured-restore-closure.v2",
            "mineagent.research-workflow-closure.v2",
        )
    )
    value = snapshot["normal_oe_note_closure"]
    _require(type(value) is dict and set(value) == {"schema_version", "tables"})
    _require(value["schema_version"] == SCHEMA)
    tables = value["tables"]
    _require(type(tables) is dict and set(tables) == set(FIELDS))
    count, indexed = 0, {}
    from uuid import UUID

    for table, fields in FIELDS.items():
        rows = tables[table]
        _require(type(rows) is list)
        count += len(rows)
        _require(count <= maximum_rows)
        index = {}
        key = "pin_id" if table == "research_note_normal_oe_pins" else "id"
        previous = None
        for row in rows:
            _require(type(row) is dict and set(row) == set(fields.split()))
            identity = row[key]
            _require(type(identity) is str and str(UUID(identity)) == identity)
            _require(
                identity not in index and (previous is None or previous < identity)
            )
            previous = identity
            index[identity] = row
        indexed[table] = index
    return indexed


def _index(snapshot, closure, table, key="id"):
    rows = snapshot[closure]["tables"][table]
    result = {row[key]: row for row in rows}
    _require(len(result) == len(rows))
    return result


def _execution_view(snapshot, execution, outcome, candidate, config, oe_facts):
    """Only called after full physical OE verification, not a replacement for it."""
    from uuid import UUID

    from backend.app.structured_factor_history import HistoricalStructuredExecution
    from packages.factors.contracts import FactorDefinition, digest
    from packages.structured_factor_strategy.views import execution_view
    from packages.structured_factor_strategy.wire import StructuredResult

    publication = _index(
        snapshot, "execution_closure", "factor_execution_snapshot_publications"
    )[execution["snapshot_id"]]
    source = _index(
        snapshot,
        "execution_closure",
        "factor_execution_snapshot_outcomes",
        "publication_id",
    )[publication["id"]]
    version = _index(snapshot, "factor_closure", "factor_versions")[
        publication["factor_version_id"]
    ]
    result = StructuredResult.model_validate_json(outcome["result_canonical"])
    measurement = oe_facts["measurements"][candidate["id"]]
    definition = FactorDefinition.model_validate_json(
        canonical_bytes(version["definition"])
    )
    _require(
        digest(result) == outcome["result_sha256"]
        and digest(measurement) == outcome["measurement_sha256"]
        and canonical_bytes(result).decode("ascii") == outcome["result_canonical"]
        and canonical_bytes(measurement).decode("ascii")
        == outcome["measurement_canonical"]
        and definition.sha256 == version["definition_sha256"]
        and definition.code_sha256 == version["code_sha256"]
        and str(config.fixed_inputs.factor_version_id) == version["id"]
        and config.fixed_inputs.snapshot_input_sha256 == source["input_sha256"]
    )
    history = HistoricalStructuredExecution(
        execution_id=UUID(execution["id"]),
        run_id=UUID(execution["run_id"]),
        structured_strategy_version_id=UUID(execution["program_version_id"]),
        factor_version_id=UUID(version["id"]),
        factor_id=UUID(version["factor_id"]),
        parent_factor_version_id=(
            UUID(version["parent_version_id"]) if version["parent_version_id"] else None
        ),
        snapshot_id=UUID(publication["id"]),
        execution_snapshot_run_id=UUID(publication["run_id"]),
        admission_id=UUID(outcome["admission_id"]),
        result_sha256=outcome["result_sha256"],
        result=result,
        factor_definition=definition,
        factor_code_sha256=version["code_sha256"],
        snapshot_input_sha256=source["input_sha256"],
    )
    return execution_view(history), publication, source, version


def _revisions(indexed):
    from types import SimpleNamespace

    from backend.app.research_note_services import validate_revision
    from packages.factors.contracts import digest

    notes = indexed["research_notes"]
    revisions = indexed["research_note_revisions"]
    pins = indexed["research_note_evidence_pins"]
    normal = {
        key: row
        for key, row in pins.items()
        if row["resource_kind"] == "normal_oe_execution"
    }
    _require(set(normal) == set(indexed["research_note_normal_oe_pins"]))
    grouped = {key: [] for key in revisions}
    for pin in pins.values():
        revision = revisions[pin["revision_id"]]
        _require(pin["owner_id"] == revision["owner_id"])
        grouped[revision["id"]].append(pin)
    result = {}
    seen = set()
    for row in revisions.values():
        _require(notes[row["note_id"]]["owner_id"] == row["owner_id"])
        pair = (row["note_id"], row["revision"])
        _require(pair not in seen)
        seen.add(pair)
        content = validate_revision(SimpleNamespace(**row))
        inventory, keys = [], set()
        for pin in grouped[row["id"]]:
            key = (pin["resource_kind"], pin["resource_id"])
            _require(key not in keys)
            keys.add(key)
            inventory.append(
                {
                    name: pin[name]
                    for name in ("resource_kind", "resource_id", "sha256", "details")
                }
            )
        inventory.sort(key=lambda pin: (pin["resource_kind"], pin["resource_id"]))
        _require(digest(inventory) == row["pins_sha256"])
        result[row["id"]] = content
    return result, normal


def _release(
    indexed,
    attachment,
    execution,
    outcome,
    candidate,
    config,
    publication,
    source,
    version,
):
    from types import SimpleNamespace

    from backend.app.experiment_services import dataset_identities
    from packages.factors.contracts import digest

    run = indexed["research_runs"][attachment["run_id"]]
    source_run = indexed["research_runs"][publication["run_id"]]
    _require(run["owner_id"] == source_run["owner_id"] == attachment["owner_id"])
    evidence = {
        "schema_version": "mineagent.oe-structured-result-release.v1",
        "run_id": run["id"],
        "execution_id": execution["id"],
        "candidate_id": candidate["id"],
        "structured_strategy_version_id": execution["program_version_id"],
        "factor_version_id": version["id"],
        "factor_id": version["factor_id"],
        "execution_snapshot_id": publication["id"],
        "snapshot_source": {
            "run_id": source_run["id"],
            "execution_snapshot_id": publication["id"],
            "manifest_sha256": publication["manifest_sha256"],
            "input_sha256": source["input_sha256"],
            "output_sha256": source["output_sha256"],
            "extent": "conservative_full_retained_dataset",
            "qualification": "historical_disclosure_not_live_execution_authority",
        },
        "config_sha256": digest(config),
        "program_sha256": candidate["program_sha256"],
        "strategy_input_sha256": execution["strategy_input_sha256"],
        "market_sha256": execution["market_sha256"],
        "result_sha256": outcome["result_sha256"],
        "measurement_sha256": outcome["measurement_sha256"],
        "objective_sha256": outcome["objective_sha256"],
        "extent": "conservative_full_retained_dataset_including_history_and_forward_labels",
        "qualification": "fixed_snapshot_comparison_not_causal_gain",
    }
    _require(run["dataset_version_id"] == str(config.fixed_inputs.dataset_version_id))
    source_binding = publication["manifest"]["input"]["dataset"]
    _require(source_binding["dataset_version_id"] == source_run["dataset_version_id"])
    _require(
        indexed["dataset_versions"][source_run["dataset_version_id"]]["content_sha256"]
        == source_binding["content_sha256"]
    )
    _require(
        indexed["dataset_versions"][str(config.fixed_inputs.dataset_version_id)][
            "content_sha256"
        ]
        == config.fixed_inputs.dataset_sha256
    )
    symbols = {key: row["symbol"] for key, row in indexed["listings"].items()}
    _require(attachment["factor_exposure_id"] != attachment["market_exposure_id"])
    for role, dataset_id in (
        ("factor", source_run["dataset_version_id"]),
        ("market", str(config.fixed_inputs.dataset_version_id)),
    ):
        dataset = indexed["dataset_versions"][dataset_id]
        row = indexed["data_usage_exposures"][attachment[f"{role}_exposure_id"]]
        _require(dataset["owner_id"] == run["owner_id"])
        expected = {
            **evidence,
            "role": role,
            "dataset_version_id": dataset_id,
            "dataset_content_sha256": dataset["content_sha256"],
        }
        _require(
            tuple(
                row[key]
                for key in (
                    "owner_id",
                    "run_id",
                    "experiment_id",
                    "split_version_id",
                    "dataset_version_id",
                    "start_date",
                    "end_date",
                    "purpose",
                    "source_key",
                )
            )
            == (
                run["owner_id"],
                run["id"],
                run["experiment_id"],
                run["split_version_id"],
                dataset_id,
                dataset["start_date"],
                dataset["end_date"],
                "engine_structured_execution_release",
                f"engine-result:oe_structured_execution:{execution['id']}:{role}:v1",
            )
            and row["provenance"] == expected
            and row["identities"]
            == dataset_identities(SimpleNamespace(**dataset), listing_symbols=symbols)
        )


def validate_snapshot(snapshot, capabilities, maximum_rows, *, oe_facts):
    from backend.app.normal_oe_note_contracts import NormalOENotePinDetails
    from packages.factors.contracts import digest

    indexed = closure_for(snapshot, capabilities, maximum_rows)
    if indexed is None:
        return
    _require(
        type(oe_facts) is dict
        and set(oe_facts) == {"configs", "measurements", "epochs"}
    )
    from backend.app.services import DomainError

    try:
        contents, normal = _revisions(indexed)
    except DomainError:
        raise ValueError("Normal OE note revision integrity differs") from None
    actual_runs = {
        r["id"]: r for r in snapshot["research_closure"]["external_bindings"]["runs"]
    }
    for key, row in indexed["research_runs"].items():
        _require(actual_runs[key]["owner_id"] == row["owner_id"])
    _dataset_inventory(snapshot, indexed)
    executions = _index(snapshot, "oe_structured_closure", "oe_structured_executions")
    outcomes = _index(
        snapshot, "oe_structured_closure", "oe_structured_engine_outcomes"
    )
    candidates = _index(snapshot, "oe_structured_closure", "oe_structured_candidates")
    checked = {}
    used_exposures, used_runs, used_datasets = set(), set(), set()
    for pin_id, pin in normal.items():
        attachment = indexed["research_note_normal_oe_pins"][pin_id]
        _require(
            type(attachment["generation"]) is int and attachment["generation"] == 1
        )
        execution = executions[attachment["execution_id"]]
        outcome = outcomes[attachment["engine_outcome_id"]]
        candidate = candidates[execution["candidate_id"]]
        scope = tuple(
            attachment[k] for k in ("owner_id", "run_id", "attempt_id", "generation")
        )
        for row in (execution, outcome, candidate):
            _require(
                tuple(
                    row[k] for k in ("owner_id", "run_id", "attempt_id", "generation")
                )
                == scope
            )
        _require(
            pin["owner_id"] == attachment["owner_id"]
            and pin["resource_id"] == execution["id"] == outcome["execution_id"]
            and outcome["candidate_id"] == candidate["id"]
            and pin["sha256"] == outcome["result_sha256"]
        )
        config = oe_facts["configs"][execution["run_id"]]
        view, publication, source, version = _execution_view(
            snapshot, execution, outcome, candidate, config, oe_facts
        )
        expected = {
            "schema_version": "mineagent.note-normal-oe-pin.v1",
            "run_id": execution["run_id"],
            "attempt_id": execution["attempt_id"],
            "generation": execution["generation"],
            "candidate_id": candidate["id"],
            "program_version_id": execution["program_version_id"],
            "program_sha256": view.program_sha256,
            "factor_id": str(view.factor_id),
            "factor_version_id": str(view.factor_version_id),
            "snapshot_id": str(view.execution_snapshot_id),
            "snapshot_input_sha256": view.snapshot_input_sha256,
            "market_sha256": view.market_sha256,
            "config_sha256": digest(config),
            "measurement_sha256": outcome["measurement_sha256"],
        }
        _require(
            NormalOENotePinDetails.model_validate_json(
                canonical_bytes(pin["details"])
            ).model_dump(mode="json")
            == expected
            == pin["details"]
        )
        used_exposures.update(
            attachment[k] for k in ("factor_exposure_id", "market_exposure_id")
        )
        used_runs.update((execution["run_id"], publication["run_id"]))
        used_datasets.update(
            (
                indexed["research_runs"][publication["run_id"]]["dataset_version_id"],
                str(config.fixed_inputs.dataset_version_id),
            )
        )
        _release(
            indexed,
            attachment,
            execution,
            outcome,
            candidate,
            config,
            publication,
            source,
            version,
        )
        checked[(pin["revision_id"], pin["resource_id"])] = (pin, expected, view)
    _require(used_exposures == set(indexed["data_usage_exposures"]))
    _require(used_runs == set(indexed["research_runs"]))
    _require(used_datasets == set(indexed["dataset_versions"]))
    used = set()
    for revision_id, content in contents.items():
        row = indexed["research_note_revisions"][revision_id]
        for ordinal, ref in enumerate(content.evidence):
            if ref.kind not in {
                "normal_oe_execution",
                "normal_oe_decision",
                "normal_oe_fill",
            }:
                continue
            key = (revision_id, str(ref.execution_id))
            pin, details, view = checked[key]
            identity = ref.model_dump(
                mode="json",
                exclude={
                    "kind",
                    "label",
                    "decision_sequence",
                    "native_fill_id",
                    "listing_id",
                    "session_date",
                    "row_sha256",
                },
            )
            expected_identity = {
                k: v
                for k, v in details.items()
                if k not in {"schema_version", "attempt_id"}
            }
            expected_identity.update(
                owner_id=pin["owner_id"],
                execution_id=pin["resource_id"],
                result_sha256=pin["sha256"],
                execution_attempt_id=details["attempt_id"],
            )
            _require(identity == expected_identity)
            _require(
                row["evidence_snapshot"][ordinal]
                == {
                    "schema_version": "mineagent.normal-oe-note-admission.v1",
                    "reference": ref.model_dump(mode="json"),
                }
            )
            validate_row_reference(ref, view)
            used.add(key)
    _require(used == set(checked))


def read_snapshot(connection, capabilities, limits):
    schema = getattr(capabilities, "normal_oe_note_schema", None)
    _require(schema in (None, SCHEMA))
    if schema is None:
        return {}
    return {
        "normal_oe_note_closure": read_closure(
            connection, limits.max_files, maximum_bytes=limits.max_manifest_bytes
        )
    }


def _dataset_inventory(snapshot, indexed):
    actual_datasets = _index(snapshot, "direct_analysis_closure", "dataset_versions")
    for key, row in indexed["dataset_versions"].items():
        _require(key == row["id"])
        _require(row["storage_key"] == f"datasets/{row['owner_id']}/{key}")
        retained = [
            item for item in snapshot["datasets"] if item["path"] == row["storage_key"]
        ]
        _require(len(retained) == 1)
        _require(
            retained[0]["sha256"] == row["content_sha256"]
            and retained[0]["manifest"] == row["manifest"]
        )
        if key in actual_datasets:
            actual = actual_datasets[key]
            _require(all(actual[field] == value for field, value in row.items()))


def validate_row_reference(ref, view):
    """Validate the exact typed view row, never pretend this alone admits a result."""
    from packages.factors.contracts import digest

    if ref.kind == "normal_oe_decision":
        rows = [r for r in view.decisions if r.event_seq == ref.decision_sequence]
        _require(len(rows) == 1 and digest(rows[0]) == ref.row_sha256)
    elif ref.kind == "normal_oe_fill":
        rows = [r for r in view.fills if r.native_fill_id == ref.native_fill_id]
        _require(
            len(rows) == 1
            and digest(rows[0]) == ref.row_sha256
            and rows[0].listing_id == ref.listing_id
            and str(rows[0].session_date) == ref.session_date
        )
