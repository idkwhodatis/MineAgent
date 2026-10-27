"""Declared cold graph/real canonical bytes; not PG/native execution acceptance."""

import copy
import hashlib
from uuid import UUID, uuid4

import pytest

from backend.app.openevolve_backup_graph import ROW_TABLES, validate_graph
from packages.openevolve_adapter.checkpoint_native import canonical, state_sha256
from packages.openevolve_adapter.host_contracts import (
    InitialCheckpointClosure,
    TerminalCommand,
)
from packages.research_runtime.broker_contracts import digest
from tests.openevolve.test_backup_population import case
from tests.openevolve.test_checkpoint_evidence import wrap


def fixture():
    state, eid, population = case()
    scope = {k: str(uuid4()) for k in ("owner_id", "run_id", "attempt_id")}
    scope["generation"] = 1
    epoch = {**scope, "id": eid, "admission_id": str(uuid4()), "spec_sha256": "a" * 64}
    tables = {name: [] for name in ROW_TABLES.values()}
    tables.update(population)
    for name, values in list(tables.items()):
        tables[name] = [{**scope, "id": str(uuid4()), **row} for row in values]
    tables.update(
        strategy_search_epochs=[epoch],
        search_checkpoint_intents=[],
        search_checkpoint_publications=[],
        search_checkpoint_files=[],
        search_evaluation_outcomes=[],
        search_objective_evaluations=[],
    )
    objects = {}

    def put(kind, doc):
        raw = canonical(doc)
        oid, sha = uuid4(), hashlib.sha256(raw).hexdigest()
        objects[(UUID(scope["owner_id"]), oid, kind, sha)] = raw
        return str(oid), sha

    state_id, sha = put("search_population", state)
    terminal_id, control_id = str(uuid4()), str(uuid4())
    ack = tables["search_objective_acknowledgements"][0]
    terminal = {
        **scope,
        "id": terminal_id,
        "epoch_id": eid,
        "frontier": 0,
        "outcome": "seed",
        "state_object_id": state_id,
        "state_sha256": sha,
        "worker_outcome_id": None,
        "seed_objective_ack_id": ack["id"],
    }
    command = TerminalCommand(
        epoch_id=UUID(eid),
        frontier=0,
        outcome="seed",
        state={"id": UUID(state_id), "sha256": sha},
        seed_objective_ack_id=UUID(ack["id"]),
    )
    control = {
        **scope,
        "id": control_id,
        "epoch_id": eid,
        "sequence": 1,
        "previous_id": None,
        "previous_sequence": None,
        "kind": "seed",
        "frontier": 0,
        "sampling_intent_id": None,
        "terminal_id": terminal_id,
        "state_object_id": state_id,
        "state_sha256": sha,
        "request_sha256": digest(command),
    }
    event_id, event_sha = put(
        "search_controller_event",
        {
            "schema_version": "mineagent.openevolve-event.v1",
            "admission_id": epoch["admission_id"],
            "epoch_id": eid,
            "kind": "terminal_applied",
            "payload": {
                "iteration": 0,
                "outcome": "seed",
                "native_state": state,
                "state_sha256": sha,
            },
        },
    )
    event = {
        **scope,
        "id": str(uuid4()),
        "epoch_id": eid,
        "admission_id": epoch["admission_id"],
        "control_id": control_id,
        "control_sequence": 1,
        "control_frontier": 0,
        "control_state_sha256": sha,
        "command_sha256": digest(command),
        "intent_id": None,
        "terminal_id": terminal_id,
        "event_kind": "terminal_applied",
        "native_iteration": 0,
        "event_object_id": event_id,
        "event_sha256": event_sha,
    }
    tables["search_control_transitions"] = [control]
    tables["search_terminal_applications"] = [terminal]
    tables["search_controller_event_receipts"] = [event]
    evaluation = {
        **scope,
        "id": str(uuid4()),
        "epoch_id": eid,
        "process_admission_id": str(uuid4()),
    }
    tables["search_evaluations"] = [evaluation]
    tables["search_evaluation_outcomes"] = [
        {
            **scope,
            "id": str(uuid4()),
            "evaluation_id": evaluation["id"],
            "admission_id": evaluation["process_admission_id"],
        }
    ]
    intent = {
        **scope,
        "id": str(uuid4()),
        "epoch_id": eid,
        "control_transition_id": control_id,
        "save_ordinal": 1,
        "terminal_id": terminal_id,
        "frontier": 0,
        "state_sha256": sha,
    }
    tables["search_checkpoint_intents"] = [intent]
    document = InitialCheckpointClosure.model_validate_json(
        canonical(
            dict(
                epoch_id=eid,
                frontier=0,
                spec_sha256=epoch["spec_sha256"],
                state_sha256=sha,
                control_sequence=1,
                **{
                    key: tuple(sorted(row["id"] for row in tables[name]))
                    for key, name in ROW_TABLES.items()
                },
                sampling_transition_ids=(),
                open_worker_assignment_ids=(),
                pending_intent_ids=(),
                evaluation_outcome_ids=tuple(
                    row["id"] for row in tables["search_evaluation_outcomes"]
                ),
            )
        )
    )
    closure_id, closure_sha = put(
        "search_checkpoint_closure", document.model_dump(mode="json")
    )
    program = next(iter(state["programs"].values()))
    native = {
        "metadata.json": canonical(state["metadata"]),
        f"programs/{program['id']}.json": canonical(program),
        "best_program.py": program["code"].encode(),
        "best_program_info.json": canonical(
            {
                "id": program["id"],
                "generation": program["generation"],
                "iteration": program["iteration_found"],
                "current_iteration": 0,
                "metrics": program["metrics"],
                "language": program["language"],
                "timestamp": program["timestamp"],
                "saved_at": 2.0,
            }
        ),
    }
    inventory, files = wrap(native)
    inv_id, inv_sha = put(
        "search_checkpoint_inventory", inventory.model_dump(mode="json")
    )
    publication = {
        **scope,
        "id": str(uuid4()),
        "epoch_id": eid,
        "intent_id": intent["id"],
        "frontier": 0,
        "state_sha256": state_sha256(state),
        "inventory_object_id": inv_id,
        "inventory_sha256": inv_sha,
        "closure_object_id": closure_id,
        "closure_sha256": closure_sha,
    }
    tables["search_checkpoint_publications"] = [publication]
    for entry in inventory.files:
        objects[
            (
                UUID(scope["owner_id"]),
                entry.object.id,
                "search_checkpoint_file",
                entry.object.sha256,
            )
        ] = files[entry.name]
        tables["search_checkpoint_files"].append(
            {
                **scope,
                "id": str(uuid4()),
                "publication_id": publication["id"],
                "name": entry.name,
                "file_sha256": entry.file_sha256,
                "size_bytes": entry.size_bytes,
                "object_id": str(entry.object.id),
                "object_kind": "search_checkpoint_file",
                "object_sha256": entry.object.sha256,
            }
        )
    # Unused fields in this graph-only projection are NULL; full shape/spec
    # validators are independently required and tested with complete DB rows.
    from backend.app.openevolve_backup_inventory import FOREIGN_KEYS

    for name, local, remote, columns in FOREIGN_KEYS:
        if name in tables and remote in tables:
            for row in tables[name]:
                for key in local:
                    row.setdefault(key, None)
            for row in tables[remote]:
                for key in columns:
                    row.setdefault(key, None)
    research = {
        "external_bindings": {
            "runs": [{"id": scope["run_id"], "owner_id": scope["owner_id"]}]
        }
    }
    return {"tables": tables}, research, objects, eid


