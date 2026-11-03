"""Closed-schema EXPERIMENT receipts. Never publish raw pytest/SQL payloads."""

import json
import math
import sys
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from xml.etree import ElementTree as ET

PROOF = Path("var/numeric-proof")
PRIVATE = Path("var/numeric-private")
POLICIES = ("default-off-waits", "explicit-opt-in-two-children")
CASES = {
    "test_normal_workflow_children_consume_frozen_inputs_and_real_numeric_results["
    + p
    + "]"
    for p in POLICIES
}


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 10**12


@lru_cache(maxsize=1)
def expected_bodies():
    from tests.integration.rd_hash_reuse_experiment import (
        _body,
        candidate_sql,
        previous_function,
    )

    return tuple(
        sha256(_body(sql).encode()).hexdigest()
        for sql in (previous_function(), candidate_sql())
    )


FAILURE_VALUES = {
    "family": frozenset(
        [
            "rpc",
            "process",
            "provider",
            "domain",
            "value",
            "validation",
            "attribute",
            "type",
            "key",
            "timeout",
            "other",
            "orm",
            "database",
        ]
    ),
    "code": frozenset(
        [
            "unknown",
            "deadline",
            "rd_controller_daemon_identity",
            "rd_controller_peer",
            "rd_controller_not_running",
            "rd_controller_launch",
            "rd_controller_receipts",
            "rd_controller_peer_drift",
            "rd_controller_incomplete_generation",
            "rd_provider_containment_unknown",
            "original_controller_deadline",
            "provider_secret_unavailable",
            "provider_timeout",
            "provider_outcome_unknown",
            "provider_containment_unconfirmed",
            "rd_controller_deadline",
            "rd_controller_expired_before_create",
            "rd_controller_expired_before_start",
            "rd_controller_expired_peer",
            "rd_controller_kernel_deadline_exceeded",
            "rd_controller_original_deadline_completion_missing",
            "rd_controller_incomplete_or_failed_reports",
            "rd_controller_daemon_stop_uncertain",
            "rd_controller_absence_uncertain",
            "neutral_transaction_lost",
            "neutral_busy",
            "numeric_process_command_deadline",
            "numeric_process_command_cleanup",
        ]
    ),
    "stage": frozenset(
        [
            "unknown",
            "queue",
            "controller",
            "controller_start",
            "controller_accept",
            "controller_finish",
            "rpc_read",
            "rpc_send",
            "peer",
            "observer",
            "provider",
            "evaluation",
            "failure_phase",
            "failure_finalize",
            "member_response",
            "object_publish",
            "object_reserve",
            "object_persist",
            "failure_document",
            "transaction",
        ]
    ),
    "sqlstate": frozenset(
        ["none", "other", "23503", "23505", "23514", "40001", "55P03", "57014"]
    ),
    "observer_failure": frozenset(
        [
            "unknown",
            "container_state",
            "host_snapshot",
            "host_read",
            "daemon_top",
            "deadline",
        ]
    ),
}


def bounded_integer(value, low=0, high=3_600_000):
    return type(value) is int and low <= value <= high


def closed_rpc_record(prefix, value):
    from runtimes.rdagent_operational.rpc import METHODS

    if type(value) is not dict:
        return None
    methods = METHODS | {"unknown", "invalid"}
    stages = {"read", "send", "auth", "handle", "commit"}
    if prefix == "RD_QUEUE_FAILURE":
        required = {"family", "code", "stage", "sqlstate"}
        if not required <= set(value) <= required | {"rpc_method", "observer_failure"}:
            return None
        for key, entry in value.items():
            allowed = (
                METHODS | {"unknown"} if key == "rpc_method" else FAILURE_VALUES[key]
            )
            if type(entry) is not str or entry not in allowed:
                return None
        return value
    if prefix != "RD_RPC_TIMING" or set(value) != {"summary", "tail"}:
        return None
    summary, tail = value["summary"], value["tail"]
    if (
        type(summary) is not list
        or type(tail) is not list
        or len(summary) > len(methods) * len(stages)
        or len(tail) > 64
    ):
        return None
    identities = set()
    for row in summary:
        if type(row) is not dict or set(row) != {
            "method",
            "stage",
            "count",
            "total_ms",
            "max_ms",
        }:
            return None
        if (
            type(row["method"]) is not str
            or row["method"] not in methods
            or type(row["stage"]) is not str
            or row["stage"] not in stages
        ):
            return None
        identity = (row["method"], row["stage"])
        if identity in identities or not all(
            bounded_integer(row[k]) for k in ("count", "total_ms", "max_ms")
        ):
            return None
        identities.add(identity)
    for row in tail:
        if type(row) is not dict or set(row) != {
            "method",
            "sequence",
            "stage",
            "elapsed_ms",
            "remaining_ms",
            "ok",
        }:
            return None
        if (
            type(row["method"]) is not str
            or row["method"] not in methods
            or type(row["stage"]) is not str
            or row["stage"] not in stages
        ):
            return None
        if (
            not bounded_integer(row["sequence"])
            or not bounded_integer(row["elapsed_ms"])
            or not bounded_integer(row["remaining_ms"], -3_600_000)
            or type(row["ok"]) is not bool
        ):
            return None
    return value


