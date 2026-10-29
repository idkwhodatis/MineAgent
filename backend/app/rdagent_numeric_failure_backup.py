"""027 RD failure semantics after actual rejection and provider-byte validation.

``rejection_units`` is the internal result of numeric_rejection_backup, never a
serialized grant. No candidate source or upstream code executes during restore.
"""

import hashlib
import json

from packages.factors.contracts import digest
from packages.rdagent_adapter.numeric_failure_codec import parse_failure_feedback

FIELDS = "evaluation_id evaluation_outcome_id cutoff_execution_id candidate_admission_id controller_launch_id final_decision_call_id rejection_receipt_id rejection_kind rejection_sha256 stop_receipt_id stop_kind stop_sha256 feedback_object_id feedback_object_kind feedback_sha256 attempt_id generation id owner_id run_id created_at"
SCOPE = ("owner_id", "run_id", "attempt_id", "generation")


def validate_failure_links(
    rd, research, native, execution, rejection_units, read_bytes, *, max_rows
):
    if type(max_rows) is not int or not 0 < max_rows <= 100000:
        raise ValueError("Bounded failure inventory required")
    if (rd.get("schema_version"), research.get("schema_version")) not in {
        (
            "mineagent.rd-source-restore-closure.v11",
            "mineagent.research-restore-closure.v16",
        ),
        (
            "mineagent.rd-source-restore-closure.v8",
            "mineagent.research-restore-closure.v13",
        ),
        (
            "mineagent.rd-source-restore-closure.v9",
            "mineagent.research-restore-closure.v14",
        ),
        (
            "mineagent.rd-source-restore-closure.v10",
            "mineagent.research-restore-closure.v15",
        ),
    }:
        raise ValueError("Exact027 failure closure pair required")

    def index(closure, table):
        rows = closure["tables"][table]
        if type(rows) is not list or len(rows) > max_rows:
            raise ValueError("Bounded failure table required")
        result = {row["id"]: row for row in rows}
        if len(result) != len(rows):
            raise ValueError("Duplicate failure evidence")
        return result

    domain = {
        name: index(rd, name)
        for name in (
            "factor_numeric_failure_feedbacks",
            "factor_evaluation_outcomes",
            "factor_evaluation_starts",
            "factor_generation_sources",
            "factor_cutoff_executions",
            "factor_controller_launches",
            "factor_upstream_calls",
            "factor_upstream_call_outcomes",
            "factor_generation_model_operations",
            "factor_generation_outcomes",
        )
    }
    shared = {
        name: index(research, name)
        for name in (
            "research_objects",
            "research_process_receipts",
            "research_model_operations",
            "research_model_transports",
            "research_model_outcomes",
        )
    }

    def require(ok):
        if not ok:
            raise ValueError("Exact numeric failure closure differs")

    def scope(row, other):
        require(all(row[k] == other[k] for k in SCOPE))

    def one(rows, predicate):
        found = [r for r in rows.values() if predicate(r)]
        require(len(found) == 1)
        return found[0]

    def raw_object(identity, sha, owner):
        obj = shared["research_objects"][identity]
        require(
            obj["owner_id"] == owner
            and obj["sha256"] == sha
            and 0 < obj["size_bytes"] <= 65536
        )
        path = f"research-objects/{owner}/by-kind/{obj['object_kind']}/{sha}.json"
        require(obj["storage_key"] == path)
        raw = read_bytes(path, sha, 65536)
        require(
            type(raw) is bytes
            and len(raw) == obj["size_bytes"]
            and hashlib.sha256(raw).hexdigest() == sha
        )
        return raw

    native_publications = index(native, "factor_native_publications")
    snapshots = index(execution, "factor_execution_snapshot_publications")
    consumed = set()
    for row in domain["factor_numeric_failure_feedbacks"].values():
        require(set(row) == set(FIELDS.split()))
        result = domain["factor_evaluation_outcomes"][row["evaluation_outcome_id"]]
        ev = domain["factor_evaluation_starts"][row["evaluation_id"]]
        src = domain["factor_generation_sources"][ev["evaluated_source_id"]]
        cut = domain["factor_cutoff_executions"][row["cutoff_execution_id"]]
        actor = domain["factor_controller_launches"][row["controller_launch_id"]]
        for other in (result, ev, src, cut, actor):
            scope(row, other)
        require(result["id"] not in consumed)
        consumed.add(result["id"])
        require(
            result["status"] == "execution_failed"
            and result["evaluation_id"] == ev["id"]
        )
        require(
            all(
                result[k] is None
                for k in (
                    "snapshot_id",
                    "snapshot_input_sha256",
                    "snapshot_output_sha256",
                )
            )
        )
        require(
            ev["expected_cutoffs"] == 1
            and cut["cutoff_ordinal"] == 0
            and cut["evaluation_id"] == ev["id"]
        )
        require(cut["candidate_admission_id"] == row["candidate_admission_id"])
        require(
            all(
                cut[k] == ev[k]
                for k in ("start_id", "evaluated_source_id", "factor_version_id")
            )
        )
        require(
            not any(
                g["start_id"] == src["start_id"]
                for g in domain["factor_generation_outcomes"].values()
            )
        )
        require(not any(p["evaluation_id"] == ev["id"] for p in snapshots.values()))
        require(
            not any(
                p["admission_id"] == row["candidate_admission_id"]
                for p in native_publications.values()
            )
        )
        launch, identity, stop, rejection = rejection_units[
            row["candidate_admission_id"]
        ]
        admitted = launch.intent.admission.model_dump(mode="json")
        scope(row, admitted)
        require(
            admitted["id"] == row["candidate_admission_id"]
            and admitted["logical_operation_id"] == cut["id"]
            and admitted["role"] == "factor_cutoff_candidate"
        )
        require(
            rejection.launch_sha256 == digest(launch)
            and rejection.identity_sha256 == digest(identity)
            and rejection.stop_sha256 == digest(stop)
        )
        for prefix in ("rejection", "stop"):
            receipt = shared["research_process_receipts"][row[prefix + "_receipt_id"]]
            scope(row, receipt)
            require(
                receipt["admission_id"] == row["candidate_admission_id"]
                and receipt["kind"] == prefix
                and row[prefix + "_kind"] == prefix
            )
            require(
                receipt["payload_sha256"]
                == row[prefix + "_sha256"]
                == digest(rejection if prefix == "rejection" else stop)
            )
        doc = parse_failure_feedback(
            raw_object(
                row["feedback_object_id"], row["feedback_sha256"], row["owner_id"]
            )
        )
        body = doc.model_dump(mode="json")
        scope(row, body)
        require(str(doc.controller_launch_id) == actor["id"])
        require(
            body["final_decision_call_id"] == row["final_decision_call_id"]
            and body["start_id"] == src["start_id"]
        )
        require(
            doc.code_sha256 == src["source_sha256"]
            and doc.input_sha256 == ev["input_sha256"]
        )
        expected = {
            k: row[k]
            for k in (
                "candidate_admission_id",
                "cutoff_execution_id",
                "rejection_receipt_id",
                "rejection_sha256",
                "stop_receipt_id",
                "stop_sha256",
            )
        }
        expected.update(
            source_id=src["id"],
            evaluation_id=ev["id"],
            factor_version_id=src["factor_version_id"],
            outcome=rejection.outcome,
            child_exit_code=rejection.child_exit_code,
        )
        require(all(body["failure"][k] == v for k, v in expected.items()))
        require(
            result["feedback_object_id"] == row["feedback_object_id"]
            and result["feedback_sha256"] == row["feedback_sha256"]
        )
        actual_feedback = json.loads(doc.upstream_feedback)
        for phase in ("critique", "final_decision"):
            call = one(
                domain["factor_upstream_calls"],
                lambda c, src=src, phase=phase: (
                    c["start_id"] == src["start_id"]
                    and c["phase"] == phase
                    and c["kind"] == "chat"
                ),
            )
            done = one(
                domain["factor_upstream_call_outcomes"],
                lambda c, call=call: c["call_id"] == call["id"],
            )
            scope(row, call)
            scope(row, done)
            require(
                done["status"] == "completed"
                and done["controller_launch_id"] == actor["id"]
                and done["member_count"] == 1
            )
            member = one(
                domain["factor_generation_model_operations"],
                lambda m, call=call: m["call_id"] == call["id"],
            )
            op = shared["research_model_operations"][member["operation_id"]]
            require(
                op["protocol"] == "mineagent.rdagent-provider-chat.v1"
                and op["state"] == "completed"
            )
            if phase == "critique":
                require(
                    doc.failure.feedback_text
                    in "\n".join(m["content"] for m in op["request"]["messages"])
                )
            transport = one(
                shared["research_model_transports"],
                lambda t, op=op: (
                    t["operation_id"] == op["id"] and t["state"] == "completed"
                ),
            )
            outcome = one(
                shared["research_model_outcomes"],
                lambda o, transport=transport: (
                    o["transport_id"] == transport["id"] and o["outcome"] == "completed"
                ),
            )
            response = json.loads(
                raw_object(
                    outcome["response_object_id"],
                    outcome["response_sha256"],
                    row["owner_id"],
                )
            )
            if phase == "critique":
                require(response["content"] == actual_feedback["code"])
            else:
                require(call["id"] == row["final_decision_call_id"])
                decision = json.loads(response["content"])
                require(
                    type(decision.get("final_decision")) is bool
                    and decision["final_decision"] == actual_feedback["final_decision"]
                )

    require(consumed == set(domain["factor_evaluation_outcomes"]))
