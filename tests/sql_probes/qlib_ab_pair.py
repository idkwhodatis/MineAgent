"""Publish only matched, successful native Qlib generation commit observations."""

import argparse
import json
import subprocess
from pathlib import Path

from tests.sql_probes.qlib_ab_evidence import (
    BEFORE_SOURCE_PHASES,
    CHILDREN,
    PHASES,
    _number,
    require,
    validate_checkout,
)

ROOT = Path("var/numeric-private")
STATUSES = {"passed", "failure", "error", "skipped"}
ATTACHED_STAGES = {
    6: ("proposal", "reserve_object"),
    8: ("proposal", "finalize_object"),
    9: ("adoption", "reserve_object"),
    11: ("adoption", "finalize_object"),
}
SLOTS = {(child, ordinal) for child in CHILDREN for ordinal in ATTACHED_STAGES}
ROW_KEYS = {
    "child",
    "method",
    "rpc",
    "transaction",
    "publication",
    "stage",
    "dbapi_count",
    "dbapi_ms",
}
ARM_FIELDS = {
    "schema",
    "revision",
    "arm",
    "acceptance",
    "phase",
    "source_attested",
    "restore_attested",
    "commits",
}


def _bounded(path, maximum):
    require(not path.is_symlink() and path.is_file() and path.stat().st_size <= maximum)
    payload = path.read_bytes()
    require(len(payload) <= maximum)
    return payload


def _load_arm(root, arm, revision):
    status = _bounded(root / f"{arm}.status", 2)
    raw = json.loads(_bounded(root / f"{arm}-closed.json", 256_000))
    require(
        type(raw) is dict
        and raw.keys() == ARM_FIELDS
        and raw["schema"] == "QLIB_ASCII_AB_V1"
        and raw["revision"] == revision
        and raw["arm"] == arm
        and raw["acceptance"] in STATUSES
        and raw["phase"] in PHASES
        and (raw["phase"] == "accepted") == (raw["acceptance"] == "passed")
        and type(raw["source_attested"]) is bool
        and type(raw["restore_attested"]) is bool
        and (raw["phase"] in BEFORE_SOURCE_PHASES) == (not raw["source_attested"])
        and (raw["phase"] in {"post_restore", "accepted"}) == raw["restore_attested"]
        and (raw["phase"] != "graph_body_edit" or arm == "ascii_reuse")
        and type(raw["commits"]) is list
        and len(raw["commits"]) <= 512
    )
    require(
        (status == b"0\n" and raw["acceptance"] in {"passed", "skipped"})
        or (status == b"1\n" and raw["acceptance"] in {"failure", "error"})
    )
    if raw["acceptance"] != "passed":
        return raw["acceptance"], raw["phase"], {}
    require(raw["source_attested"] and raw["restore_attested"])
    observed = {}
    for row in raw["commits"]:
        require(type(row) is dict and row.keys() == ROW_KEYS)
        if row["method"] != "generation_boundary":
            continue
        key = (row["child"], row["transaction"])
        require(
            type(row["child"]) is str
            and type(row["transaction"]) is int
            and key in SLOTS
            and key not in observed
            and type(row["rpc"]) is int
            and row["rpc"] == 75
            and (row["publication"], row["stage"]) == ATTACHED_STAGES[key[1]]
            and type(row["dbapi_count"]) is int
            and row["dbapi_count"] == 1
            and _number(row["dbapi_ms"])
        )
        observed[key] = row["dbapi_ms"]
    return "passed", "accepted", observed


def compare(root, revision):
    baseline, baseline_phase, left = _load_arm(root, "baseline", revision)
    treatment, treatment_phase, right = _load_arm(root, "ascii_reuse", revision)
    complete = (
        baseline == treatment == "passed" and left.keys() == right.keys() == SLOTS
    )
    slots = (
        [
            {
                "child": child,
                "transaction": ordinal,
                "publication": ATTACHED_STAGES[ordinal][0],
                "stage": ATTACHED_STAGES[ordinal][1],
                "baseline_dbapi_ms": left[child, ordinal],
                "ascii_reuse_dbapi_ms": right[child, ordinal],
            }
            for child, ordinal in sorted(SLOTS)
        ]
        if complete
        else []
    )
    return {
        "schema": "QLIB_ASCII_AB_PAIR_V1",
        "revision": revision,
        "baseline": baseline,
        "ascii_reuse": treatment,
        "baseline_phase": baseline_phase,
        "ascii_reuse_phase": treatment_phase,
        "comparison": "generation_commit_slots" if complete else "unavailable",
        "slots": slots,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkout", required=True)
    args = parser.parse_args()
    try:
        validate_checkout(args.checkout)
        result = compare(ROOT, args.checkout)
    except (ValueError, OSError, subprocess.SubprocessError, TypeError, OverflowError):
        print("QLIB_ASCII_AB_PAIR_V1 unavailable")
        return 1
    print(json.dumps(result, sort_keys=True, allow_nan=False, separators=(",", ":")))
    return 0 if result["comparison"] != "unavailable" else 1


if __name__ == "__main__":
    raise SystemExit(main())
