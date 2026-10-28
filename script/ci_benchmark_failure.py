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
# Only top-level shape is checked. These private containers are NEVER retained
# or traversed by the projection; this is not validation of benchmark evidence.
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


def validate_diagnostic(value, program):
    """Recheck only the closed safe projection before a public log emission."""
    if (
        program not in PROGRAMS
        or type(value) is not dict
        or set(value)
        != {
            "program",
            "schema_version",
            "status",
            "error_code",
            "last_reported_section",
        }
        or any(type(item) is not str for item in value.values())
        or value["program"] != program
        or value["schema_version"] != PROGRAMS[program]
        or value["status"] != "failed"
        or value["error_code"] not in ERROR_CODES
        or value["last_reported_section"] not in (*SECTIONS[program], "none")
    ):
        return None
    return dict(value)


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
        return {
            "program": program,
            "schema_version": PROGRAMS[program],
            "status": "failed",
            "error_code": value["error_code"],
            "last_reported_section": next(
                (key for key in reversed(SECTIONS[program]) if key in value), "none"
            ),
        }
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return None
