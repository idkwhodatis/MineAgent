"""Private JUnit projection for the branch-only production heartbeat proof."""

import json
from argparse import ArgumentParser
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

ROOT = Path("var/numeric-private")
CASES = {
    "heartbeat": (
        "test_workflow_heartbeat_production",
        "test_actual_workflow_heartbeat_waits_and_times_out_without_renewal",
    ),
    "bridge": (
        "test_workflow_current_head_baseline",
        "test_actual_current032g_workflow_completes_with_readonly_graph_checks",
    ),
    "restore": (
        "test_workflow_structured_stage_flow",
        "test_actual_final_rd_checkpoint_drives_one_oe_stage_and_exact031_restore",
    ),
}
_COORDINATION_CLASS = "tests.integration.test_workflow_heartbeat_rpc_coordination"
_COORDINATION_TEST = "test_exact_workflow_rpc_coordinates_with_heartbeat"
_COORDINATION_CASES = {
    "heartbeat-commits-first": "heartbeat_commits_first",
    "rpc-commits-first": "rpc_commits_first",
    "rpc-rolls-back-first": "rpc_rolls_back_first",
}
# Source anchors are exact statements in the unchanged integration helpers.
# Only a known source frame and the last crossed statement determine a phase.
BRIDGE = {
    "wid = create_workflow(env, browser_engine=engine, browser_tmp_path=tmp_path)": "source_browser",
    "with diagnose_first_rd_drain(env.factory, engine, wid):": "rd_drain",
    "checkpoint = session.get(ResearchWorkflowCheckpoint, root.latest_checkpoint_id)": "rd_checkpoint_readback",
    'for path in (f"execution-snapshots/{rd.snapshot_id}", f"analyses/{rd.analysis_id}"):': "rd_result_release",
    "drain(env.factory, env.settings)": "oe_drain",
    "with historical_session(env.factory, owner, env.settings) as session:": "oe_terminal",
}
RESTORE = {
    "assert_restore(drill_databases, tmp_path, source, env, completed)": "historical_restore",
    "assert_unavailable_before032(env, completed)": "note_upgrade",
    "assert_notes_restore(": "note_restore",
    'legacy._upgrade(source, "032f_shared_validation_cost")': "keyset_upgrade",
    "assert_upgrade_and_restore(": "keyset_restore",
}
PHASES = frozenset(
    {
        "setup",
        "teardown",
        "heartbeat",
        "before_bridge",
        "bridge",
        "current_graph_readback",
        "rd_waiting_release",
        *BRIDGE.values(),
        *RESTORE.values(),
    }
)
CODES = frozenset(
    {
        "workflow_owner_busy",
        "neutral_busy",
        "provider_outcome_unknown",
        "lease_fence_lost",
        "deadline_exceeded",
    }
)


_DRAIN_PHASES = {"rd_drain", "rd_waiting_release", "rd_checkpoint_readback"}
_ROLES = (
    "rd_orchestrator",
    "factor_cutoff_candidate",
    "factor_normalizer",
    "native_evaluator",
)
_UNIT_STATES = frozenset(
    {"launch_intended", "uncertain", "running", "stopped", "unknown"}
)
_DRAIN_FIELDS = {
    "first_lock_scope": {"owner", "resource", "unobserved"},
    "first_holder": {"workflow_heartbeat", "other", "unstable", "unobserved"},
    "first_caller": {
        "recovery",
        "stop_callback",
        "rpc_auth",
        "rpc_dispatch",
        "admission_callback",
        "controller",
        "other",
    },
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
            "file_missing",
            "permission",
            "os",
            "other",
            "orm",
            "database",
            "unknown",
        ]
    ),
    "code": frozenset(
        CODES
        | {
            "unknown",
            "deadline",
            "neutral_transaction_lost",
            "attempt_fenced",
            "rd_controller_deadline",
            "rd_controller_incomplete_generation",
            "rd_controller_original_deadline_completion_missing",
            "rd_controller_incomplete_or_failed_reports",
            "rd_controller_daemon_stop_uncertain",
            "rd_provider_containment_unknown",
            "provider_timeout",
            "provider_containment_unconfirmed",
            "provider_input_rejected",
            "provider_admission_unconfirmed",
            "numeric_process_command_deadline",
            "numeric_process_uncertain",
            "analysis_process_deadline",
            "analysis_process_original_deadline",
            "analysis_process_uncertain",
            "analysis_process_output_rejected",
        }
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
        [
            "none",
            "other",
            "23503",
            "23505",
            "23514",
            "40001",
            "40P01",
            "55P03",
            "57014",
            "P0001",
        ]
    ),
    "run": frozenset(
        [
            "queued",
            "running",
            "succeeded",
            "failed",
            "cancelled",
            "interrupted",
            "paused",
            "cancel_requested",
            "unknown",
            "diagnostic_unavailable",
        ]
    ),
    "rpc_method": frozenset(
        {
            "unknown",
            "check_control",
            "begin_step",
            "begin_exchange",
            "end_exchange",
            "submit_chat",
            "submit_embedding",
            "semantic_retry",
            "publish_source",
            "reject_source",
            "begin_evaluation",
            "execute_or_reuse",
            "finish_evaluation",
            "generation_boundary",
        }
    ),
    **dict.fromkeys(_ROLES, _UNIT_STATES),
}


