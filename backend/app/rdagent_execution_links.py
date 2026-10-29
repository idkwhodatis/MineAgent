"""Final021 cold links, called only after source/research/execution byte closures.

Empty final outcomes do not imply success. Canonical typed artifact bodies are
reread and hash-bound to independently inventoried actual files, never accepted
from a caller's DTO or inferred from an absent history.
"""

import hashlib
import json

from packages.factors.contracts import (
    FeatureRow,
    SnapshotInput,
    canonical_bytes,
    digest,
)
from packages.rdagent_adapter.execution_outcomes import parse_document
from packages.rdagent_adapter.fixture_objective import measured_fixture_objective

SCOPE = ("owner_id", "run_id", "attempt_id", "generation")


def validate_execution_generation_links(
    rd_source_closure,
    execution_closure,
    research_closure,
    read_json,
    max_rows,
    *,
    direct_analysis_closure=None,
):
    def require(value):
        if not value:
            raise ValueError("RD execution final links differ")

    require(type(max_rows) is int and max_rows > 0)
    configured = rd_source_closure["schema_version"] in {
        "mineagent.rd-source-restore-closure.v7",
        "mineagent.rd-source-restore-closure.v11",
        "mineagent.rd-source-restore-closure.v4",
        "mineagent.rd-source-restore-closure.v5",
        "mineagent.rd-source-restore-closure.v8",
        "mineagent.rd-source-restore-closure.v9",
        "mineagent.rd-source-restore-closure.v6",
        "mineagent.rd-source-restore-closure.v10",
    }
    require(
        (
            rd_source_closure["schema_version"],
            execution_closure["schema_version"],
            research_closure["schema_version"],
        )
        in (
            {
                (
                    "mineagent.rd-source-restore-closure.v7",
                    "mineagent.execution-snapshot-restore-closure.v2",
                    "mineagent.research-restore-closure.v12",
                ),
                (
                    "mineagent.rd-source-restore-closure.v4",
                    "mineagent.execution-snapshot-restore-closure.v2",
                    "mineagent.research-restore-closure.v7",
                ),
                (
                    "mineagent.rd-source-restore-closure.v4",
                    "mineagent.execution-snapshot-restore-closure.v2",
                    "mineagent.research-restore-closure.v8",
                ),
                (
                    "mineagent.rd-source-restore-closure.v4",
                    "mineagent.execution-snapshot-restore-closure.v2",
                    "mineagent.research-restore-closure.v9",
                ),
                (
                    "mineagent.rd-source-restore-closure.v5",
                    "mineagent.execution-snapshot-restore-closure.v2",
                    "mineagent.research-restore-closure.v10",
                ),
                (
                    "mineagent.rd-source-restore-closure.v8",
                    "mineagent.execution-snapshot-restore-closure.v2",
                    "mineagent.research-restore-closure.v13",
                ),
                (
                    "mineagent.rd-source-restore-closure.v9",
                    "mineagent.execution-snapshot-restore-closure.v2",
                    "mineagent.research-restore-closure.v14",
                ),
                (
                    "mineagent.rd-source-restore-closure.v6",
                    "mineagent.execution-snapshot-restore-closure.v2",
                    "mineagent.research-restore-closure.v11",
                ),
                (
                    "mineagent.rd-source-restore-closure.v10",
                    "mineagent.execution-snapshot-restore-closure.v2",
                    "mineagent.research-restore-closure.v15",
                ),
                (
                    "mineagent.rd-source-restore-closure.v11",
                    "mineagent.execution-snapshot-restore-closure.v2",
                    "mineagent.research-restore-closure.v16",
                ),
            }
            if configured
            else {
                (
                    "mineagent.rd-source-restore-closure.v3",
                    "mineagent.execution-snapshot-restore-closure.v1",
                    "mineagent.research-restore-closure.v6",
                ),
            }
        )
    )
    analysis_rows = {}
    analysis_intents = {}
    configured_runs = set()
    parser = parse_document
    if configured:
        require(
            type(direct_analysis_closure) is dict
            and direct_analysis_closure.get("schema_version")
            == "mineagent.direct-analysis-restore-closure.v1"
        )
        from packages.rdagent_adapter.analysis_object_codec import (
            parse_document as parser,
        )

        for row in direct_analysis_closure["tables"]["factor_direct_analysis_outcomes"]:
            require(row["analysis_id"] not in analysis_rows)
            analysis_rows[row["analysis_id"]] = row
        for row in direct_analysis_closure["tables"]["factor_direct_analysis_intents"]:
            require(row["id"] not in analysis_intents)
            analysis_intents[row["id"]] = row
        configured_runs = {
            (row["owner_id"], row["run_id"])
            for row in direct_analysis_closure["tables"]["factor_analysis_plans"]
        }

    def indexes(closure):
        result = {}
        for name, rows in closure["tables"].items():
            require(type(rows) is list and len(rows) <= max_rows)
            if name.endswith("partitions"):
                continue
            key = (
                "publication_id"
                if name
                in {"research_object_outcomes", "factor_execution_snapshot_outcomes"}
                else (
                    "evaluation_id"
                    if name == "factor_evaluation_result_consumers"
                    else (
                        "reservation_id"
                        if name == "engine_object_reservation_consumers"
                        else (
                            "admission_id"
                            if name == "research_process_states"
                            else "id"
                        )
                    )
                )
            )
            if rows and key not in rows[0]:
                continue
            result[name] = {r[key]: r for r in rows}
            require(len(result[name]) == len(rows))
        return result

    rd, ex, rs = (
        indexes(rd_source_closure),
        indexes(execution_closure),
        indexes(research_closure),
    )

    def same(a, b):
        require(all(a[k] == b[k] for k in SCOPE))

    def artifact(identity, kind, scope):
        obj = rs["research_objects"][identity]
        require(
            obj["owner_id"] == scope["owner_id"]
            and obj["object_kind"] == kind
            and 1 <= obj["size_bytes"] <= 65536
        )
        raw = canonical_bytes(read_json(obj["storage_key"]))
        require(
            len(raw) == obj["size_bytes"]
            and hashlib.sha256(raw).hexdigest() == obj["sha256"]
        )
        doc = parser(kind, raw).model_dump(mode="json")
        same(doc, scope)
        publications = [
            p
            for p in rs["research_object_publications"].values()
            if p["id"] in rs["research_object_outcomes"]
            and rs["research_object_outcomes"][p["id"]]["object_id"] == identity
            and all(p[k] == scope[k] for k in SCOPE)
        ]
        require(publications)
        return obj, doc

    evaluated = {}
    for result in ex["factor_execution_evaluation_outcomes"].values():
        start = rd["factor_generation_starts"][result["generation_start_id"]]
        evaluation = rd["factor_evaluation_starts"][result["evaluation_id"]]
        source = rd["factor_generation_sources"][evaluation["evaluated_source_id"]]
        publication = ex["factor_execution_snapshot_publications"][
            result["snapshot_id"]
        ]
        snapshot = ex["factor_execution_snapshot_outcomes"][result["snapshot_id"]]
        boundary = rd["factor_generation_boundaries"][result["boundary_id"]]
        for row in (start, evaluation, source, publication, snapshot, boundary):
            same(row, result)
        require(
            source["start_id"] == start["id"] == boundary["start_id"]
            and boundary["boundary"] == "returned"
            and boundary["controller_launch_id"] == result["controller_launch_id"]
        )
        manifest = publication["journal"]["manifest"]
        inp = SnapshotInput.model_validate_json(json.dumps(manifest["input"]))
        rawrows = read_json(manifest["assembled_rows"]["storage_key"])
        require(digest(rawrows) == manifest["assembled_rows"]["sha256"])
        rows = tuple(
            FeatureRow.model_validate_json(json.dumps(row)) for row in rawrows["rows"]
        )
        objective = None
        common = {
            **{k: result[k] for k in SCOPE},
            "start_id": start["id"],
            "source_id": source["id"],
            "evaluation_id": evaluation["id"],
            "snapshot_id": result["snapshot_id"],
            "factor_version_id": source["factor_version_id"],
            "code_sha256": source["source_sha256"],
            "input_sha256": result["snapshot_input_sha256"],
            "output_sha256": result["snapshot_output_sha256"],
            "manifest_sha256": result["snapshot_manifest_sha256"],
        }
        obj, feedback = artifact(
            result["feedback_object_id"], "factor_generation_feedback", result
        )
        if (result["owner_id"], result["run_id"]) in configured_runs:
            require(feedback["schema_version"] == "mineagent.rd-execution-feedback.v2")
            analysis = analysis_rows[feedback["analysis_id"]]
            intent = analysis_intents[feedback["analysis_id"]]
            same(analysis, result)
            same(intent, result)
            require(intent["snapshot_id"] == result["snapshot_id"])
            measurement = analysis["measurement"]
            require(
                feedback["analysis_result_sha256"] == analysis["result_sha256"]
                and feedback["objective_measurement_sha256"]
                == analysis["measurement_sha256"]
                == digest(measurement)
                and feedback["objective_status"] == measurement["status"]
                and feedback["objective_reason"] == measurement["reason"]
            )
            objective = measurement["status"] == "passed"
        else:
            require(feedback["schema_version"] == "mineagent.rd-execution-feedback.v1")
            objective = measured_fixture_objective(inp, rows)
            require(feedback["objective_passed"] == objective)
        require(
            obj["sha256"] == result["feedback_sha256"]
            and all(feedback[k] == v for k, v in common.items())
            and feedback["row_count"] == len(rows)
            and feedback["missing_count"] == sum(r.value is None for r in rows)
            and feedback["declared_window"]
            == {**inp.window.model_dump(mode="json"), "complete": True}
        )
        require(start["id"] not in evaluated)
        evaluated[start["id"]] = (result, common, feedback, objective)
    for outcome in rd["factor_generation_outcomes"].values():
        require(outcome["start_id"] in evaluated)
        result, common, feedback, objective = evaluated[outcome["start_id"]]
        same(outcome, result)
        require(
            outcome["outcome"] == "accepted"
            and outcome["logical_step"] == 0
            and outcome["error_code"] is None
            and objective is True
            and outcome["feedback_object_id"] == result["feedback_object_id"]
        )
        require(
            all(
                outcome[k] == common["source_id"]
                for k in (
                    "evaluated_source_id",
                    "adopted_source_id",
                    "continuation_source_id",
                )
            )
            and all(
                outcome[k] == common["factor_version_id"]
                for k in (
                    "factor_version_id",
                    "adopted_factor_version_id",
                    "continuation_factor_version_id",
                )
            )
        )
        proposal_obj, proposal = artifact(
            outcome["proposal_object_id"], "factor_generation_proposal", outcome
        )
        adoption_obj, adoption = artifact(
            outcome["adoption_object_id"], "factor_adoption_receipt", outcome
        )
        require(all(proposal[k] == v and adoption[k] == v for k, v in common.items()))
        source = rd["factor_generation_sources"][common["source_id"]]
        assembly = rd["factor_response_assemblies"][source["assembly_id"]]
        boundary = rd["factor_generation_boundaries"][result["boundary_id"]]
        require(
            proposal["source_assembly_id"] == assembly["id"]
            and proposal["source_assembly_sha256"] == assembly["object_sha256"]
        )
        require(
            adoption["boundary_id"] == boundary["id"]
            and adoption["observation_id"] == boundary["observation_id"]
            and adoption["controller_launch_id"] == boundary["controller_launch_id"]
            and adoption["proposal_object_id"] == proposal_obj["id"]
            and adoption["proposal_sha256"] == proposal_obj["sha256"]
            and adoption["feedback_object_id"] == result["feedback_object_id"]
            and adoption["feedback_sha256"] == result["feedback_sha256"]
            and outcome["adoption_sha256"] == adoption_obj["sha256"]
            and outcome["adoption_object_kind"] == "factor_adoption_receipt"
        )
        start = rd["factor_generation_starts"][outcome["start_id"]]
        held = rs["research_budget_reservations"][start["mutation_reservation_id"]]
        settlements = [
            s
            for s in rs["research_budget_settlements"].values()
            if s["reservation_id"] == held["id"]
        ]
        require(
            held["state"] == "settled"
            and len(settlements) == 1
            and settlements[0]["outcome"] == "conservative_unknown"
            and settlements[0]["charge"] == held["amounts"]
        )
        same(settlements[0], outcome)
    return True
