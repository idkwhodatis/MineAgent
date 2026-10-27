"""Exact research-v5 protected RD-controller cold closure; never resumption.

Full shared/source closures must independently validate first. This extension
re-reads actual bounded control bytes and cross-closes controller provenance.
Historical root inode is compared across journals, not against restored storage.
No process reactivation, kernel observation, source/native execution or cleanup.
"""

import hashlib
from types import SimpleNamespace
from uuid import UUID

from packages.factors.contracts import canonical_bytes
from packages.rdagent_adapter.operational_bootstrap import RDOperationalBootstrap
from packages.rdagent_adapter.persisted_identity import require_source_identity
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.process_contracts import canonical, digest
from packages.research_runtime.process_persistence_codec import ROW_FIELDS, decode_row
from runtimes.rdagent_operational.process_journal import (
    STORAGE_FLOOR,
    ControllerChild,
    ControllerIdentity,
    ControllerIntent,
    ControllerLaunch,
    ControllerStop,
    controller_admission,
    validate_records,
)

SCHEMA = "mineagent.research-restore-closure.v5"
RD_SCHEMA = "mineagent.rd-source-restore-closure.v2"
SCOPE = ("owner_id", "run_id", "attempt_id", "generation")
NO_CHILD_TABLES = (
    "factor_generation_sources",
    "factor_source_publications",
    "factor_evaluation_starts",
    "factor_evaluation_outcomes",
    "factor_generation_outcomes",
    "factor_upstream_call_outcomes",
    "factor_generation_observations",
    "factor_generation_boundaries",
    "factor_research_checkpoints",
)


def _admission(row):
    if set(row) != set(ROW_FIELDS) | {"created_at"}:
        raise ValueError("Exact process row inventory required")
    values = {k: row[k] for k in ROW_FIELDS}
    for key in (
        "id",
        "owner_id",
        "run_id",
        "attempt_id",
        "logical_operation_id",
        "reservation_id",
    ):
        value = UUID(values[key])
        if str(value) != values[key]:
            raise ValueError("Canonical controller UUID required")
        values[key] = value
    return controller_admission(decode_row(values))


def manifest_keys(row, *, physical=None):
    """Potential control-byte paths; missing optional child creates no file.

    With a pinned inventory, omit absent child. Without it, include its potential
    path so a retention scan protects a child journal written before DB ingestion.
    All other process files (staged inputs, tickets, active sockets) remain closed.
    """
    admission = _admission(row)
    prefix = f"research-processes/{admission.owner_id}/{admission.id}/control/"
    names = ("launch", "identity", "child", "stop")
    return {
        prefix + name + ".json"
        for name in names
        if name != "child" or physical is None or prefix + "child.json" in physical
    }


