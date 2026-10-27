"""Cold native input/outcome closure using retained positive-stop process records.

This validates native self-observations, not independently authentic financial
returns. process_records must come from validate_process_unit on actual bytes.
"""

import hashlib
from uuid import UUID

from backend.app.openevolve_native_evidence import decode_native_files
from packages.openevolve_adapter.checkpoint_native import canonical
from packages.openevolve_adapter.execution_binding import (
    NativeEvaluationBinding,
    bound_native_observation,
)
from packages.research_runtime.broker_contracts import digest
from packages.tradegrad_adapter.results import canonical_json_bytes, sha256_bytes
from runtimes.openevolve_evaluator.contracts import NativeInputManifest
from runtimes.openevolve_evaluator.protected_output import MAX_FRAME, validate_export

SCOPE = ("owner_id", "run_id", "attempt_id", "generation")


def validate_evaluations(
    closure, research, specs, process_records, read_object, read_bytes
):
    tables = closure["tables"]
    preparations = {r["id"]: r for r in tables["search_evaluation_preparations"]}
    inputs = {r["evaluation_id"]: r for r in tables["search_evaluation_inputs"]}
    outcomes = {r["evaluation_id"]: r for r in tables["search_evaluation_outcomes"]}
    reservations = {
        r["id"]: r for r in research["tables"]["research_budget_reservations"]
    }
    candidates = {r["id"]: r for r in tables["search_candidates"]}
    receipts = {r["id"]: r for r in research["tables"]["research_process_receipts"]}
    observations = {}
    for preparation in preparations.values():
        budget = reservations[preparation["reservation_id"]]
        if (
            budget["purpose"] != "process_unit"
            or budget["logical_key"] != "oe-native:" + preparation["id"] + ":1"
            or budget["amounts"]
            != {
                "calls": 0,
                "cost_microunits": 0,
                "tokens": 0,
                "wall_milliseconds": 90000,
                "storage_bytes": 268500992,
                "mutation_slots": 0,
                "evaluator_executions": 1,
                "process_slots": 1,
            }
            or any(budget[k] != preparation[k] for k in SCOPE)
        ):
            raise ValueError("Native preparation exact reserved capacity differs")
    for evaluation in tables["search_evaluations"]:
        identity = evaluation["id"]
        preparation, retained = preparations.get(identity), inputs.get(identity)
        records = process_records.get(evaluation["process_admission_id"])
        candidate = candidates.get(evaluation["candidate_id"])
        if (
            preparation is None
            or retained is None
            or records is None
            or candidate is None
        ):
            raise ValueError(
                "Native evaluation lacks prepared input and stopped physical unit"
            )
        for key in (
            *SCOPE,
            "epoch_id",
            "candidate_id",
            "source_sha256",
            "window_id",
            "execution_sha256",
            "logical_ticket_id",
            "physical_ordinal",
            "reservation_id",
        ):
            if evaluation[key] != preparation[key]:
                raise ValueError("Native preparation/evaluation identity differs")
        if (
            evaluation["physical_ordinal"] != 1
            or preparation["admission_id"] != evaluation["process_admission_id"]
            or evaluation["phase"] != candidate["kind"]
            or evaluation["source_sha256"] != candidate["source_sha256"]
            or evaluation["original_strategy_id"] is not None
            or evaluation["prepared_strategy_id"] is not None
            or any(retained[k] != evaluation[k] for k in (*SCOPE, "epoch_id"))
        ):
            raise ValueError("Unsupported native evaluation phase/binding")
        spec = specs[evaluation["run_id"]]
        window = next(
            (w for w in spec.execution.windows if w.id == evaluation["window_id"]), None
        )
        if window is None or evaluation["execution_sha256"] != digest(spec.execution):
            raise ValueError("Native evaluation window/plan differs")
        raw = read_object(
            evaluation["owner_id"],
            retained["manifest_object_id"],
            "search_evaluation_input",
            retained["manifest_sha256"],
        )
        manifest = NativeInputManifest.model_validate_json(raw)
        intent = records["launch"].intent
        if (
            len(raw) != retained["manifest_size_bytes"]
            or canonical(manifest.model_dump(mode="json")) != raw
            or intent.inputs.manifest_object_id != UUID(retained["manifest_object_id"])
            or manifest.evaluation_id != UUID(identity)
            or manifest.native_artifacts_sha256 != evaluation["input_manifest_sha256"]
            or manifest.execution_plan_sha256 != evaluation["execution_sha256"]
            or manifest.source_sha256 != evaluation["source_sha256"]
            or (manifest.window_start, manifest.window_end)
            != (window.start, window.end)
            or intent.admission.image_id != spec.execution.evaluator_image
        ):
            raise ValueError("Native retained input dual-hash identity differs")
        intent.inputs.require_manifest(manifest)
        outcome = outcomes.get(identity)
        if outcome is None:
            continue  # stopped failure before outcome publication retains its full charge
        stop = receipts.get(outcome["stop_receipt_id"])
        if (
            stop is None
            or stop["kind"] != "stop"
            or stop["admission_id"] != evaluation["process_admission_id"]
            or stop["payload"] != records["stop"].model_dump(mode="json")
            or outcome["admission_id"] != evaluation["process_admission_id"]
            or outcome["input_manifest_sha256"] != evaluation["input_manifest_sha256"]
            or any(outcome[k] != evaluation[k] for k in SCOPE)
        ):
            raise ValueError("Native outcome lacks exact positive stop")
        observed = {
            "schema_version": "mineagent.native-failure-object.v1",
            "evaluation_id": identity,
            "failure_code": "invalid_result",
        }
        extraction = records.get("extraction")
        if extraction is not None:
            path = f"research-processes/{evaluation['owner_id']}/{evaluation['process_admission_id']}/output/{extraction.basename}"
            raw = read_bytes(path, extraction.sha256, MAX_FRAME)
            if (
                type(raw) is not bytes
                or len(raw) != extraction.size_bytes
                or hashlib.sha256(raw).hexdigest() != extraction.sha256
            ):
                raise ValueError("Native retained export bytes differ")
            files = validate_export(raw)
            try:
                observed = decode_native_files(files)
            except ValueError:
                pass  # exactly mirrors live parsing failure, never filesystem failure
        kind = (
            "search_native_observation"
            if observed["schema_version"] == "mineagent.native-observation-object.v1"
            else "search_native_failure"
        )
        expected_status = (
            "observed" if kind == "search_native_observation" else "failed"
        )
        raw = read_object(
            evaluation["owner_id"],
            outcome["bundle_object_id"],
            kind,
            outcome["bundle_sha256"],
        )
        if (
            raw != canonical(observed)
            or outcome["status"] != expected_status
            or outcome["bundle_kind"] != kind
        ):
            raise ValueError(
                "Native outcome was not produced by retained protected export"
            )
        request = {
            "evaluation_id": identity,
            "stop_receipt_id": stop["id"],
            "evidence_sha256": outcome["bundle_sha256"],
        }
        if hashlib.sha256(canonical(request)).hexdigest() != outcome["request_sha256"]:
            raise ValueError("Native outcome request commitment differs")
        if expected_status == "observed":
            bundle = observed["bundle"]
            bundle_sha = sha256_bytes(canonical_json_bytes(bundle))
            if outcome["native_bundle_sha256"] != bundle_sha:
                raise ValueError("Native bundle commitment differs")
            binding = NativeEvaluationBinding(
                **{
                    k: UUID(evaluation[k]) for k in ("owner_id", "run_id", "attempt_id")
                },
                generation=evaluation["generation"],
                evaluation_id=UUID(identity),
                candidate_id=UUID(evaluation["candidate_id"]),
                strategy_version_id=None,
                phase=evaluation["phase"],
                source_sha256=evaluation["source_sha256"],
                input_manifest_sha256=evaluation["input_manifest_sha256"],
                execution_plan_sha256=evaluation["execution_sha256"],
                evaluator_image=spec.execution.evaluator_image,
                bundle_sha256=bundle_sha,
                window=window,
            )
            observations[identity] = bound_native_observation(bundle, binding, spec)
        elif outcome["native_bundle_sha256"] is not None:
            raise ValueError(
                "Failed native outcome cannot retain successful bundle identity"
            )
    evaluation_ids = {r["id"] for r in tables["search_evaluations"]}
    if set(inputs) != evaluation_ids or set(outcomes) - evaluation_ids:
        raise ValueError("Native input/outcome inventory lacks evaluation")
    for identity, preparation in preparations.items():
        if identity not in evaluation_ids and reservations[
            preparation["reservation_id"]
        ]["state"] not in {"reserved", "uncertain"}:
            raise ValueError("Pending preparation cannot release capacity")
    return observations