def closed_record(prefix, value, mode):
    # Import offline-only helper; no installation takes place while reporting.
    from tests.integration.rd_hash_reuse_experiment import PINS, REVISION
    from tests.integration.rd_pg_diagnostic import FUNCTIONS

    if not isinstance(value, dict):
        return None
    if prefix in ("RD_QUEUE_FAILURE", "RD_RPC_TIMING"):
        return closed_rpc_record(prefix, value)
    if prefix == "QLIB_HASH_EXPERIMENT":
        from tests.integration.rd_canonical_reuse_experiment import provenance

        canonical = provenance(mode)
        keys = {
            "mode",
            "policy",
            "schema_revision",
            "schema_state",
            "migration_sql_sha256",
            "original_body_sha256",
            "installed_body_sha256",
            "catalog_properties_verified",
            "production_acceptance",
            "elapsed_seconds",
        } | set(canonical)
        if (
            set(value) == keys
            and value["mode"] == mode
            and all(value[key] == expected for key, expected in canonical.items())
            and type(value["canonical_reuse"]) is bool
            and value["policy"] in POLICIES
            and value["schema_revision"] == REVISION
            and value["schema_state"]
            == "full032c_"
            + (
                "experimental_hash_and_canonical_reuse"
                if mode == "candidate"
                else "unmodified"
            )
            and value["migration_sql_sha256"] == PINS
            and value["catalog_properties_verified"] is True
            and value["production_acceptance"] is False
            and number(value["elapsed_seconds"])
            and value["original_body_sha256"] == expected_bodies()[0]
            and value["installed_body_sha256"] == expected_bodies()[mode == "candidate"]
        ):
            return value
    elif prefix == "QLIB_HASH_PROBES":
        if (
            set(value) == {"mode", "policy", "phase", "baseline", "rejections"}
            and value["mode"] == mode
            and value["policy"] in POLICIES
            and value["phase"] in ("first", "seeded")
            and (value["phase"] != "seeded" or value["policy"] == POLICIES[1])
            and value["baseline"] == "accepted"
            and isinstance(value["rejections"], list)
            and len(value["rejections"]) == 3
            and all(
                isinstance(r, dict)
                and set(r) == {"probe", "sqlstate"}
                and r["probe"]
                in ("missing_submission", "config_commitment", "selection_commitment")
                and r["sqlstate"] in ("P0001", "P0002", "23514")
                for r in value["rejections"]
            )
            and {r["probe"] for r in value["rejections"]}
            == {"missing_submission", "config_commitment", "selection_commitment"}
        ):
            return value
    elif prefix == "RD_PG_FUNCTIONS":
        if value.get("stage") not in ("workflow_first", "workflow_seeded"):
            return None
        if set(value) == {"stage", "status"} and value["status"] in (
            "unavailable",
            "counter_reset_or_invalid",
        ):
            return value
        if (
            set(value) != {"stage", "status", "functions"}
            or value["status"] != "sampled_natural_flush_may_lag"
            or not isinstance(value["functions"], list)
            or len(value["functions"]) > len(FUNCTIONS)
        ):
            return None
        for row in value["functions"]:
            if not isinstance(row, dict) or row.get("function") not in FUNCTIONS:
                return None
            status = row.get("status")
            keys = {"function", "status"}
            if status == "sampled":
                keys |= {"calls", "total_ms", "self_ms"}
            elif status == "baseline_missing":
                keys |= {
                    "delta",
                    "observed_calls",
                    "observed_total_ms",
                    "observed_self_ms",
                }
                if row.get("delta", 1) is not None:
                    return None
            elif status != "missing_or_untracked":
                return None
            if set(row) != keys or any(
                not number(row[k]) for k in keys - {"function", "status", "delta"}
            ):
                return None
        if len({r["function"] for r in value["functions"]}) == len(value["functions"]):
            return value
    return None