def _emit_value(name, value):
    return (
        value if type(value) is str and value in _DRAIN_FIELDS[name] else "unavailable"
    )


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate evidence field")
        result[key] = value
    return result


def _holder_observation(stdout):
    prefix = "RD_NEUTRAL_HOLDER_V1:"
    rows = [
        line[len(prefix) :] for line in stdout.splitlines() if line.startswith(prefix)
    ]
    if len(rows) != 1 or len(rows[0]) > 300:
        return {}
    try:
        row = json.loads(rows[0], object_pairs_hook=_strict_object)
    except (ValueError, RecursionError):
        return {}
    if type(row) is not dict or row.keys() != {"lock_scope", "holder", "caller"}:
        return {}
    fields = {
        f"first_{key}": _emit_value(f"first_{key}", val) for key, val in row.items()
    }
    return fields if "unavailable" not in fields.values() else {}


def _drain_observations(stdout):
    """Only exact fixed vocabulary from the original fixture's private capture."""
    if type(stdout) is not str or len(stdout) > 4_000_000:
        return {}
    seen = {}
    prefixes = {"RD_QUEUE_FAILURE:": 8, "RD_QUEUE_STATE:": 1}
    for line in stdout.splitlines():
        prefix = next((p for p in prefixes if line.startswith(p)), None)
        if prefix is None:
            continue
        rows = seen.setdefault(prefix, [])
        if len(rows) >= prefixes[prefix] or len(line) > 16_384:
            return {}
        try:
            row = json.loads(line[len(prefix) :], object_pairs_hook=_strict_object)
        except (ValueError, RecursionError):
            return {}
        if type(row) is not dict:
            return {}
        rows.append(row)
    result = {}
    holder = _holder_observation(stdout)
    if holder:
        result.update(holder)
    failures = seen.get("RD_QUEUE_FAILURE:", [])
    if failures:
        result.update(
            {
                key: _emit_value(key, failures[0].get(key))
                for key in ("family", "code", "stage", "sqlstate", "rpc_method")
            }
        )
    states = seen.get("RD_QUEUE_STATE:", [])
    if states:
        result["run"] = _emit_value("run", states[0].get("status"))
        units = states[0].get("units")
        if (
            states[0].get("unit_overflow") is False
            and type(units) is list
            and len(units) <= 16
            and all(type(unit) is dict for unit in units)
        ):
            for role in _ROLES:
                values = [
                    _emit_value(role, unit.get("state"))
                    for unit in units
                    if unit.get("role") == role
                ]
                if values and len(set(values)) == 1:
                    result[role] = values[0]
    return {key: val for key, val in result.items() if val != "unavailable"}


def _bridge_phase(source, line):
    phase = _anchored_phase(
        source,
        line,
        BRIDGE,
        "bridge",
        occurrences={"drain(env.factory, env.settings)": 2},
    )
    if phase != "rd_drain":
        return phase
    records = [entry.strip() for entry in source.splitlines()]
    release_checks = [
        i
        for i in range(len(records) - 3)
        if records[i : i + 4]
        == [
            "assert (",
            "drive_one(env.factory, env.settings, owner_id=owner, workflow_id=wid).state",
            '== "waiting_release"',
            ")",
        ]
    ]
    if len(release_checks) == 1 and line >= release_checks[0] + 1:
        return "rd_waiting_release"
    return phase


def _drain_verdict(root, name):
    module, test = CASES[name]
    case = _case(root / f"{name}.xml", module, test)
    if case is None or not any(case.find(s) is not None for s in ("failure", "error")):
        return {}
    fields = {}
    for key, allowed in (
        ("proof_phase", _DRAIN_PHASES),
        *((f"rd_drain_{key}", allowed) for key, allowed in _DRAIN_FIELDS.items()),
    ):
        values = _properties(case, key)
        if len(values) == 1 and values[0] in allowed:
            fields[key.removeprefix("rd_drain_")] = values[0]
    return fields if "proof_phase" in fields and len(fields) > 1 else {}


def _anchored_phase(source, line, anchors, fallback, *, occurrences=None):
    records = source.splitlines()
    if not 1 <= line <= len(records):
        return fallback
    markers = {}
    for code, phase in anchors.items():
        indices = [i for i, val in enumerate(records, 1) if val.strip() == code]
        expected = (occurrences or {}).get(code, 1)
        if len(indices) != expected:
            return fallback
        markers[indices[-1]] = phase
    return next(
        (markers[key] for key in sorted(markers, reverse=True) if key <= line), fallback
    )


