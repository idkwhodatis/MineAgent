"""024 initial-epoch checkpoint graph, not process/restore/resume authority.

Each registered checkpoint explicitly selects a closed control-prefix subgraph.
This validates membership, not inferred database insertion timestamps. Only the
highest save ordinal covering the complete current graph qualifies as final.
Shape/FKs, specs, budgets and physical processes are separately checked by the
caller. Partial failed epochs may retain incomplete placement/upload state, but
receive no completed-checkpoint qualification from this component.
"""

import hashlib
from collections import defaultdict
from types import SimpleNamespace
from uuid import UUID

from backend.app.openevolve_backup_inventory import FOREIGN_KEYS, UUID_COLUMNS
from backend.app.openevolve_backup_population import validate_population
from backend.app.openevolve_checkpoint_events import validate_event_objects
from backend.app.openevolve_checkpoint_publication import TABLES
from backend.app.openevolve_services import _pending_intent_ids, _validate_control_chain
from packages.openevolve_adapter.checkpoint_evidence import validate_checkpoint_evidence
from packages.openevolve_adapter.checkpoint_native import _json, canonical
from packages.openevolve_adapter.host_contracts import (
    CheckpointInventory,
    EvidenceRef,
    InitialCheckpointClosure,
)
from packages.openevolve_adapter.worker_checkpoint_closure import (
    validate_worker_closure,
)

SCOPE = ("owner_id", "run_id", "attempt_id", "generation")
ROW_TABLES = {key: model.__tablename__ for key, model in TABLES.items()}


def _row(table, value):
    """Data-only namespace for pure graph validators; never an AttemptFence."""
    return SimpleNamespace(
        **{
            key: UUID(item) if key in UUID_COLUMNS[table] and item is not None else item
            for key, item in value.items()
        }
    )


