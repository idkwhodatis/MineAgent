"""Private JUnit projection for the branch-only production heartbeat proof."""

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
# Source anchors are exact statements in the unchanged integration helpers.
# Only a known source frame and the last crossed statement determine a phase.
BRIDGE = {
    "wid = create_workflow(env, browser_engine=engine, browser_tmp_path=tmp_path)": "source_browser",
    "with diagnose_first_rd_drain(env.factory, engine, wid):": "rd_drain",
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
            return _anchored_phase(
                str(frame.frame.code.source()),
                frame.lineno - frame.frame.code.firstlineno + 1,
                BRIDGE,
                "bridge",
                occurrences={"drain(env.factory, env.settings)": 2},
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
        error = call.excinfo.value
        if type(error).__module__ == "backend.app.services" and type(
            error
        ).__name__ == ("DomainError"):
            code = getattr(error, "code", None)
            if type(code) is str and code in CODES:
                item.user_properties.append(("proof_code", code))
    return report


def outcome(root, name):
    module, test = CASES[name]
    path = root / f"{name}.xml"
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 20_000_000:
            return "unavailable", "unavailable", "unavailable"
        cases = list(ET.fromstring(path.read_bytes()).iter("testcase"))
    except (OSError, ET.ParseError):
        return "unavailable", "unavailable", "unavailable"
    if len(cases) != 1:
        return "unavailable", "unavailable", "unavailable"
    case = cases[0]
    if (case.get("classname"), case.get("name")) != (
        f"tests.integration.{module}",
        test,
    ):
        return "unavailable", "unavailable", "unavailable"
    statuses = [s for s in ("failure", "error", "skipped") if case.find(s) is not None]
    if len(statuses) > 1:
        return "unavailable", "unavailable", "unavailable"
    status = statuses[0] if statuses else "passed"
    phase = "complete" if status == "passed" else "unavailable"
    code = "unavailable"
    if status in {"failure", "error"}:
        for key, allowed in (("proof_phase", PHASES), ("proof_code", CODES)):
            values = [
                prop.get("value")
                for prop in case.findall("./properties/property")
                if prop.get("name") == key
            ]
            if len(values) == 1 and values[0] in allowed:
                if key == "proof_phase":
                    phase = values[0]
                else:
                    code = values[0]
    return status, phase, code


def main():
    parser = ArgumentParser()
    parser.add_argument("--require-pass", choices=CASES)
    args = parser.parse_args()
    if args.require_pass:
        return int(outcome(ROOT, args.require_pass)[0] != "passed")
    failed = False
    for name in CASES:
        status, phase, code = outcome(ROOT, name)
        print(f"RD_PRODUCTION_HEARTBEAT_V1 {name}={status} phase={phase} code={code}")
        failed |= status != "passed"
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
