"""Project one private pytest JUnit file; never echo parsed text on error."""

import argparse
import json
import math
import os
import re
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

CASE = (
    "test_normal_workflow_children_consume_frozen_inputs_and_real_numeric_results"
    "[explicit-opt-in-two-children]"
)
CLASS = "tests.integration.test_rdagent_workflow_user_flow"
PREFIX = "RD_QLIB_COST_V1:"
CHILDREN = {"first", "seeded"}
CALLS = {"generation_boundary": {75}, "submit_chat": {12, 31, 44, 55}}
CHAT_OBJECTS = {
    "request",
    "response",
    "receipt_intent",
    "receipt_started",
    "receipt_stopped",
}
GEN_OBJECTS = {6: "proposal", 8: "proposal", 9: "adoption", 11: "adoption"}
STAGES = {"reserve_object", "finalize_object"}
ARMS = {"baseline", "ascii_reuse"}
SHA = re.compile(r"[a-f0-9]{40}")
MAX_BYTES = 20_000_000
BEFORE_SOURCE_PHASES = {
    "setup",
    "arm_configuration",
    "database_identity",
    "pinned_baseline",
    "graph_body_edit",
    "source_attestation",
}
AFTER_SOURCE_PHASES = {"fixture_before_restore", "cold_restore", "restore_attestation"}
PHASES = BEFORE_SOURCE_PHASES | AFTER_SOURCE_PHASES | {"post_restore", "accepted"}


def require(ok):
    if not ok:
        raise ValueError("Unavailable Qlib A/B evidence")


def validate_checkout(expected):
    require(SHA.fullmatch(expected) and expected == os.environ.get("GITHUB_SHA"))
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    )
    require(result.stdout.strip() == expected)
    # Untracked ignored build artifacts are not part of the measured checkout.
    require(
        subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )


def _number(value):
    return (
        type(value) in {float, int} and math.isfinite(value) and 0 <= value <= 3_600_000
    )


def _project_rows(content):
    rows = {}
    seen = set()
    for line in content.splitlines():
        if not line.startswith(PREFIX):
            continue
        require(len(line) <= 300_000)
        report = json.loads(line.removeprefix(PREFIX))
        require(isinstance(report, dict))
        method, child, rpc = (report.get(k) for k in ("method", "child", "rpc_ordinal"))
        if method not in CALLS or type(rpc) is not int or rpc not in CALLS[method]:
            continue
        require(child in CHILDREN and report.get("policy") == "explicit_opt_in")
        key = (child, method, rpc)
        require(key not in seen and report.get("inclusive_overlapping") is True)
        seen.add(key)
        # A truncated report does not prove which matching stages were observed.
        if report.get("overflow") is True:
            continue
        require(report.get("overflow") is False)
        transactions = report.get("transactions")
        require(isinstance(transactions, list) and len(transactions) <= 64)
        visited = set()
        for transaction in transactions:
            require(isinstance(transaction, dict))
            ordinal = transaction.get("ordinal")
            require(
                type(ordinal) is int and 1 <= ordinal <= 64 and ordinal not in visited
            )
            visited.add(ordinal)
            publication = transaction.get("publication")
            if not (
                (
                    method == "generation_boundary"
                    and GEN_OBJECTS.get(ordinal) == publication
                    and ordinal in GEN_OBJECTS
                )
                or (method == "submit_chat" and publication in CHAT_OBJECTS)
            ):
                continue
            require(transaction.get("resource_scope") == "objects")
            stage = transaction.get("publication_stage")
            completion = transaction.get("completion")
            if stage is None or completion != "commit":
                continue
            require(stage in STAGES)
            components = transaction.get("components")
            require(isinstance(components, list) and len(components) <= 32)
            observed = [
                c
                for c in components
                if isinstance(c, dict) and c.get("component") == "dbapi_commit"
            ]
            if not observed:
                continue
            require(len(observed) == 1)
            component = observed[0]
            count, failures = component.get("count"), component.get("failures")
            total, maximum = component.get("total_ms"), component.get("max_ms")
            require(
                type(count) is int
                and count == 1
                and type(failures) is int
                and failures in (0, 1)
            )
            require(_number(total) and _number(maximum) and total == maximum)
            if failures:
                continue
            rows[(*key, ordinal)] = {
                "child": child,
                "method": method,
                "rpc": rpc,
                "transaction": ordinal,
                "publication": publication,
                "stage": stage,
                "dbapi_count": count,
                "dbapi_ms": total,
            }
    return [rows[k] for k in sorted(rows)]


def project(path, arm, revision):
    require(arm in ARMS and SHA.fullmatch(revision))
    require(
        not path.is_symlink() and path.is_file() and path.stat().st_size <= MAX_BYTES
    )
    payload = path.read_bytes()
    require(
        len(payload) <= MAX_BYTES
        and b"<!DOCTYPE" not in payload
        and b"<!ENTITY" not in payload
    )
    cases = list(ET.fromstring(payload).iter("testcase"))
    require(len(cases) == 1)
    case = cases[0]
    require(case.get("name") == CASE and case.get("classname") == CLASS)
    failures = [k for k in ("error", "failure", "skipped") if case.find(k) is not None]
    status = failures[0] if failures else "passed"
    props = {}
    phase = None
    for prop in case.findall("./properties/property"):
        key = prop.get("name")
        if key in {"qlib_ascii_ab_source", "qlib_ascii_ab_restore"}:
            require(key not in props and prop.get("value") == arm)
            props[key] = True
        elif key == "qlib_ascii_ab_phase":
            require(phase is None and prop.get("value") in PHASES - {"accepted"})
            phase = prop.get("value")
    phase = phase or "setup"
    source = props.get("qlib_ascii_ab_source", False)
    restored = props.get("qlib_ascii_ab_restore", False)
    require(not restored or source)
    require((phase in BEFORE_SOURCE_PHASES) == (not source))
    require((phase == "post_restore") == restored)
    require(phase != "graph_body_edit" or arm == "ascii_reuse")
    require(status != "passed" or (source and restored and phase == "post_restore"))
    if status == "passed":
        phase = "accepted"
    output = case.findall("system-out")
    require(len(output) <= 1)
    rows = _project_rows(output[0].text or "") if source and output else []
    return {
        "schema": "QLIB_ASCII_AB_V1",
        "revision": revision,
        "arm": arm,
        "acceptance": status,
        "phase": phase,
        "source_attested": source,
        "restore_attested": restored,
        "commits": rows,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--arm", choices=sorted(ARMS), required=True)
    parser.add_argument("--checkout", required=True)
    args = parser.parse_args()
    try:
        validate_checkout(args.checkout)
        result = project(args.junit, args.arm, args.checkout)
    except (
        ValueError,
        ET.ParseError,
        OSError,
        subprocess.SubprocessError,
        TypeError,
        OverflowError,
    ):
        print("QLIB_ASCII_AB_V1 unavailable")
        return 1
    print(json.dumps(result, sort_keys=True, allow_nan=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
