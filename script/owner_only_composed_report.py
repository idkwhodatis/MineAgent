"""Closed outcome-only evidence from the isolated owner-only diagnostic."""

from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path("var/numeric-private")
CLASS = "tests.integration.test_workflow_owner_only_composed"


def _case(path, name):
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 20_000_000:
            return None
        cases = list(ET.fromstring(path.read_bytes()).iter("testcase"))
    except (OSError, ET.ParseError):
        return None
    if len(cases) != 1:
        return None
    case = cases[0]
    if (case.get("classname"), case.get("name")) != (CLASS, name):
        return None
    return case


def _properties(case):
    return [
        (item.get("name"), item.get("value"))
        for item in case.findall("./properties/property")
    ]


def _outcome(case, *, require_restore=False):
    if case is None:
        return "unavailable"
    for status in ("failure", "error", "skipped"):
        if case.find(status) is not None:
            return status
    if require_restore:
        evidence = _properties(case)
        if len(evidence) != 2 or set(evidence) != {
            ("owner_only_bridge", "passed"),
            ("owner_only_cold_restore", "passed"),
        }:
            return "unavailable"
    return "passed"


def outcome(path, name, *, require_restore=False):
    return _outcome(_case(path, name), require_restore=require_restore)


def _phase(case, verdict, *, forward):
    if verdict == "passed":
        return "complete"
    if case is None:
        return "blocked_by_forward" if forward != "passed" else "unavailable"
    if verdict != "failure":
        return "unavailable"
    match _properties(case):
        case []:
            return "before_bridge_verified"
        case [("owner_only_bridge", "passed")]:
            return "before_restore_verified"
        case [("owner_only_bridge", "passed"), ("owner_only_cold_restore", "passed")]:
            return "after_restore"
        case _:
            return "unavailable"


def main():
    forward = outcome(
        ROOT / "owner-only-forward.xml", "test_owner_only_forward_lock_and_expiry"
    )
    composed_case = _case(
        ROOT / "owner-only-composed.xml",
        "test_actual_current032g_owner_only_workflow_and_cold_restore",
    )
    composed = _outcome(composed_case, require_restore=True)
    print(f"RD_OWNER_ONLY_COMPOSED_V1 forward={forward} composed={composed}")
    print(
        "RD_OWNER_ONLY_COMPOSED_PHASE_V1 "
        + _phase(composed_case, composed, forward=forward)
    )
    return 0 if forward == composed == "passed" else 1