def validate_controller_closure_unit(
    row,
    indexes,
    rd_source_closure,
    physical,
    check,
    read_bytes,
    allowed,
    reservation_ids,
    *,
    research_schema,
    seed_context=None,
):
    if (research_schema, rd_source_closure.get("schema_version")) not in {
        (SCHEMA, RD_SCHEMA),
        (
            "mineagent.research-restore-closure.v10",
            "mineagent.rd-source-restore-closure.v5",
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
            "mineagent.research-restore-closure.v7",
            "mineagent.rd-source-restore-closure.v4",
        ),
        (
            "mineagent.research-restore-closure.v6",
            "mineagent.rd-source-restore-closure.v3",
        ),
    }:
        raise ValueError("Explicit research-v5 and RD source closure required")
    from collections.abc import Mapping

    if research_schema == "mineagent.research-restore-closure.v10":
        if not isinstance(seed_context, Mapping):
            raise ValueError(
                "Seed context required before protected controller closure"
            )
    elif seed_context is not None:
        raise ValueError("Historical controller cannot acquire seed authority")
    admission = _admission(row)
    scope = lambda other: (
        type(other.get("generation")) is int
        and all(other.get(k) == row[k] for k in SCOPE)
    )
    state = indexes["research_process_states"].get(row["id"])
    if state is None or state["status"] != "stopped" or not scope(state):
        raise ValueError("Controller is not positively stopped")
    reservation = indexes["research_budget_reservations"].get(row["reservation_id"])
    if (
        reservation is None
        or not scope(reservation)
        or reservation["purpose"] != "process_unit"
        or reservation["id"] in reservation_ids
    ):
        raise ValueError("Controller reservation scope/reuse differs")
    amounts = BudgetVector.model_validate(reservation["amounts"])
    if (
        amounts.process_slots != 1
        or amounts.wall_milliseconds < 60000
        or amounts.storage_bytes < STORAGE_FLOOR
        or any(
            getattr(amounts, k) != 0
            for k in ("calls", "tokens", "mutation_slots", "evaluator_executions")
        )
    ):
        raise ValueError("Controller physical reservation floor differs")
    settlements = [
        s
        for s in indexes["research_budget_settlements"].values()
        if s["reservation_id"] == reservation["id"]
    ]
    if reservation["state"] == "settled":
        if (
            len(settlements) != 1
            or not scope(settlements[0])
            or settlements[0]["outcome"] != "conservative_unknown"
            or BudgetVector.model_validate(settlements[0]["charge"]) != amounts
        ):
            raise ValueError("Controller requires full conservative stopped charge")
    elif reservation["state"] not in {"reserved", "uncertain"} or settlements:
        raise ValueError("Unresolved controller reservation must remain held")
    receipts = [
        r
        for r in indexes["research_process_receipts"].values()
        if r["admission_id"] == row["id"]
    ]
    by_kind = {r["kind"]: r for r in receipts}
    if len(by_kind) != len(receipts) or set(by_kind) not in (
        {"launch_intent", "launch_identity", "stop"},
        {"launch_intent", "launch_identity", "controller_child", "stop"},
    ):
        raise ValueError("Exact controller receipt inventory required")
    if any(
        not scope(r) or r["payload_sha256"] != digest(r["payload"]) for r in receipts
    ):
        raise ValueError("Controller receipt scope/hash differs")
    intent = ControllerIntent.model_validate_json(
        canonical(by_kind["launch_intent"]["payload"])
    )
    identity = ControllerIdentity.model_validate_json(
        canonical(by_kind["launch_identity"]["payload"])
    )
    child = (
        ControllerChild.model_validate_json(
            canonical(by_kind["controller_child"]["payload"])
        )
        if "controller_child" in by_kind
        else None
    )
    stop = ControllerStop.model_validate_json(canonical(by_kind["stop"]["payload"]))
    launch = ControllerLaunch(
        schema_version="mineagent.rd-controller-launch.v1",
        intent=intent,
        runtime_name="mineagent-rd-controller-" + str(admission.id),
        root_device=identity.root_device,
        root_inode=identity.root_inode,
    )
    if intent.admission != admission:
        raise ValueError("Controller intent admission differs")
    launch, identity, child, stop = validate_records(launch, identity, child, stop)
    tables = rd_source_closure["tables"]

    def one(rows, predicate):
        found = [r for r in rows if predicate(r)]
        if len(found) != 1:
            raise ValueError("Exact controller domain predecessor required")
        return found[0]

    run = one(
        rd_source_closure["external_bindings"]["runs"],
        lambda r: r["id"] == row["run_id"] and r["owner_id"] == row["owner_id"],
    )
    run_obj = SimpleNamespace(**run)
    run_obj.dataset_version_id = UUID(run["dataset_version_id"])
    run_obj.owner_id = UUID(run["owner_id"])
    if (
        research_schema != "mineagent.research-restore-closure.v10"
        and run["config"].get("schema_version") != "mineagent.factor-research.v1"
    ):
        raise ValueError("Historical controller config grammar differs")
    config = require_source_identity(run_obj)
    spec = one(
        tables["factor_research_specs"],
        lambda r: r["run_id"] == row["run_id"] and r["owner_id"] == row["owner_id"],
    )
    binding = one(
        tables["factor_research_attempt_bindings"],
        lambda r: r["id"] == row["logical_operation_id"] and scope(r),
    )
    controller = one(
        tables["factor_controller_launches"],
        lambda r: r["admission_id"] == row["id"] and scope(r),
    )
    if (
        spec["config"] != config.model_dump(mode="json")
        or spec["config_sha256"] != config.sha256
        or spec["input_sha256"] != config.input.sha256
        or binding["spec_id"] != spec["id"]
        or binding["resumed_checkpoint_id"] is not None
        or controller["attempt_binding_id"] != binding["id"]
    ):
        raise ValueError("Controller immutable domain binding differs")
    bootstrap_values = {
        "owner_id": admission.owner_id,
        "run_id": admission.run_id,
        "attempt_id": admission.attempt_id,
        "generation": admission.generation,
        "spec_id": UUID(spec["id"]),
        "attempt_binding_id": UUID(binding["id"]),
        "config": config,
        "config_sha256": config.sha256,
        "input_sha256": config.input.sha256,
    }
    if research_schema == "mineagent.research-restore-closure.v10":
        from backend.app.rdagent_seed_backup import build_expected_bootstrap

        bootstrap = build_expected_bootstrap(
            seed_context=seed_context, **bootstrap_values
        )
    else:
        bootstrap = RDOperationalBootstrap(**bootstrap_values)
    raw = canonical_bytes(bootstrap)
    if (
        controller["bootstrap"] != bootstrap.model_dump(mode="json")
        or controller["bootstrap_sha256"] != bootstrap.sha256
        or admission.image_id != config.runtime.orchestrator_image
        or intent.bootstrap_sha256 != bootstrap.sha256
        or intent.bootstrap_size_bytes != len(raw)
    ):
        raise ValueError("Controller bootstrap/image differs")
    if child is None and (
        run["status"] not in {"failed", "cancelled"}
        or any(scope(r) for name in NO_CHILD_TABLES for r in tables[name])
    ):
        raise ValueError(
            "Childless stop is failure only, never source/evaluation/controller evidence"
        )
    prefix = f"research-processes/{row['owner_id']}/{row['id']}/control/"
    records = {"launch": launch, "identity": identity, "stop": stop}
    if child is not None:
        records["child"] = child
    keys = {prefix + name + ".json" for name in records}
    if any(
        key.startswith(prefix.removesuffix("control/")) and key not in keys
        for key in physical
    ):
        raise ValueError("Unknown controller file or active IPC/staging remains")
    for name, record in records.items():
        key = prefix + name + ".json"
        expected = canonical(record.model_dump(mode="json"))
        sha = hashlib.sha256(expected).hexdigest()
        if key not in physical or not 0 < len(expected) <= 16384:
            raise ValueError("Bounded controller journal absent")
        check(key, sha, len(expected))
        observed = read_bytes(key, sha, 16384)
        if (
            type(observed) is not bytes
            or observed != expected
            or hashlib.sha256(observed).hexdigest() != sha
        ):
            raise ValueError("Actual protected controller bytes differ")
    reservation_ids.add(reservation["id"])
    allowed.update(keys)
    return True