def test_full_registered_checkpoint_graph_uses_actual_envelopes():
    closure, research, objects, eid = fixture()
    reads = []

    def read(*key):
        reads.append(key)
        return objects[key]

    assert validate_graph(closure, research, read) == frozenset({eid})
    assert {k[2] for k in reads} == {
        "search_checkpoint_inventory",
        "search_checkpoint_closure",
        "search_checkpoint_file",
        "search_population",
        "search_controller_event",
    }


@pytest.mark.parametrize(
    "fault",
    [
        "scope",
        "duplicate",
        "missing_event",
        "wrong_control",
        "duplicate_publication",
        "file_missing",
        "file_hash",
        "file_scope",
        "outcome_missing",
        "extra_row",
        "bytes",
        "pending_tail",
    ],
)
def test_checkpoint_rejects_substitution_and_incomplete_graph(fault):
    closure, research, objects, _ = fixture()
    tables = closure["tables"]
    if fault == "scope":
        tables["search_candidates"][0]["owner_id"] = str(uuid4())
    elif fault == "duplicate":
        tables["search_candidates"].append(
            copy.deepcopy(tables["search_candidates"][0])
        )
    elif fault == "missing_event":
        tables["search_controller_event_receipts"].clear()
    elif fault == "wrong_control":
        tables["search_checkpoint_intents"][0]["control_transition_id"] = str(uuid4())
    elif fault == "duplicate_publication":
        tables["search_checkpoint_publications"].append(
            {**tables["search_checkpoint_publications"][0], "id": str(uuid4())}
        )
    elif fault == "file_missing":
        tables["search_checkpoint_files"].pop()
    elif fault == "file_hash":
        tables["search_checkpoint_files"][0]["object_sha256"] = "0" * 64
    elif fault == "file_scope":
        tables["search_checkpoint_files"][0]["owner_id"] = str(uuid4())
    elif fault == "outcome_missing":
        tables["search_evaluation_outcomes"].clear()
    elif fault == "extra_row":
        tables["search_evaluations"].append(
            {**tables["search_evaluations"][0], "id": str(uuid4())}
        )
    elif fault == "bytes":
        key = next(k for k in objects if k[2] == "search_checkpoint_file")
        objects[key] += b" "
    else:
        key = next(k for k in objects if k[2] == "search_checkpoint_closure")
        import json

        doc = json.loads(objects[key])
        doc["pending_intent_ids"] = [str(uuid4())]
        raw = canonical(doc)
        sha = hashlib.sha256(raw).hexdigest()
        objects[(*key[:3], sha)] = raw
        tables["search_checkpoint_publications"][0]["closure_sha256"] = sha
    if fault == "extra_row":
        # Explicit older cut stays historical; it cannot qualify as full final.
        assert (
            validate_graph(closure, research, lambda *key: objects[key]) == frozenset()
        )
    else:
        with pytest.raises((ValueError, KeyError)):
            validate_graph(closure, research, lambda *key: objects[key])


