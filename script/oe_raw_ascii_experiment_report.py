"""Closed OE experiment receipts; raw pytest/SQL logs stay private, never emitted."""

import ast
import json
import math
import sys
from hashlib import sha256
from pathlib import Path
from xml.etree import ElementTree as ET

PRIVATE = Path("var/numeric-private")
PROOF = Path("var/numeric-proof")
PREFIXES = (
    "OE_RAW_ASCII_EXPERIMENT_V1",
    "OE_RECEIPT_COST_V1",
    "OE_PG_FUNCTIONS_V1",
    "OE_QUEUE_FAILURE_V1",
    "OE_PROVIDER_PROGRESS_V1",
)
NORMAL = "test_normal_demo_generated_factor_and_model_child_reach_actual_engine"
REJECTION = (
    "test_normal_provider_actual_rejection_preserves_failed_feedback_without_snapshot"
)


def integer(v, low=0, high=600000000):
    return type(v) is int and low <= v <= high


def number(v):
    return type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 10**12


def known(v, choices):
    return type(v) is str and v in choices


def expected_provenance(mode):
    from tests.integration import oe_raw_ascii_experiment as d

    def digest(sql):
        return sha256(d._body(sql.replace("@SCHEMA@", '"public"')).encode()).hexdigest()

    return {
        "mode": mode,
        "revision": "032c_oe_provider_table_checks",
        "scope": "disposable_fixture_only",
        "production_acceptance": False,
        "schema_state": "full032c_experimental_raw_ascii"
        if mode == "candidate"
        else "full032c_unmodified",
        "raw_original_body_sha256": digest(d.original_sql()),
        "raw_installed_body_sha256": digest(
            d.candidate_sql() if mode == "candidate" else d.original_sql()
        ),
        "canonical_helper_body_sha256": digest(d.helper_sql()),
        "source_sha256": d.PINS,
        "plain_input_max_bytes": 1048574,
        "canonical_output_max_bytes": 1048576,
    }


