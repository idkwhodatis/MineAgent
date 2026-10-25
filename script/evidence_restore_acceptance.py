"""Strict outcome-only admission for the isolated completed-evidence restore gate."""

import argparse
from pathlib import Path
from xml.etree import ElementTree as ET

from script.summarize_ci_junit import summarize

REQUIRED_CASE = (
    "tests.system.test_research_evidence_restore",
    "test_real_completed_research_evidence_cold_restore_reopens_exact_http",
)
MAX_BYTES = 20_000_000


def report_root(path: Path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError("Fresh bounded restore report required")
    raw = path.read_bytes()
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise ValueError("Unsupported report framing")
    return ET.fromstring(raw)


def verify_report(path: Path, exit_code: int) -> None:
    root = report_root(path)
    suites = root.findall("testsuite")
    cases = list(root.iter("testcase"))
    if (
        exit_code != 0
        or root.tag != "testsuites"
        or len(suites) != 1
        or len(list(root)) != 1
        or len(cases) != 1
        or (cases[0].get("classname"), cases[0].get("name")) != REQUIRED_CASE
        or any(
            next(root.iter(tag), None) is not None
            for tag in ("skipped", "failure", "error")
        )
        or any(
            suites[0].get(key) != value
            for key, value in {
                "tests": "1",
                "failures": "0",
                "errors": "0",
                "skipped": "0",
            }.items()
        )
    ):
        raise ValueError("Exact completed-evidence restore must execute and pass")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--exit-code", type=int, required=True)
    args = parser.parse_args()
    destination = Path("var/ci-evidence/completed-evidence-restore-junit.xml")
    try:
        if destination.exists() or destination.is_symlink():
            raise ValueError("Stale evidence destination")
        report_root(args.report)
        summarize(args.report, destination)
        verify_report(args.report, args.exit_code)
    except Exception:  # noqa: BLE001 - never disclose private report or process payloads
        print(
            "Completed-evidence restore acceptance failed; inspect private runner evidence"
        )
        return 1
    print(
        "Exact completed-evidence cold restore passed with successful fixture teardown"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