def test_failed_partial_epoch_without_publication_is_not_completed_checkpoint():
    closure, research, objects, _ = fixture()
    closure["tables"]["search_checkpoint_publications"].clear()
    closure["tables"]["search_checkpoint_files"].clear()
    assert validate_graph(closure, research, lambda *key: objects[key]) == frozenset()


@pytest.mark.parametrize("retain_seed_cut", [False, True])
def test_interrupted_sampling_before_placement_keeps_no_checkpoint_authority(
    retain_seed_cut,
):
    from packages.openevolve_adapter.host_contracts import SamplingIntentCommand

    closure, research, objects, eid = fixture()
    tables = closure["tables"]
    if not retain_seed_cut:
        tables["search_checkpoint_publications"].clear()
        tables["search_checkpoint_files"].clear()
    epoch = tables["strategy_search_epochs"][0]
    scope = {
        key: epoch[key] for key in ("owner_id", "run_id", "attempt_id", "generation")
    }
    prior = tables["search_control_transitions"][0]
    candidate = tables["search_candidates"][0]
    state_key = next(key for key in objects if key[2] == "search_population")
    import json

    state = json.loads(objects[state_key])
    snapshot = {
        "programs": state["programs"],
        "islands": state["metadata"]["islands"],
        "current_island": 0,
        "sampling_island": 0,
        "feature_dimensions": ["complexity", "diversity"],
        "artifacts": {},
    }

    def put(kind, value):
        raw = canonical(value)
        key = (UUID(scope["owner_id"]), uuid4(), kind, hashlib.sha256(raw).hexdigest())
        objects[key] = raw
        return str(key[1]), key[3]

    selection_id, selection_sha = put(
        "search_context",
        {
            "schema_version": "mineagent.native-selection.v1",
            "native_iteration": 1,
            "parent_id": candidate["upstream_program_id"],
            "inspiration_ids": [],
            "snapshot": snapshot,
        },
    )
    intent = {
        **scope,
        "id": str(uuid4()),
        "epoch_id": eid,
        "native_iteration": 1,
        "parent_candidate_id": candidate["id"],
        "parent_source_sha256": candidate["source_sha256"],
        "mutation_reservation_id": str(uuid4()),
        "selection_object_id": selection_id,
        "selection_sha256": selection_sha,
    }
    tables["search_iteration_intents"].append(intent)
    command = SamplingIntentCommand.model_validate_json(
        canonical(
            {
                "intent": {
                    "epoch_id": eid,
                    "native_iteration": 1,
                    "parent_candidate_id": candidate["id"],
                    "parent_source_sha256": candidate["source_sha256"],
                    "mutation_reservation_id": intent["mutation_reservation_id"],
                    "selection": {"id": selection_id, "sha256": selection_sha},
                },
                "before_state_sha256": prior["state_sha256"],
                "after_state": {
                    "id": prior["state_object_id"],
                    "sha256": prior["state_sha256"],
                },
            }
        )
    )
    control = {
        **prior,
        "id": str(uuid4()),
        "sequence": 2,
        "previous_id": prior["id"],
        "previous_sequence": 1,
        "kind": "sampling",
        "sampling_intent_id": intent["id"],
        "terminal_id": None,
        "request_sha256": digest(command),
    }
    tables["search_control_transitions"].append(control)
    event_id, event_sha = put(
        "search_controller_event",
        {
            "schema_version": "mineagent.openevolve-event.v1",
            "epoch_id": eid,
            "admission_id": epoch["admission_id"],
            "kind": "iteration_intent",
            "payload": {
                "iteration": 1,
                "parent_id": candidate["upstream_program_id"],
                "inspiration_ids": [],
                "snapshot": snapshot,
                "sampling": {
                    "schema_version": "mineagent.native-sampling-transition.v1",
                    "before_state_sha256": prior["state_sha256"],
                    "after_state": state,
                },
            },
        },
    )
    tables["search_controller_event_receipts"].append(
        {
            **tables["search_controller_event_receipts"][0],
            "id": str(uuid4()),
            "control_id": control["id"],
            "control_sequence": 2,
            "command_sha256": digest(command),
            "intent_id": intent["id"],
            "terminal_id": None,
            "event_kind": "iteration_intent",
            "native_iteration": 1,
            "event_object_id": event_id,
            "event_sha256": event_sha,
        }
    )
    assert validate_graph(closure, research, lambda *key: objects[key]) == frozenset()
    # The same tail may not be hidden behind the earlier final publication.
    tables["search_controller_event_receipts"][-1]["command_sha256"] = "0" * 64
    from backend.app.services import DomainError

    with pytest.raises((DomainError, ValueError)):
        validate_graph(closure, research, lambda *key: objects[key])


