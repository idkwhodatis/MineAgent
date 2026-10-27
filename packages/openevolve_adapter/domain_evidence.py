"""Finite retained OE evidence grammars. Semantic authorization stays in host/SQL.

Float-bearing native observations remain exact bounded canonical bytes; nothing
here executes native code or upgrades a native producer's self-observation.
"""

import hashlib
import math
from uuid import UUID

from packages.openevolve_adapter.checkpoint_native import (
    PROGRAM_FIELDS,
    _normalize,
    canonical,
)
from packages.openevolve_adapter.event_projection import (
    ControllerEvent,
    SamplingPayload,
    TerminalPayload,
)
from packages.openevolve_adapter.host_contracts import (
    InitialCheckpointClosure,
    ProposalSourceEnvelope,
    RenderedProjection,
    SourceEnvelope,
)
from packages.openevolve_adapter.worker_identity_evidence import WorkerIdentityEvidence
from packages.tradegrad_adapter.independent_results import validate_result_bundle

MODELS = {
    "search_source": SourceEnvelope,
    "search_rendered_prompt": RenderedProjection,
    "search_proposal_source": ProposalSourceEnvelope,
    "search_checkpoint_closure": InitialCheckpointClosure,
    "search_worker_identity": WorkerIdentityEvidence,
}
KINDS = frozenset(MODELS) | {
    "search_population",
    "search_context",
    "search_controller_event",
    "search_worker_outcome",
    "search_native_observation",
    "search_native_failure",
    "search_objective",
}