def _phase(item, call):
    if call.when != "call":
        return call.when
    module = Path(item.path).stem
    if module == CASES["heartbeat"][0]:
        return "heartbeat"
    for frame in reversed(call.excinfo.traceback):
        filename = Path(frame.path)
        if (
            filename.name == "workflow_structured_stage_fixture.py"
            and frame.name == "run_bridge"
        ):
            return _bridge_phase(
                str(frame.frame.code.source()),
                frame.lineno - frame.frame.code.firstlineno + 1,
            )
        if filename.name == f"{module}.py" and frame.name == item.originalname:
            source = str(frame.frame.code.source())
            line = frame.lineno - frame.frame.code.firstlineno + 1
            if module == CASES["restore"][0]:
                return _anchored_phase(source, line, RESTORE, "before_bridge")
            return _anchored_phase(
                source,
                line,
                {
                    "completed = run_bridge(env, source, tmp_path)": "bridge",
                    "call_counts = (len(env.rd_calls), len(env.oe_calls))": "current_graph_readback",
                },
                "before_bridge",
            )
    return "before_bridge"


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if report.failed and call.excinfo is not None:
        try:
            phase = _phase(item, call)
        except (OSError, TypeError, ValueError, AttributeError):
            phase = None  # Reporter faults never replace the original test failure.
        if phase in PHASES:
            item.user_properties.append(("proof_phase", phase))
        if phase in _DRAIN_PHASES and call.when == "call":
            for key, value in _drain_observations(report.capstdout).items():
                item.user_properties.append((f"rd_drain_{key}", value))
        error = call.excinfo.value
        if type(error).__module__ == "backend.app.services" and type(
            error
        ).__name__ == ("DomainError"):
            code = getattr(error, "code", None)
            if type(code) is str and code in CODES:
                item.user_properties.append(("proof_code", code))
    return report


def _properties(case, key):
    return [
        prop.get("value")
        for prop in case.findall("./properties/property")
        if prop.get("name") == key
    ]


def _read_cases(path):
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 20_000_000:
            return []
        return list(ET.fromstring(path.read_bytes()).iter("testcase"))
    except (OSError, ET.ParseError):
        return []


def _case(path, module, test):
    cases = _read_cases(path)
    if len(cases) != 1:
        return None
    case = cases[0]
    if (case.get("classname"), case.get("name")) != (
        f"tests.integration.{module}",
        test,
    ):
        return None
    return case


def coordination(root):
    expected = {
        f"{_COORDINATION_TEST}[{parameter}]": field
        for parameter, field in _COORDINATION_CASES.items()
    }
    unavailable = dict.fromkeys(expected.values(), "unavailable")
    cases = _read_cases(root / "coordination.xml")
    if len(cases) != len(expected) or any(
        case.get("classname") != _COORDINATION_CLASS for case in cases
    ):
        return unavailable
    actual = {}
    for case in cases:
        field = expected.get(case.get("name"))
        if field is None or field in actual:
            return unavailable
        statuses = [
            s for s in ("failure", "error", "skipped") if case.find(s) is not None
        ]
        if len(statuses) > 1:
            return unavailable
        actual[field] = statuses[0] if statuses else "passed"
    return {field: actual[field] for field in expected.values()}


def outcome(root, name):
    module, test = CASES[name]
    case = _case(root / f"{name}.xml", module, test)
    if case is None:
        return "unavailable", "unavailable", "unavailable"
    statuses = [s for s in ("failure", "error", "skipped") if case.find(s) is not None]
    if len(statuses) > 1:
        return "unavailable", "unavailable", "unavailable"
    status = statuses[0] if statuses else "passed"
    phase = "complete" if status == "passed" else "unavailable"
    code = "unavailable"
    if status in {"failure", "error"}:
        for key, allowed in (("proof_phase", PHASES), ("proof_code", CODES)):
            values = _properties(case, key)
            if len(values) == 1 and values[0] in allowed:
                if key == "proof_phase":
                    phase = values[0]
                else:
                    code = values[0]
    return status, phase, code


def main():
    parser = ArgumentParser()
    parser.add_argument("--require-pass", choices=(*CASES, "coordination"))
    args = parser.parse_args()
    if args.require_pass == "coordination":
        return int(any(value != "passed" for value in coordination(ROOT).values()))
    if args.require_pass:
        return int(outcome(ROOT, args.require_pass)[0] != "passed")
    outcomes = coordination(ROOT)
    fields = " ".join(f"{key}={value}" for key, value in outcomes.items())
    print(f"RD_PRODUCTION_RPC_COORDINATION_V1 {fields}")
    failed = any(value != "passed" for value in outcomes.values())
    for name in CASES:
        status, phase, code = outcome(ROOT, name)
        print(f"RD_PRODUCTION_HEARTBEAT_V1 {name}={status} phase={phase} code={code}")
        failed |= status != "passed"
        if name in {"bridge", "restore"}:
            fields = _drain_verdict(ROOT, name)
            if fields:
                detail = " ".join(f"{key}={fields[key]}" for key in sorted(fields))
                print(f"RD_PRODUCTION_RD_DRAIN_V1 case={name} {detail}")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