@pytest.mark.parametrize(
    "fault", [None, "duplicate_ordinal", "gap", "missing", "pending_final"]
)
def test_same_cursor_separate_save_ordinals_keep_exact_final_membership(fault):
    closure, research, objects, eid = fixture()
    tables = closure["tables"]
    first = tables["search_checkpoint_publications"][0]
    intent = {
        **tables["search_checkpoint_intents"][0],
        "id": str(uuid4()),
        "save_ordinal": 2,
    }
    tables["search_checkpoint_intents"].append(intent)
    second = {**first, "id": str(uuid4()), "intent_id": intent["id"]}
    if fault != "pending_final":
        tables["search_checkpoint_publications"].append(second)
        for row in list(tables["search_checkpoint_files"]):
            tables["search_checkpoint_files"].append(
                {**row, "id": str(uuid4()), "publication_id": second["id"]}
            )
    if fault == "duplicate_ordinal":
        intent["save_ordinal"] = 1
    elif fault == "gap":
        intent["save_ordinal"] = 3
    elif fault == "missing":
        intent.pop("save_ordinal")
    if fault in {"duplicate_ordinal", "gap", "missing"}:
        with pytest.raises(ValueError, match="save ordinals"):
            validate_graph(closure, research, lambda *key: objects[key])
    else:
        expected = frozenset() if fault == "pending_final" else frozenset({eid})
        assert validate_graph(closure, research, lambda *key: objects[key]) == expected


