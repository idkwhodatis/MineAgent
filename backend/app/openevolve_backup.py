"""024 cold composition over registered bytes; never executable restoration.

Not activated by schema capabilities until the complete 024 migration and required
actual process/PG restore gate are present. Components alone are not acceptance.
"""

import hashlib
from uuid import UUID

from backend.app.openevolve_backup_evaluations import validate_evaluations
from backend.app.openevolve_backup_graph import validate_graph
from backend.app.openevolve_backup_objectives import validate_objectives
from backend.app.openevolve_backup_shape import validate_references
from backend.app.openevolve_backup_spec import validate_specs
from backend.app.openevolve_backup_upload import validate_uploads
from packages.research_runtime.budget_contracts import BudgetVector

SCOPE = ("owner_id", "run_id", "attempt_id", "generation")


def registered_reader(research, read_bytes):
    if not callable(read_bytes):
        raise TypeError("Actual bounded registered object bytes required")
    objects = {r["id"]: r for r in research["tables"]["research_objects"]}
    if len(objects) != len(research["tables"]["research_objects"]):
        raise ValueError("Duplicate registered object")

    def read(owner, identity, kind, sha):
        owner, identity = str(owner), str(identity)
        if str(UUID(owner)) != owner or str(UUID(identity)) != identity:
            raise ValueError("Canonical registered identity required")
        row = objects.get(identity)
        if (
            row is None
            or row["owner_id"] != owner
            or row["object_kind"] != kind
            or row["sha256"] != sha
            or type(row["size_bytes"]) is not int
            or not 0 < row["size_bytes"] <= 16 * 1024 * 1024
        ):
            raise ValueError("Object differs from registered immutable identity")
        raw = read_bytes(row["storage_key"], sha, row["size_bytes"])
        if (
            type(raw) is not bytes
            or len(raw) != row["size_bytes"]
            or hashlib.sha256(raw).hexdigest() != sha
        ):
            raise ValueError("Actual retained object bytes differ")
        return raw

    return read


def validate_terminal_closure(closure, research, completed):
    """No success from a historical cut, pending upload or unresolved hold."""
    tables = closure["tables"]
    runs = {r["id"]: r for r in closure["external_bindings"]["runs"]}
    reservations = {
        r["id"]: r for r in research["tables"]["research_budget_reservations"]
    }
    settlements = {}
    for row in research["tables"]["research_budget_settlements"]:
        settlements.setdefault(row["reservation_id"], []).append(row)
    for intent in tables["search_iteration_intents"]:
        held = reservations[intent["mutation_reservation_id"]]
        if (
            any(held[k] != intent[k] for k in SCOPE)
            or held["purpose"] != "engine_mutation"
            or held["logical_key"]
            != f"oe-mutation:{intent['epoch_id']}:{intent['native_iteration']}"
            or held["amounts"] != BudgetVector(mutation_slots=1).model_dump(mode="json")
        ):
            raise ValueError("Mutation reservation exact consumer differs")
        if held["state"] == "settled":
            values = settlements.get(held["id"], [])
            if (
                len(values) != 1
                or values[0]["charge"] != held["amounts"]
                or values[0]["outcome"] != "conservative_unknown"
            ):
                raise ValueError("Mutation cannot refund retained computation")
    for epoch in tables["strategy_search_epochs"]:
        run = runs[epoch["run_id"]]
        if run["status"] != "succeeded":
            continue
        if epoch["id"] not in completed:
            raise ValueError("Succeeded search lacks complete highest actual save")
        for table, key, target_table, target_key in (
            ("search_evaluation_preparations", "id", "search_evaluations", "id"),
            ("search_evaluations", "id", "search_evaluation_outcomes", "evaluation_id"),
            (
                "search_checkpoint_uploads",
                "id",
                "search_checkpoint_upload_completions",
                "upload_id",
            ),
            (
                "search_checkpoint_intents",
                "id",
                "search_checkpoint_publications",
                "intent_id",
            ),
        ):
            required = {r[key] for r in tables[table] if r["epoch_id"] == epoch["id"]}
            # Outcomes derive epoch through their evaluation FK, not a fictitious
            # outcome.epoch_id column. Shape already forbids orphan consumers.
            actual = {r[target_key] for r in tables[target_table]}
            if not required <= actual:
                raise ValueError(
                    "Succeeded search retains incomplete physical/upload obligation"
                )
        if any(
            r["run_id"] == run["id"] and r["state"] != "settled"
            for r in reservations.values()
        ):
            raise ValueError("Succeeded search retains unresolved accounting")
        if any(
            r["run_id"] == run["id"] and r["state"] != "completed"
            for r in research["tables"]["research_model_operations"]
        ):
            raise ValueError("Succeeded search retains unresolved model operation")


def validate_closure(closure, research, process_records, read_bytes, maximum_rows):
    validate_references(closure, research, maximum_rows=maximum_rows)
    specs = validate_specs(closure, research)
    read_object = registered_reader(research, read_bytes)
    # Launch/profile validation is a distinct typed original-byte commitment,
    # not inferred from the stopped process profile string or SQL shape alone.
    from backend.app.openevolve_backup_launch import validate_launches

    validate_launches(closure, research, specs, process_records)
    validate_uploads(closure["tables"], research, read_object)
    observations = validate_evaluations(
        closure, research, specs, process_records, read_object, read_bytes
    )
    validate_objectives(closure["tables"], specs, observations, read_object)
    complete = validate_graph(closure, research, read_object)
    runs = {r["id"]: r for r in closure["external_bindings"]["runs"]}
    for epoch in closure["tables"]["strategy_search_epochs"]:
        if (
            runs[epoch["run_id"]]["status"] == "succeeded"
            and process_records[epoch["admission_id"]].get("child") is None
        ):
            raise ValueError(
                "Successful search requires actual protected controller child"
            )
    validate_terminal_closure(closure, research, complete)
    return complete