def _uuid(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError("canonical evidence UUID")


def _sha(value):
    if (
        type(value) is not str
        or len(value) != 64
        or any(x not in "0123456789abcdef" for x in value)
    ):
        raise ValueError("evidence SHA")


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("evidence integer")


def parse_domain(kind, raw, value):
    if kind in MODELS:
        model = MODELS[kind].model_validate_json(raw)
        if canonical(model.model_dump(mode="json")) != raw:
            raise ValueError("full canonical typed evidence")
        return model
    if kind == "search_population":
        if (
            set(value) != {"metadata", "programs"}
            or canonical(_normalize(value["metadata"], value["programs"])) != raw
            or len(value["programs"]) > 8
        ):
            raise ValueError("initial bounded native population")
    elif kind == "search_controller_event":
        model = ControllerEvent.model_validate_json(raw)
        cls = SamplingPayload if model.kind == "iteration_intent" else TerminalPayload
        payload = cls.model_validate_json(canonical(model.payload))
        if (
            canonical(model.model_dump(mode="json")) != raw
            or payload.model_dump(mode="json") != model.payload
        ):
            raise ValueError("controller evidence shape")
    elif kind == "search_context":
        if (
            set(value)
            != {
                "schema_version",
                "native_iteration",
                "parent_id",
                "inspiration_ids",
                "snapshot",
            }
            or value["schema_version"] != "mineagent.native-selection.v1"
        ):
            raise ValueError("selection fields")
        _integer(value["native_iteration"], 1, 2)
        _uuid(value["parent_id"])
        if (
            type(value["inspiration_ids"]) is not list
            or len(value["inspiration_ids"]) > 8
            or len(set(value["inspiration_ids"])) != len(value["inspiration_ids"])
        ):
            raise ValueError("selection inspirations")
        for identity in value["inspiration_ids"]:
            _uuid(identity)
        snapshot = value["snapshot"]
        if (
            type(snapshot) is not dict
            or set(snapshot)
            != {
                "programs",
                "islands",
                "current_island",
                "feature_dimensions",
                "artifacts",
                "sampling_island",
            }
            or type(snapshot["programs"]) is not dict
            or not 1 <= len(snapshot["programs"]) <= 8
            or snapshot["feature_dimensions"] != ["complexity", "diversity"]
            or type(snapshot["artifacts"]) is not dict
        ):
            raise ValueError("selection snapshot")
        _integer(snapshot["current_island"], 0, 0)
        _integer(snapshot["sampling_island"], 0, 0)
        if (
            type(snapshot["islands"]) is not list
            or len(snapshot["islands"]) != 1
            or type(snapshot["islands"][0]) is not list
        ):
            raise ValueError("selection islands")
        for pid, program in snapshot["programs"].items():
            _uuid(pid)
            if (
                type(program) is not dict
                or set(program) != PROGRAM_FIELDS
                or program["id"] != pid
                or type(program["code"]) is not str
                or not 1 <= len(program["code"].encode()) <= 32768
            ):
                raise ValueError("selection program")
    elif kind == "search_worker_outcome":
        if value.get("schema_version") == "mineagent.search-worker-outcome.v2":
            from packages.openevolve_adapter.worker_outcome_evidence import validate

            return validate(value)
        if (
            set(value)
            != {
                "schema_version",
                "epoch_id",
                "intent_id",
                "native_iteration",
                "status",
                "objective_ack_id",
            }
            or value["schema_version"] != "mineagent.search-worker-outcome.v1"
            or value["status"] not in {"evaluated", "worker_error"}
        ):
            raise ValueError("worker outcome")
        _uuid(value["epoch_id"])
        _uuid(value["intent_id"])
        _integer(value["native_iteration"], 1, 2)
        if value["objective_ack_id"] is not None:
            _uuid(value["objective_ack_id"])
        elif value["status"] == "evaluated":
            raise ValueError("evaluated worker needs acknowledgement")
    elif kind == "search_native_observation":
        if (
            set(value) != {"schema_version", "bundle"}
            or value["schema_version"] != "mineagent.native-observation-object.v1"
        ):
            raise ValueError("native observation envelope")
        validate_result_bundle(value["bundle"])
    elif kind == "search_native_failure":
        if (
            set(value) != {"schema_version", "evaluation_id", "failure_code"}
            or value["schema_version"] != "mineagent.native-failure-object.v1"
            or value["failure_code"]
            not in {"engine_error", "invalid_result", "timeout"}
        ):
            raise ValueError("native failure")
        _uuid(value["evaluation_id"])
    elif kind == "search_objective":
        if (
            set(value)
            != {
                "schema_version",
                "candidate_id",
                "source_sha256",
                "objective_sha256",
                "evaluation_ids",
                "status",
                "combined_score",
                "reason",
                "feedback",
                "feedback_sha256",
            }
            or value["schema_version"] != "mineagent.search-objective-object.v1"
        ):
            raise ValueError("objective envelope")
        _uuid(value["candidate_id"])
        _sha(value["source_sha256"])
        _sha(value["objective_sha256"])
        _sha(value["feedback_sha256"])
        if (
            type(value["evaluation_ids"]) is not list
            or not 1 <= len(value["evaluation_ids"]) <= 8
            or len(set(value["evaluation_ids"])) != len(value["evaluation_ids"])
        ):
            raise ValueError("objective evaluations")
        for identity in value["evaluation_ids"]:
            _uuid(identity)
        if value["status"] not in {
            "eligible",
            "execution_failed",
            "constraint_failed",
            "undefined_objective",
        }:
            raise ValueError("objective status")
        score = value["combined_score"]
        if (score is None) != (value["status"] != "eligible") or (
            score is not None
            and (type(score) not in (int, float) or not math.isfinite(score))
        ):
            raise ValueError("objective score")
        reason = value["reason"]
        if reason not in {
            "eligible",
            "native_error",
            "undefined_objective",
            "invalid_drawdown",
            "explicit_trade_constraint",
            "nonfinite_aggregation",
        }:
            raise ValueError("objective reason")
        feedback = value["feedback"]
        if (
            type(feedback) is not str
            or not 1 <= len(feedback.encode()) <= 16384
            or hashlib.sha256(feedback.encode()).hexdigest() != value["feedback_sha256"]
        ):
            raise ValueError("objective feedback")
        if feedback != canonical(
            {"status": value["status"], "combined_score": score, "reason": reason}
        ).decode("utf-8"):
            raise ValueError("objective exact feedback payload")
    else:
        raise ValueError("unsupported domain evidence")
    return value