def test_cut_cannot_include_future_outcome_without_its_evaluation():
    import json

    closure, research, objects, _ = fixture()
    tables = closure["tables"]
    evaluation = {**tables["search_evaluations"][0], "id": str(uuid4())}
    outcome = {
        **tables["search_evaluation_outcomes"][0],
        "id": str(uuid4()),
        "evaluation_id": evaluation["id"],
    }
    tables["search_evaluations"].append(evaluation)
    tables["search_evaluation_outcomes"].append(outcome)
    key = next(key for key in objects if key[2] == "search_checkpoint_closure")
    doc = json.loads(objects[key])
    doc["evaluation_outcome_ids"] = sorted(
        doc["evaluation_outcome_ids"] + [outcome["id"]]
    )
    raw = canonical(doc)
    sha = hashlib.sha256(raw).hexdigest()
    objects[(*key[:3], sha)] = raw
    tables["search_checkpoint_publications"][0]["closure_sha256"] = sha
    with pytest.raises(ValueError, match="transitively closed"):
        validate_graph(closure, research, lambda *key: objects[key])


def test_same_cursor_final_save_preserves_distinct_native_saved_at_bytes():
    import json

    from packages.openevolve_adapter.host_contracts import CheckpointFileEnvelope

    closure, research, objects, eid = fixture()
    tables = closure["tables"]
    first = tables["search_checkpoint_publications"][0]
    intent = {
        **tables["search_checkpoint_intents"][0],
        "id": str(uuid4()),
        "save_ordinal": 2,
    }
    tables["search_checkpoint_intents"].append(intent)
    original_files = list(tables["search_checkpoint_files"])
    owner = UUID(first["owner_id"])
    native = {}
    for row in original_files:
        raw = objects[
            (
                owner,
                UUID(row["object_id"]),
                "search_checkpoint_file",
                row["object_sha256"],
            )
        ]
        native[row["name"]] = CheckpointFileEnvelope.model_validate_json(raw).bytes()
    info = json.loads(native["best_program_info.json"])
    info["saved_at"] = 3.0
    native["best_program_info.json"] = canonical(info)
    inventory, files = wrap(native)
    raw = canonical(inventory.model_dump(mode="json"))
    inventory_id, inventory_sha = uuid4(), hashlib.sha256(raw).hexdigest()
    objects[(owner, inventory_id, "search_checkpoint_inventory", inventory_sha)] = raw
    second = {
        **first,
        "id": str(uuid4()),
        "intent_id": intent["id"],
        "inventory_object_id": str(inventory_id),
        "inventory_sha256": inventory_sha,
    }
    tables["search_checkpoint_publications"].append(second)
    for entry in inventory.files:
        objects[
            (owner, entry.object.id, "search_checkpoint_file", entry.object.sha256)
        ] = files[entry.name]
        tables["search_checkpoint_files"].append(
            {
                **original_files[0],
                "id": str(uuid4()),
                "publication_id": second["id"],
                "name": entry.name,
                "file_sha256": entry.file_sha256,
                "size_bytes": entry.size_bytes,
                "object_id": str(entry.object.id),
                "object_sha256": entry.object.sha256,
            }
        )
    assert first["inventory_sha256"] != second["inventory_sha256"]
    assert first["state_sha256"] == second["state_sha256"]
    assert validate_graph(closure, research, lambda *key: objects[key]) == frozenset(
        {eid}
    )