def report(mode, exit_code):
    cases = []
    xml = PRIVATE / f"hash-{mode}.xml"
    if xml.is_file() and xml.stat().st_size <= 8 * 1024 * 1024:
        for case in ET.parse(xml).getroot().iter("testcase"):
            name = case.get("name")
            # Never echo unexpected test identifiers or failure strings.
            cases.append(
                {
                    "case": name if name in CASES else "unexpected_case",
                    "seconds": float(case.get("time", "0")),
                    "outcome": next(
                        (
                            tag
                            for tag in ("failure", "error", "skipped")
                            if case.find(tag) is not None
                        ),
                        "passed",
                    ),
                }
            )
    records = {
        key: []
        for key in (
            "QLIB_HASH_EXPERIMENT",
            "QLIB_HASH_PROBES",
            "RD_PG_FUNCTIONS",
            "RD_QUEUE_FAILURE",
            "RD_RPC_TIMING",
        )
    }
    log = PRIVATE / f"hash-{mode}.log"
    if log.is_file() and log.stat().st_size <= 16 * 1024 * 1024:
        for line in log.read_text().splitlines():
            for prefix, retained in records.items():
                # Pytest progress markers can precede the diagnostic prefix.
                if prefix + ":" not in line:
                    continue
                try:
                    value = closed_record(
                        prefix, json.loads(line.split(prefix + ":", 1)[1]), mode
                    )
                except (ValueError, TypeError, KeyError):
                    value = None
                if value is not None and len(retained) < (
                    24 if prefix == "RD_QUEUE_FAILURE" else 8
                ):
                    retained.append(value)
    complete = (
        len(cases) == 2
        and {c["case"] for c in cases} == CASES
        and all(c["outcome"] != "skipped" and number(c["seconds"]) for c in cases)
        and len(records["QLIB_HASH_EXPERIMENT"]) == 2
        and {r["policy"] for r in records["QLIB_HASH_EXPERIMENT"]} == set(POLICIES)
        and 2 <= len(records["QLIB_HASH_PROBES"]) <= 3
        and {
            (r["policy"], r["phase"])
            for r in records["QLIB_HASH_PROBES"]
            if r["phase"] == "first"
        }
        == {(p, "first") for p in POLICIES}
        and len({(r["policy"], r["phase"]) for r in records["QLIB_HASH_PROBES"]})
        == len(records["QLIB_HASH_PROBES"])
    )
    passed = (
        complete and exit_code == 0 and all(c["outcome"] == "passed" for c in cases)
    )
    result = {
        "experiment_only": True,
        "production_acceptance": False,
        "mode": mode,
        "expected_cases": 2,
        "pytest_exit_code": exit_code,
        "passed": passed,
        "complete": complete,
        "cases": cases,
        "diagnostics": records,
    }
    PROOF.mkdir(parents=True, exist_ok=True)
    (PROOF / f"hash-{mode}.json").write_text(
        json.dumps(result, sort_keys=True, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "experiment_only": True,
                "mode": mode,
                "passed": passed,
                "case_count": len(cases),
            }
        )
    )
    return passed


def verify():
    values = [
        json.loads((PROOF / f"hash-{m}.json").read_text())
        for m in ("control", "candidate")
    ]
    paired = [
        {
            (r["policy"], r["phase"]): r["rejections"]
            for r in v["diagnostics"]["QLIB_HASH_PROBES"]
        }
        for v in values
    ]
    common = set(paired[0]) & set(paired[1])
    summary = {
        "experiment_only": True,
        "production_acceptance": False,
        "control_passed": values[0]["passed"],
        "candidate_passed": values[1]["passed"],
        "experiment_complete": all(v["complete"] for v in values),
        "paired_probes_equal": bool(common)
        and all(paired[0][key] == paired[1][key] for key in common),
        "paired_phases": sorted(common),
        "unpaired_phases": sorted(set(paired[0]) ^ set(paired[1])),
        "whole_graph_equivalence": "not_established",
        "timing_order": "control_then_candidate_not_randomized",
    }
    (PROOF / "hash-comparison.json").write_text(
        json.dumps(summary, sort_keys=True, indent=2) + "\n"
    )
    print(json.dumps(summary, sort_keys=True))
    # Deliberately red on failed control; retain a successful candidate separately.
    return all(v["passed"] for v in values) and summary["paired_probes_equal"]


if __name__ == "__main__":
    if sys.argv[1:] == ["verify"]:
        ok = verify()
    elif len(sys.argv) == 3 and sys.argv[1] in ("control", "candidate"):
        ok = report(sys.argv[1], int(sys.argv[2]))
    else:
        raise SystemExit("Explicit experiment mode required")
    raise SystemExit(0 if ok else 1)