def closed(prefix, v, mode):
    if type(v) is not dict:
        return None
    if prefix == PREFIXES[0]:
        # Exact expected constants/hashes; bool must not pass equality as integer.
        return (
            v
            if type(v.get("production_acceptance")) is bool
            and v == expected_provenance(mode)
            else None
        )
    if prefix == "OE_RECEIPT_COST_V1":
        if (
            set(v) != {"records", "overflow", "units", "file_times_may_overlap_body"}
            or v["units"] != "microseconds"
            or type(v["overflow"]) is not bool
            or v["file_times_may_overlap_body"] is not True
            or type(v["records"]) is not list
            or len(v["records"]) > 128
        ):
            return None
        for r in v["records"]:
            if type(r) is not dict or not known(
                r.get("phase"), ("intent", "started", "stopped", "unknown")
            ):
                return None
            op = r.get("operation")
            if known(op, ("receipt", "file_publish", "file_read")):
                if set(r) != {"phase", "operation", "total_us"} or not (
                    r["total_us"] is None or integer(r["total_us"])
                ):
                    return None
            elif known(op, ("reserve_transaction", "publication_transaction")):
                if (
                    set(r)
                    != {
                        "phase",
                        "operation",
                        "entry_us",
                        "body_us",
                        "exit_us",
                        "entered",
                    }
                    or type(r["entered"]) is not bool
                    or not all(
                        r[k] is None or integer(r[k])
                        for k in ("entry_us", "body_us", "exit_us")
                    )
                ):
                    return None
            else:
                return None
        return v
    if prefix == "OE_PG_FUNCTIONS_V1":
        from tests.integration.oe_pg_diagnostic import FUNCTIONS

        if v.get("stage") != "normal":
            return None
        if set(v) == {"stage", "status"} and known(
            v["status"], ("unavailable", "counter_reset_or_invalid")
        ):
            return v
        if (
            set(v) != {"stage", "status", "functions"}
            or v["status"] != "sampled_natural_flush_may_lag"
            or type(v["functions"]) is not list
            or len(v["functions"]) != len(FUNCTIONS)
        ):
            return None
        names = set()
        for r in v["functions"]:
            if (
                type(r) is not dict
                or not known(r.get("function"), FUNCTIONS)
                or r["function"] in names
            ):
                return None
            names.add(r["function"])
            keys = {"function", "status"}
            status = r.get("status")
            if status == "sampled":
                keys |= {"calls", "total_ms", "self_ms"}
                if not integer(r.get("calls"), high=10**12):
                    return None
            elif status == "baseline_missing":
                keys |= {
                    "delta",
                    "observed_calls",
                    "observed_total_ms",
                    "observed_self_ms",
                }
                if r.get("delta", 1) is not None or not integer(
                    r.get("observed_calls"), high=10**12
                ):
                    return None
            elif status == "unchanged_unknown":
                keys |= {"delta"}
                if r.get("delta", 1) is not None:
                    return None
            elif status != "missing_or_untracked":
                return None
            if set(r) != keys or any(
                not number(r[k]) for k in keys - {"function", "status", "delta"}
            ):
                return None
        return v
    if prefix == "OE_QUEUE_FAILURE_V1":
        # Project only fixed family/code/stage. Never retain exception metadata.
        source = Path(__file__).parents[1] / "tests/integration/oe_queue_diagnostic.py"
        constants = {}
        for n in ast.parse(source.read_text()).body:
            if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", None) in (
                "CODES",
                "STAGES",
            ):
                constants[n.targets[0].id] = ast.literal_eval(n.value)
        if (
            not {"family", "code", "stage"}
            <= set(v)
            <= {
                "family",
                "code",
                "stage",
                "category",
                "validation_count",
                "validation_overflow",
                "validation_types",
                "database",
            }
        ):
            return None
        if (
            not known(
                v["family"],
                (
                    "process",
                    "domain",
                    "provider",
                    "value",
                    "validation",
                    "type",
                    "key",
                    "attribute",
                    "timeout",
                    "other",
                    "orm",
                    "database",
                ),
            )
            or not known(v["code"], constants["CODES"] | {"unknown"})
            or not known(v["stage"], set(constants["STAGES"].values()) | {"unknown"})
        ):
            return None
        return {k: v[k] for k in ("family", "code", "stage")}
    if prefix == "OE_PROVIDER_PROGRESS_V1":
        if (
            set(v) != {"count", "overflow", "calls", "synthetic_http_count"}
            or not integer(v["count"], high=9)
            or type(v["overflow"]) is not bool
            or type(v["calls"]) is not list
            or len(v["calls"]) > 8
            or not (
                v["synthetic_http_count"] == "unknown"
                or integer(v["synthetic_http_count"], high=17)
            )
        ):
            return None
        failures = (
            "timeout",
            "broken_pipe",
            "eof",
            "os_error",
            "value",
            "type",
            "response_rejected",
            "cancelled",
            "other",
            "input_closed",
            "output_limit",
        )
        for r in v["calls"]:
            required = {"entry_remaining_ms", "wire", "validation"}
            if type(r) is not dict or not required <= set(r) <= required | {
                "wire_remaining_ms",
                "wire_elapsed_ms",
                "wire_failure",
                "envelope",
                "validation_failure",
            }:
                return None
            for k, x in r.items():
                if k.endswith("_ms"):
                    if not (
                        x is None
                        or x == "unknown"
                        or integer(
                            x, low=0 if k == "wire_elapsed_ms" else -600000, high=600000
                        )
                    ):
                        return None
                elif not known(
                    x,
                    {
                        "wire": ("not_entered", "entered", "failed", "returned"),
                        "validation": ("not_entered", "entered", "failed", "accepted"),
                        "wire_failure": failures,
                        "validation_failure": failures,
                        "envelope": (
                            "success_envelope",
                            "child_rejected",
                            "empty",
                            "invalid",
                            "other",
                        ),
                    }[k],
                ):
                    return None
        return v
    return None


