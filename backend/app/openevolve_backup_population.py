"""Retained native population is bound to source ancestry and host objectives.

The caller validates scoped FKs, recomputes objective receipts from retained native
observations, and supplies exact canonical registered bytes before using this.
"""

import hashlib
import json

from packages.openevolve_adapter.checkpoint_native import _normalize, state_sha256
from packages.openevolve_adapter.observer_transport import canonical, unique
from packages.openevolve_adapter.runtime_profile import validate_source_bound


def validate_population(state, epoch_id, frontier, expected_sha, tables):
    if type(state) is not dict or set(state) != {"metadata", "programs"}:
        raise ValueError("Native population schema differs")
    normalized = _normalize(state["metadata"], state["programs"])
    if (
        state_sha256(normalized) != expected_sha
        or normalized["metadata"]["last_iteration"] != frontier
    ):
        raise ValueError("Native population frontier differs")
    candidates = {
        r["id"]: r for r in tables["search_candidates"] if r["epoch_id"] == epoch_id
    }
    by_program = {r["upstream_program_id"]: r for r in candidates.values()}
    grants = {
        r["candidate_id"]: r
        for r in tables["search_candidate_grants"]
        if r["epoch_id"] == epoch_id
    }
    intents = {
        r["id"]: r
        for r in tables["search_iteration_intents"]
        if r["epoch_id"] == epoch_id
    }
    acks = {
        r["candidate_id"]: r
        for r in tables["search_objective_acknowledgements"]
        if r["epoch_id"] == epoch_id
    }
    receipts = {
        r["id"]: r
        for r in tables["search_objective_receipts"]
        if r["epoch_id"] == epoch_id
    }
    if len(by_program) != len(candidates):
        raise ValueError("Duplicate native program identity")
    for pid, program in normalized["programs"].items():
        source = program["code"].encode()
        validate_source_bound(source)
        candidate = by_program.get(pid)
        if (
            candidate is None
            or candidate["source_sha256"] != hashlib.sha256(source).hexdigest()
        ):
            raise ValueError("Native population has unregistered source")
        if candidate["kind"] == "seed":
            iteration, parent_id, generation = 0, None, 0
        elif candidate["kind"] == "mutation":
            grant = grants.get(candidate["id"])
            intent = intents.get(grant["intent_id"]) if grant else None
            parent = candidates.get(candidate["parent_candidate_id"])
            if (
                intent is None
                or parent is None
                or intent["parent_candidate_id"] != parent["id"]
                or intent["parent_source_sha256"] != parent["source_sha256"]
            ):
                raise ValueError("Native population ancestry intent differs")
            iteration, parent_id = (
                intent["native_iteration"],
                parent["upstream_program_id"],
            )
            generation = 1 if parent["kind"] == "seed" else 2
        else:
            raise ValueError("Unsupported native candidate kind")
        if (
            program["iteration_found"],
            program["parent_id"],
            program["generation"],
        ) != (iteration, parent_id, generation) or iteration > frontier:
            raise ValueError("Native population ancestry/iteration differs")
        ack = acks.get(candidate["id"])
        receipt = receipts.get(ack["objective_receipt_id"]) if ack else None
        if (
            receipt is None
            or receipt["status"] != "eligible"
            or type(program["metrics"].get("combined_score")) not in (int, float)
            or program["metrics"] != {"combined_score": receipt["combined_score"]}
        ):
            raise ValueError("Native population has unverified objective")
        if (
            type(receipt["feedback"]) is not str
            or hashlib.sha256(receipt["feedback"].encode()).hexdigest()
            != receipt["feedback_sha256"]
            or type(program["artifacts_json"]) is not str
        ):
            raise ValueError("Native population feedback absent")
        artifacts = json.loads(program["artifacts_json"], object_pairs_hook=unique)
        if canonical(artifacts) != canonical({"native_feedback": receipt["feedback"]}):
            raise ValueError("Native population host feedback differs")
    return normalized
