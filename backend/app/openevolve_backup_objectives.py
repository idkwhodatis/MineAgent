"""Recompute retained objective receipts from closed native observations."""

import hashlib

from packages.openevolve_adapter.checkpoint_native import canonical
from packages.openevolve_adapter.objective import score
from packages.research_runtime.broker_contracts import digest

SCOPE = ("owner_id", "run_id", "attempt_id", "generation")


def validate_objectives(tables, specs, observations, read_object):
    evaluations = {r["id"]: r for r in tables["search_evaluations"]}
    outcomes = {r["id"]: r for r in tables["search_evaluation_outcomes"]}
    for receipt in tables["search_objective_receipts"]:
        spec = specs[receipt["run_id"]]
        members = sorted(
            (
                r
                for r in tables["search_objective_evaluations"]
                if r["objective_receipt_id"] == receipt["id"]
            ),
            key=lambda r: r["ordinal"],
        )
        if len(members) != len(spec.objective.windows) or receipt[
            "objective_sha256"
        ] != digest(spec.objective):
            raise ValueError("Objective requires exact ordered frozen windows")
        ordered, ids, failed = [], [], False
        for ordinal, (member, window) in enumerate(
            zip(members, spec.objective.windows, strict=True)
        ):
            evaluation, outcome = (
                evaluations.get(member["evaluation_id"]),
                outcomes.get(member["outcome_id"]),
            )
            if (
                evaluation is None
                or outcome is None
                or member["ordinal"] != ordinal
                or member["window_id"] != window.id
                or any(
                    member[k] != receipt[k] or evaluation[k] != receipt[k]
                    for k in (*SCOPE, "epoch_id", "candidate_id", "source_sha256")
                )
                or member["execution_sha256"] != digest(spec.execution)
                or evaluation["execution_sha256"] != member["execution_sha256"]
                or evaluation["window_id"] != window.id
                or outcome["evaluation_id"] != evaluation["id"]
            ):
                raise ValueError("Objective physical window/outcome membership differs")
            ids.append(evaluation["id"])
            if outcome["status"] == "failed":
                failed = True
            elif outcome["status"] == "observed" and evaluation["id"] in observations:
                ordered.append(observations[evaluation["id"]])
            else:
                raise ValueError("Objective has no verified native observation")
        if failed:
            status, value, reason = "execution_failed", None, "native_error"
        else:
            computed = score(spec.objective, tuple(ordered))
            status = (
                "eligible"
                if computed.eligible
                else "constraint_failed"
                if computed.reason == "explicit_trade_constraint"
                else "undefined_objective"
            )
            value, reason = computed.combined_score, computed.reason
        feedback = canonical(
            {"status": status, "combined_score": value, "reason": reason}
        ).decode()
        feedback_sha = hashlib.sha256(feedback.encode()).hexdigest()
        expected = {
            "schema_version": "mineagent.search-objective-object.v1",
            "candidate_id": receipt["candidate_id"],
            "source_sha256": receipt["source_sha256"],
            "objective_sha256": digest(spec.objective),
            "evaluation_ids": ids,
            "status": status,
            "combined_score": value,
            "reason": reason,
            "feedback": feedback,
            "feedback_sha256": feedback_sha,
        }
        raw = read_object(
            receipt["owner_id"],
            receipt["evidence_object_id"],
            "search_objective",
            receipt["evidence_sha256"],
        )
        if (
            raw != canonical(expected)
            or receipt["status"] != status
            or receipt["combined_score"] != value
            or (
                receipt["combined_score"] is not None
                and type(receipt["combined_score"]) not in (int, float)
            )
            or receipt["feedback"] != feedback
            or receipt["feedback_sha256"] != feedback_sha
        ):
            raise ValueError(
                "Objective result differs from recomputed native observations"
            )
