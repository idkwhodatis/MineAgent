"""Closed failure metadata for two repository-owned benchmark commands only.

Never parse API bodies or publish raw process output, IDs, URLs or exceptions.
These diagnostics cannot turn a failed subprocess into acceptance evidence.
"""

import json
import math
import sys

PROGRAMS = {
    "script.benchmark_v1_workloads": "tradegrad.fixture-workloads.v1",
    "script.benchmark_v1_daily_reference": "tradegrad.daily-reference-performance.v1",
}
SECTIONS = {
    "script.benchmark_v1_workloads": ("import", "single_evaluation", "concurrent_pair"),
    "script.benchmark_v1_daily_reference": (
        "import",
        "standalone_configured_run",
        "single_evaluation",
        "metadata_api",
        "windowed_chart",
        "concurrent_runs",
    ),
}
# Private containers are never retained. Only the explicit numeric workload
# fields below may be projected; this is not validation of benchmark evidence.
COMMON_TYPES = {
    "schema_version": (str,),
    "status": (str,),
    "error_code": (str,),
    "scope": (str,),
    "capacity_claim": (bool,),
    "live_data": (bool,),
    "model_calls": (int, type(None)),
    "hardware": (dict,),
    "workload": (dict,),
    "bounds": (dict,),
    "created": (dict,),
    "cleanup": (dict,),
    "control_http_latency": (dict,),
    "total_wall_ms": (int, float),
    "http_requests": (int,),
}
DAILY_TYPES = {
    "profile_id": (str,),
    "user_agreed_production_profile": (bool,),
    "performance_threshold": (type(None),),
    "identity": (dict,),
}
ERROR_CODES = frozenset(
    [
        "ci_required",
        "concurrency_not_observed",
        "docker_required",
        "fixture_integrity",
        "fixture_mode_required",
        "import_failed",
        "internal_error",
        "invalid_arguments",
        "invalid_credentials",
        "invalid_execution_evidence",
        "invalid_origin",
        "invalid_request",
        "invalid_response",
        "invalid_token",
        "missing_credentials",
        "opt_in_required",
        "redirect_blocked",
        "request_budget",
        "request_too_large",
        "response_too_large",
        "run_failed",
        "seed_not_found",
        "timeout",
        "transport_error",
        "unsupported_encoding",
    ]
)


def _bounded_depth(raw):
    depth = 0
    quoted = escaped = False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > 32:
                return False
        elif byte in (93, 125):
            depth -= 1
    return True


# These bounds are diagnostic filters, NOT acceptance thresholds. Missing or
# invalid values stay absent, never zero. The workload CLI caps wall time at 600s.
NUMERIC_FIELDS = frozenset(
    {
        "attempt_overlap_ms",
        "batch_wall_ms",
        "attempt_1_duration_ms",
        "attempt_2_duration_ms",
        "submission_count",
        "submission_p50_ms",
        "submission_p95_ms",
        "submission_max_ms",
    }
)


def _number(key, value):
    if key == "submission_count":
        return type(value) is int and 0 <= value <= 3
    return type(value) in (int, float) and 0 <= value <= 600000 and math.isfinite(value)


def _workload_numbers(value):
    result = {}
    pair = value.get("concurrent_pair", {})
    for key in ("attempt_overlap_ms", "batch_wall_ms"):
        if _number(key, pair.get(key)):
            result[key] = pair[key]
    durations = pair.get("persisted_attempt_duration_ms")
    if type(durations) is list and len(durations) == 2:
        for i, number in enumerate(durations, 1):
            key = f"attempt_{i}_duration_ms"
            if _number(key, number):
                result[key] = number
    latency = value.get("control_http_latency", {}).get("run_submission")
    if type(latency) is dict:
        for suffix in ("count", "p50_ms", "p95_ms", "max_ms"):
            key = "submission_" + suffix
            if _number(key, latency.get(suffix)):
                result[key] = latency[suffix]
    return result


def validate_diagnostic(value, program):
    """Recheck only the closed safe projection before a public log emission."""
    if (
        program not in PROGRAMS
        or type(value) is not dict
        or set(value) - {"workload_numeric_evidence"}
        != {
            "program",
            "schema_version",
            "status",
            "error_code",
            "last_reported_section",
        }
        or any(
            type(item) is not str
            for key, item in value.items()
            if key != "workload_numeric_evidence"
        )
        or value["program"] != program
        or value["schema_version"] != PROGRAMS[program]
        or value["status"] != "failed"
        or value["error_code"] not in ERROR_CODES
        or value["last_reported_section"] not in (*SECTIONS[program], "none")
    ):
        return None
    result = dict(value)
    if "workload_numeric_evidence" in value:
        numbers = value["workload_numeric_evidence"]
        if (
            program != "script.benchmark_v1_workloads"
            or value["error_code"] != "concurrency_not_observed"
            or value["last_reported_section"] != "concurrent_pair"
            or type(numbers) is not dict
            or not numbers
            or not set(numbers) <= NUMERIC_FIELDS
            or any(not _number(key, item) for key, item in numbers.items())
        ):
            return None
        result["workload_numeric_evidence"] = dict(numbers)
    return result


def decode_failure(arguments, output):
    """Fail closed for two fixed programs; never retain their private report."""
    if (
        type(arguments) not in (list, tuple)
        or len(arguments) < 3
        or arguments[0] != sys.executable
        or arguments[1] != "-m"
        or type(arguments[2]) is not str
        or arguments[2] not in PROGRAMS
        or type(output) is not bytes
        or not 1 <= len(output) <= 1048576
        or not _bounded_depth(output)
    ):
        return None
    program = arguments[2]

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate key")
            result[key] = value
        return result

    def invalid(_):
        raise ValueError("Non-finite JSON")

    def finite(raw):
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError("Non-finite JSON")
        return value

    try:
        value = json.loads(
            output, object_pairs_hook=pairs, parse_constant=invalid, parse_float=finite
        )
        types = {**COMMON_TYPES, **{key: (dict,) for key in SECTIONS[program]}}
        if program == "script.benchmark_v1_daily_reference":
            types.update(DAILY_TYPES)
        if (
            type(value) is not dict
            or not {"schema_version", "status", "error_code"} <= set(value)
            or not set(value) <= set(types)
            or any(type(item) not in types[key] for key, item in value.items())
            or value["schema_version"] != PROGRAMS[program]
            or value["status"] != "failed"
            or value["error_code"] not in ERROR_CODES
        ):
            return None
        result = {
            "program": program,
            "schema_version": PROGRAMS[program],
            "status": "failed",
            "error_code": value["error_code"],
            "last_reported_section": next(
                (key for key in reversed(SECTIONS[program]) if key in value), "none"
            ),
        }
        if (
            program == "script.benchmark_v1_workloads"
            and value["error_code"] == "concurrency_not_observed"
            and result["last_reported_section"] == "concurrent_pair"
        ):
            numbers = _workload_numbers(value)
            if numbers:
                result["workload_numeric_evidence"] = numbers
        return validate_diagnostic(result, program)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return None