def unique_pairs(pairs):
    value = {}
    for key, entry in pairs:
        if key in value:
            raise ValueError("Duplicate diagnostic key")
        value[key] = entry
    return value


def report(mode, exit_code):
    if mode not in ("control", "candidate") or not (
        exit_code is None or integer(exit_code, high=255)
    ):
        raise ValueError("Closed experiment arguments required")
    records = {k: [] for k in PREFIXES}
    invalid = 0
    path = PRIVATE / f"normal-027-{mode}.log"
    if path.is_file() and path.stat().st_size <= 16 * 1024 * 1024:
        for line in path.read_text(errors="replace").splitlines():
            if len(line) > 65536:
                invalid = min(10000, invalid + 1)
                continue
            for prefix in PREFIXES:
                if prefix + ":" not in line:
                    continue
                if len(records[prefix]) >= 8:
                    invalid = min(10000, invalid + 1)
                    continue
                try:
                    parsed = json.loads(
                        line.split(prefix + ":", 1)[1], object_pairs_hook=unique_pairs
                    )
                    value = closed(prefix, parsed, mode)
                except Exception:  # noqa: BLE001 -- never render parse/error data
                    value = None
                if value is None:
                    invalid = min(10000, invalid + 1)
                else:
                    records[prefix].append(value)
    cases = []
    xml = PRIVATE / f"normal-027-{mode}.xml"
    try:
        if xml.is_file() and xml.stat().st_size <= 8 * 1024 * 1024:
            for case in list(ET.parse(xml).getroot().iter("testcase"))[:3]:
                name = case.get("name")
                seconds = float(case.get("time", "nan"))
                cases.append(
                    {
                        "case": name
                        if name in (NORMAL, REJECTION)
                        else "unexpected_case",
                        "seconds": seconds if number(seconds) else None,
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
    except Exception:  # noqa: BLE001 -- unknown XML is not a fabricated pass
        cases = []
    result = {
        "mode": mode,
        "production_acceptance": False,
        "exit_code": exit_code,
        "cases": cases,
        "case_inventory": "exact"
        if len(cases) == (2 if mode == "control" else 1)
        and {c["case"] for c in cases}
        == ({NORMAL, REJECTION} if mode == "control" else {NORMAL})
        else "unavailable_or_unexpected",
        "records": records,
        "invalid_records": invalid,
    }
    mandatory = (PREFIXES[0], PREFIXES[1], PREFIXES[2], "OE_PROVIDER_PROGRESS_V1")
    result["complete"] = (
        result["case_inventory"] == "exact"
        and integer(exit_code, high=255)
        and invalid == 0
        and all(number(case["seconds"]) for case in cases)
        and all(len(records[prefix]) == 1 for prefix in mandatory)
    )
    result["passed"] = (
        result["complete"]
        and exit_code == 0
        and all(case["outcome"] == "passed" for case in cases)
    )
    PROOF.mkdir(parents=True, exist_ok=True)
    (PROOF / f"oe-raw-{mode}.json").write_text(
        json.dumps(result, sort_keys=True) + "\n"
    )
    print("OE_RAW_EXPERIMENT_RECEIPT:" + json.dumps(result, sort_keys=True))
    return result


def comparison():
    # Reconstruct from private sources, never trust an existing public artifact.
    results = {}
    for mode in ("control", "candidate"):
        path = PRIVATE / f"normal-027-{mode}.exit"
        try:
            code = int(path.read_text())
        except Exception:  # noqa: BLE001 -- absence stays unknown
            code = None
        results[mode] = report(mode, code if integer(code, high=255) else None)
    result = {
        "production_acceptance": False,
        "variants": results,
        "complete": all(v["complete"] for v in results.values()),
        "passed": all(v["passed"] for v in results.values()),
    }
    (PROOF / "oe-raw-comparison.json").write_text(
        json.dumps(result, sort_keys=True) + "\n"
    )
    return result


if __name__ == "__main__":
    sys.exit(0 if comparison()["passed"] else 1)
