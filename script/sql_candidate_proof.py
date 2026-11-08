"""Exact candidate-only CI inventory/evidence; never installs SQL or promotes it."""

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests/structured/sql_candidate_inventory.json"
SCOPE = "isolated SQL candidate proof, not production or native deadline acceptance"


def inventory(group):
    value = json.loads(MANIFEST.read_text())[group]
    assert value and len(value) == len(set(value)), "Duplicate or empty inventory"
    return value


def check_collection(expected, source):
    actual = [
        line
        for line in source.splitlines()
        if line.startswith("tests/") and "::" in line
    ]
    assert Counter(actual) == Counter(expected), (
        "Candidate collection differs from exact named inventory"
    )


def check_junit(expected, source):
    root = ET.fromstring(source)
    cases = list(root.iter("testcase"))
    identities = [
        (case.attrib.get("classname"), case.attrib.get("name")) for case in cases
    ]
    wanted = [
        (node.split("::")[0].removesuffix(".py").replace("/", "."), node.split("::")[1])
        for node in expected
    ]
    assert Counter(identities) == Counter(wanted), (
        "Candidate JUnit named inventory differs"
    )
    assert root.find(".//failure") is None, "Candidate failure"
    assert root.find(".//error") is None, "Candidate error"
    assert root.find(".//skipped") is None, "Candidate skip"
    for suite in root.iter("testsuite"):
        assert all(
            int(suite.get(field, "0")) == 0
            for field in ("failures", "errors", "skipped")
        )
    # Export only allowlisted names/outcomes and bounded numeric durations, never
    # captured stdout, assertion text, environment values or arbitrary XML data.
    result = []
    by_identity = dict(zip(identities, cases, strict=True))
    for node, identity in zip(expected, wanted, strict=True):
        duration = by_identity[identity].get("time", "")
        assert re.fullmatch(r"[0-9]{1,8}(?:\.[0-9]{1,9})?", duration)
        result.append({"node": node, "outcome": "passed", "seconds": duration})
    return result


# Only these fixed synthetic-component measurements may enter public CI evidence.
COMPONENT_TABLES = {
    "oe_structured_control_states",
    "oe_structured_candidates",
    "oe_structured_executions",
    "oe_structured_engine_outcomes",
    "oe_structured_epochs",
}


def export_component_measurements(component, samples):
    assert component in {"minimal", "expanded"}, "Unknown component"
    assert isinstance(samples, list) and len(samples) == 6, "Expected six samples"
    common = {"phase", "sample", "write_ms"}
    fields = common | (
        {"rows_written_per_table", "fetched_spec_rows", "same_spec_read_ms"}
        if component == "minimal"
        else {"rows_per_table", "checked_fetches", "read_ms"}
    )
    for index, row in enumerate(samples):
        assert isinstance(row, dict) and set(row) == fields, (
            "Unexpected measurement fields"
        )
        assert row["phase"] == ("before" if index < 3 else "after")
        assert type(row["sample"]) is int and row["sample"] == index % 3
        for key in (
            "write_ms",
            "same_spec_read_ms" if component == "minimal" else "read_ms",
        ):
            value = row[key]
            assert (
                type(value) in (int, float) and math.isfinite(value) and value >= 0
            ), "Nonfinite or nonnumeric timing"
        if component == "minimal":
            for key, expected in (
                ("rows_written_per_table", 8),
                ("fetched_spec_rows", 64),
            ):
                assert type(row[key]) is int and row[key] == expected
        else:
            assert type(row["rows_per_table"]) is int and row["rows_per_table"] == 4
            assert (
                isinstance(row["checked_fetches"], dict)
                and set(row["checked_fetches"]) == COMPONENT_TABLES
            )
            assert all(
                type(value) is int and value == 32
                for value in row["checked_fetches"].values()
            )
    report = {
        "scope": "synthetic row-local component timings, not native or deadline acceptance",
        "component": component,
        "samples": samples,
    }
    data = (
        json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode()
    assert len(data) <= 16384, "Measurement report exceeds 16 KiB"
    directory = os.environ.get("MINEAGENT_SQL_CANDIDATE_MEASUREMENTS_DIR")
    if directory:
        path = Path(directory)
        assert path.resolve() == (Path.cwd() / "var/sql-candidate-proof").resolve()
        path.mkdir(parents=True, exist_ok=True)
        assert not path.is_symlink()
        with (path / f"{component}-component-measurements.json").open("xb") as stream:
            stream.write(data)
    return report


def record_source():
    def git(*args):
        return subprocess.check_output(
            ["git", *args], cwd=ROOT, text=True, timeout=20
        ).strip()

    commit = git("rev-parse", "HEAD")
    assert commit == os.environ["GITHUB_SHA"], "Checkout is not the requested CI commit"
    assert not git("status", "--porcelain", "--untracked-files=no"), (
        "Tracked source is dirty"
    )
    files = git("ls-files").splitlines()
    hashes = {}
    for name in files:
        path = ROOT / name
        assert path.is_file() and not path.is_symlink(), (
            "Source is not an ordinary file"
        )
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "scope": SCOPE,
        "commit": commit,
        "tree": git("rev-parse", "HEAD^{tree}"),
        "sha256": hashes,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", choices=("source", "keyset", "rows"))
    parser.add_argument("--collection", type=Path)
    parser.add_argument("--junit", type=Path)
    parser.add_argument("--source-evidence", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.source_evidence:
        assert not any((args.group, args.collection, args.junit))
        result = record_source()
    else:
        assert args.group and args.collection and args.junit
        expected = inventory(args.group)
        check_collection(expected, args.collection.read_text())
        result = {
            "scope": SCOPE,
            "group": args.group,
            "cases": check_junit(expected, args.junit.read_bytes()),
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
