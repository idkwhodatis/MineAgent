"""Bounded actual native result projection, not adoption or execution authority."""

import hashlib
from uuid import UUID

from packages.openevolve_structured.checkpoint import PROGRAM_FIELDS, canonical

SCHEMA = "mineagent.oe-structured-worker-outcome.v1"
PROJECTION_FIELDS = frozenset(
    ("epoch_id", "intent_id", "native_iteration", "status", "objective_ack_id")
)
OBSERVATION_FIELDS = frozenset(
    ("iteration", "outcome", "child", "parent_id", "target_island")
)


def _uuid(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError("Canonical native result UUID required")


def validate(value, domain):
    if (
        type(value) is not dict
        or set(value) != {"schema_version", "projection", "observation", "event_sha256"}
        or value["schema_version"] != SCHEMA
    ):
        raise ValueError("Closed native result evidence required")
    raw = canonical(value)
    if len(raw) > 131072:
        raise ValueError("Native result evidence exceeds bound")
    sha = value["event_sha256"]
    if type(sha) is not str or len(sha) != 64 or set(sha) - set("0123456789abcdef"):
        raise ValueError("Exact observed event digest required")
    p, o = value["projection"], value["observation"]
    if (
        type(p) is not dict
        or set(p) != PROJECTION_FIELDS
        or type(o) is not dict
        or set(o) != OBSERVATION_FIELDS
    ):
        raise ValueError("Closed result projection required")
    _uuid(p["epoch_id"])
    _uuid(p["intent_id"])
    if (
        type(p["native_iteration"]) is not int
        or not 1 <= p["native_iteration"] <= 2
        or type(o["iteration"]) is not int
        or o["iteration"] != p["native_iteration"]
    ):
        raise ValueError("Result iteration differs")
    if p["status"] not in ("evaluated", "worker_error") or o["outcome"] != p["status"]:
        raise ValueError("Result status differs")
    if p["objective_ack_id"] is not None:
        _uuid(p["objective_ack_id"])
    if p["status"] == "worker_error":
        # Pinned SerializableResult(error=..., iteration=...) has these defaults.
        if any(o[k] is not None for k in ("child", "parent_id", "target_island")):
            raise ValueError("Native error result carries unexpected child")
        return value
    if (
        p["objective_ack_id"] is None
        or type(o["target_island"]) is not int
        or o["target_island"] != 0
    ):
        raise ValueError("Evaluated result lacks objective/island")
    _uuid(o["parent_id"])
    child = o["child"]
    if type(child) is not dict or set(child) != PROGRAM_FIELDS:
        raise ValueError("Exact native Program fields required")
    _uuid(child["id"])
    if (
        child["parent_id"] != o["parent_id"]
        or type(child["iteration_found"]) is not int
        or child["iteration_found"] != o["iteration"]
    ):
        raise ValueError("Native child ancestry differs")
    code = child["code"]
    if (
        type(code) is not str
        or not 1 <= len(code.encode()) <= 32768
        or any(x in code for x in ("\0", "\r"))
        or code.startswith("\ufeff")
    ):
        raise ValueError("Native child source grammar differs")
    from .candidate import project_candidate

    project_candidate(code.encode("utf-8"), domain)
    if (
        child["language"] != "json"
        or child["prompts"] is not None
        or child["artifact_dir"] is not None
    ):
        raise ValueError("External native child input forbidden")
    if type(child["generation"]) is not int or not 1 <= child["generation"] <= 2:
        raise ValueError("Native child generation differs")
    if (
        type(child["metrics"]) is not dict
        or set(child["metrics"]) != {"combined_score"}
        or type(child["metrics"]["combined_score"]) not in (int, float)
    ):
        raise ValueError("Eligible native child score required")
    if type(child["metadata"]) is not dict or any(
        type(child[k]) not in (int, float)
        for k in ("timestamp", "complexity", "diversity")
    ):
        raise ValueError("Native child metadata differs")
    if (
        child["changes_description"] is not None
        and type(child["changes_description"]) is not str
    ):
        raise ValueError("Native child description differs")
    if child["embedding"] is not None or child["artifacts_json"] is not None:
        raise ValueError("Initial profile has no native embedding/artifacts")
    return value


def candidate_sha256(value, domain):
    validate(value, domain)
    child = value["observation"]["child"]
    return None if child is None else hashlib.sha256(child["code"].encode()).hexdigest()