def validate_graph(closure, research_closure, read_object):
    """Return only fully validated published epoch IDs, after actual byte reads.

    read_object(owner_id,id,kind,sha) must resolve exact registered physical bytes;
    it is not a caller-supplied DTO. The callback and predecessor closure checks
    remain mandatory even when this graph validator succeeds.
    """
    if not callable(read_object):
        raise TypeError("Registered object reader required")
    tables = closure["tables"]
    epochs = {row["id"]: row for row in tables["strategy_search_epochs"]}
    if len(epochs) != len(tables["strategy_search_epochs"]):
        raise ValueError("Duplicate epoch")
    runs = {row["id"]: row for row in research_closure["external_bindings"]["runs"]}
    grouped = {}
    names = set(ROW_TABLES.values()) | {
        "search_checkpoint_intents",
        "search_checkpoint_publications",
    }
    for name in names:
        by_epoch = defaultdict(list)
        seen = set()
        for row in tables[name]:
            epoch = epochs.get(row["epoch_id"])
            if epoch is None or any(row[k] != epoch[k] for k in SCOPE):
                raise ValueError("Checkpoint row crosses epoch scope")
            if row["id"] in seen:
                raise ValueError("Duplicate checkpoint graph identity")
            seen.add(row["id"])
            by_epoch[row["epoch_id"]].append(row)
            if len(by_epoch[row["epoch_id"]]) > 64:
                raise ValueError("Checkpoint epoch row bound")
        grouped[name] = by_epoch
    files_by_publication = defaultdict(list)
    seen_files = set()
    for row in tables["search_checkpoint_files"]:
        if row["id"] in seen_files:
            raise ValueError("Duplicate checkpoint file")
        seen_files.add(row["id"])
        files_by_publication[row["publication_id"]].append(row)
    evaluation_by_id = {row["id"]: row for row in tables["search_evaluations"]}
    outcomes_by_epoch = defaultdict(list)
    seen_outcomes = set()
    for row in tables["search_evaluation_outcomes"]:
        evaluation = evaluation_by_id.get(row["evaluation_id"])
        if evaluation is None or any(row[k] != evaluation[k] for k in SCOPE):
            raise ValueError("Checkpoint evaluation outcome crosses scope")
        if row["id"] in seen_outcomes:
            raise ValueError("Duplicate checkpoint evaluation outcome")
        seen_outcomes.add(row["id"])
        outcomes_by_epoch[evaluation["epoch_id"]].append(row)
    published, accepted_publications = set(), set()
    for epoch_id, epoch_data in epochs.items():
        run = runs.get(epoch_data["run_id"])
        if run is None or run["owner_id"] != epoch_data["owner_id"]:
            raise ValueError("Checkpoint epoch owner differs")
        epoch = _row("strategy_search_epochs", epoch_data)
        # This is only immutable scope data used by a pure content comparator.
        scope = SimpleNamespace(**{key: getattr(epoch, key) for key in SCOPE})
        rows = {
            key: [_row(name, row) for row in grouped[name][epoch_id]]
            for key, name in ROW_TABLES.items()
        }
        controls = rows["control_transition_ids"]
        intents = rows["intent_ids"]
        terminals = rows["terminal_ids"]
        publications = grouped["search_checkpoint_publications"][epoch_id]
        checkpoint_intents = grouped["search_checkpoint_intents"][epoch_id]
        ordinals = [item.get("save_ordinal") for item in checkpoint_intents]
        if any(type(n) is not int or not 1 <= n <= 4 for n in ordinals) or sorted(
            ordinals
        ) != list(range(1, len(ordinals) + 1)):
            raise ValueError("Exact contiguous checkpoint save ordinals required")
        if len({row["intent_id"] for row in publications}) != len(publications):
            raise ValueError("Duplicate checkpoint publication for save intent")
        if not controls:
            if (
                intents
                or terminals
                or rows["controller_event_receipt_ids"]
                or publications
            ):
                raise ValueError("Checkpoint graph lacks control cursor")
            continue
        latest = max(controls, key=lambda row: row.sequence)
        _validate_control_chain(controls, intents, terminals, latest)
        events = rows["controller_event_receipt_ids"]
        if len(events) != len(controls) or {r.control_id for r in events} != {
            r.id for r in controls
        }:
            raise ValueError("Control cursor lacks exact controller event")

        control_by_id = {row.id: row for row in controls}
        intent_by_id = {row.id: row for row in intents}
        for event in events:
            control = control_by_id[event.control_id]
            iteration = (
                intent_by_id[control.sampling_intent_id].native_iteration
                if control.kind == "sampling"
                else control.frontier
            )
            if (
                event.admission_id,
                event.control_sequence,
                event.control_frontier,
                event.control_state_sha256,
                event.command_sha256,
                event.intent_id,
                event.terminal_id,
                event.native_iteration,
                event.event_kind,
            ) != (
                epoch.admission_id,
                control.sequence,
                control.frontier,
                control.state_sha256,
                control.request_sha256,
                control.sampling_intent_id,
                control.terminal_id,
                iteration,
                "iteration_intent"
                if control.kind == "sampling"
                else "terminal_applied",
            ):
                raise ValueError("Committed event/control binding differs")
        event_by_id = {row.id: row for row in events}
        ticket_by_id = {row.id: row for row in rows["worker_ticket_ids"]}
        for ticket in ticket_by_id.values():
            event = event_by_id.get(ticket.controller_receipt_id)
            if (
                ticket.admission_id != epoch.admission_id
                or event is None
                or event.event_kind != "iteration_intent"
                or event.intent_id != ticket.intent_id
            ):
                raise ValueError("Committed worker ticket provenance differs")
        expected_by_id = {row.id: row for row in rows["worker_expected_identity_ids"]}
        for expected in expected_by_id.values():
            ticket = ticket_by_id.get(expected.ticket_id)
            if ticket is None or (expected.admission_id, expected.intent_id) != (
                ticket.admission_id,
                ticket.intent_id,
            ):
                raise ValueError("Committed placement ticket differs")
        for assignment in rows["worker_assignment_ids"]:
            expected = expected_by_id.get(assignment.expected_identity_id)
            fields = (
                "admission_id",
                "intent_id",
                "ticket_id",
                "host_pid",
                "start_ticks",
                "pid_namespace",
                "cgroup",
            )
            if expected is None or any(
                getattr(assignment, key) != getattr(expected, key) for key in fields
            ):
                raise ValueError("Committed assignment kernel identity differs")

        def read(reference, kind, owner_id=epoch.owner_id):
            raw = read_object(owner_id, reference.id, kind, reference.sha256)
            if (
                type(raw) is not bytes
                or not 0 < len(raw) <= 8 * 1024**2
                or hashlib.sha256(raw).hexdigest() != reference.sha256
            ):
                raise ValueError("Checkpoint registered object bytes differ")
            value = _json(raw)
            if canonical(value) != raw:
                raise ValueError("Checkpoint object is not canonical")
            return value

        def population(control, read=read, epoch_id=epoch_id):
            value = read(
                EvidenceRef(id=control.state_object_id, sha256=control.state_sha256),
                "search_population",
            )
            return validate_population(
                value, epoch_id, control.frontier, control.state_sha256, tables
            )

        validate_event_objects(
            scope, epoch, rows, read_evidence=read, validated_population=population
        )
        if not publications:
            # Missing placement may be legitimate after interrupted admission.
            # No complete checkpoint is returned, and no pending hold is released.
            continue
        for publication_data in publications:
            publication = _row("search_checkpoint_publications", publication_data)
            intent_data = next(
                (
                    r
                    for r in checkpoint_intents
                    if r["id"] == str(publication.intent_id)
                ),
                None,
            )
            if intent_data is None:
                raise ValueError("Checkpoint publication lacks exact intent")
            intent = _row("search_checkpoint_intents", intent_data)
            control = control_by_id.get(intent.control_transition_id)
            if (
                control is None
                or (publication.frontier, publication.state_sha256)
                != (intent.frontier, intent.state_sha256)
                or (intent.frontier, intent.state_sha256)
                != (control.frontier, control.state_sha256)
            ):
                raise ValueError("Checkpoint intent cursor differs")
            inventory = CheckpointInventory.model_validate_json(
                canonical(
                    read(
                        EvidenceRef(
                            id=publication.inventory_object_id,
                            sha256=publication.inventory_sha256,
                        ),
                        "search_checkpoint_inventory",
                    )
                )
            )
            document = InitialCheckpointClosure.model_validate_json(
                canonical(
                    read(
                        EvidenceRef(
                            id=publication.closure_object_id,
                            sha256=publication.closure_sha256,
                        ),
                        "search_checkpoint_closure",
                    )
                )
            )
            if (
                document.epoch_id,
                document.frontier,
                document.spec_sha256,
                document.state_sha256,
                document.control_sequence,
            ) != (
                epoch.id,
                intent.frontier,
                epoch.spec_sha256,
                intent.state_sha256,
                control.sequence,
            ):
                raise ValueError("Checkpoint closure identity differs")
            selected = {}
            cut_tables = dict(tables)
            complete = control.id == latest.id and intent.save_ordinal == max(ordinals)
            for key, name in ROW_TABLES.items():
                wanted = set(getattr(document, key))
                existing = {row.id: row for row in rows[key]}
                if not wanted <= existing.keys():
                    raise ValueError("Checkpoint substitutes durable evidence")
                selected[key] = [existing[identity] for identity in wanted]
                cut_tables[name] = [
                    row for row in grouped[name][epoch_id] if UUID(row["id"]) in wanted
                ]
                complete = complete and wanted == existing.keys()
            prefix = {row.id for row in controls if row.sequence <= control.sequence}
            if set(document.control_transition_ids) != prefix:
                raise ValueError("Checkpoint must cover exact control prefix")
            prefix_controls = selected["control_transition_ids"]
            if set(document.intent_ids) != {
                row.sampling_intent_id
                for row in prefix_controls
                if row.kind == "sampling"
            } or set(document.terminal_ids) != {
                row.terminal_id for row in prefix_controls if row.kind != "sampling"
            }:
                raise ValueError("Checkpoint prefix intent/terminal coverage differs")
            terminal = max(selected["terminal_ids"], key=lambda row: row.frontier)
            if intent.terminal_id != terminal.id:
                raise ValueError("Checkpoint terminal is not prefix frontier")
            wanted_outcomes = set(document.evaluation_outcome_ids)
            actual_outcomes = {
                UUID(row["id"]): row for row in outcomes_by_epoch[epoch_id]
            }
            if not wanted_outcomes <= actual_outcomes.keys():
                raise ValueError("Checkpoint substitutes evaluation outcome")
            cut_tables["search_evaluation_outcomes"] = [
                actual_outcomes[identity] for identity in wanted_outcomes
            ]
            complete = complete and wanted_outcomes == actual_outcomes.keys()
            # These immutable dependent rows have no IDs in the historical
            # checkpoint DTO. Include precisely the members owned by selected
            # receipts/candidates, never unrelated rows from later saves.
            cut_tables["search_objective_evaluations"] = [
                row
                for row in tables["search_objective_evaluations"]
                if UUID(row["objective_receipt_id"])
                in set(document.objective_receipt_ids)
            ]
            cut_tables["search_candidate_grants"] = [
                row
                for row in tables["search_candidate_grants"]
                if UUID(row["candidate_id"]) in set(document.candidate_ids)
            ]
            # Validate the selected graph's own relations rather than filling holes
            # with future rows from the full final database.
            _subset_references(cut_tables)
            _validate_control_chain(
                prefix_controls,
                selected["intent_ids"],
                selected["terminal_ids"],
                control,
            )
            if set(document.sampling_transition_ids) != {
                r.id for r in prefix_controls if r.kind == "sampling"
            } or set(document.pending_intent_ids) != _pending_intent_ids(
                selected["intent_ids"],
                selected["worker_outcome_ids"],
                selected["terminal_ids"],
            ):
                raise ValueError("Checkpoint pending or sampling tail differs")
            validate_worker_closure(
                epoch,
                selected,
                set(document.pending_intent_ids),
                set(document.open_worker_assignment_ids),
            )

            def cut_population(
                row, cut_tables=cut_tables, epoch_id=epoch_id, read=read
            ):
                value = read(
                    EvidenceRef(id=row.state_object_id, sha256=row.state_sha256),
                    "search_population",
                )
                return validate_population(
                    value, epoch_id, row.frontier, row.state_sha256, cut_tables
                )

            validate_event_objects(
                scope,
                epoch,
                selected,
                read_evidence=read,
                validated_population=cut_population,
            )
            stored_files = files_by_publication[str(publication.id)]
            by_name = {row["name"]: row for row in stored_files}
            if len(by_name) != len(stored_files) or set(by_name) != {
                entry.name for entry in inventory.files
            }:
                raise ValueError("Checkpoint retained file inventory differs")
            files = {}
            for entry in inventory.files:
                row = by_name[entry.name]
                if any(row[k] != epoch_data[k] for k in SCOPE) or (
                    row["file_sha256"],
                    row["size_bytes"],
                    row["object_id"],
                    row["object_kind"],
                    row["object_sha256"],
                ) != (
                    entry.file_sha256,
                    entry.size_bytes,
                    str(entry.object.id),
                    "search_checkpoint_file",
                    entry.object.sha256,
                ):
                    raise ValueError("Checkpoint file registration differs")
                files[entry.name] = read_object(
                    epoch.owner_id,
                    entry.object.id,
                    "search_checkpoint_file",
                    entry.object.sha256,
                )
            native = validate_checkpoint_evidence(inventory, files, intent.state_sha256)
            if canonical(native) != canonical(cut_population(control)):
                raise ValueError(
                    "Checkpoint files differ from acknowledged cut population"
                )
            accepted_publications.add(str(publication.id))
            if complete:
                published.add(epoch_id)
    if set(files_by_publication) - accepted_publications:
        raise ValueError("Orphan checkpoint file rows")
    return frozenset(published)


def _subset_references(tables):
    selected_names = set(ROW_TABLES.values()) | {
        "search_evaluation_outcomes",
        "search_objective_evaluations",
        "search_candidate_grants",
    }
    for source, local, remote, columns in FOREIGN_KEYS:
        if source not in selected_names or remote not in selected_names:
            continue
        references = {tuple(row[key] for key in columns) for row in tables[remote]}
        for row in tables[source]:
            values = tuple(row[key] for key in local)
            if not any(value is None for value in values) and values not in references:
                raise ValueError("Checkpoint selected graph is not transitively closed")
