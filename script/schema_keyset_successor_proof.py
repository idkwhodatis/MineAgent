"""Exact032g proof inventory; source/PG proof is not native deadline acceptance."""

import argparse
import json
from pathlib import Path

from script.sql_candidate_proof import check_collection, check_junit, record_source

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests/structured/schema_keyset_successor_inventory.json"
SCOPE = "032g key-set successor experiment; native Qlib/OE gates remain required"


def inventory(group):
    document = json.loads(MANIFEST.read_text())
    assert set(document) == {"source", "postgres"}, "Unexpected proof groups"
    expected = document[group]
    assert expected and len(expected) == len(set(expected)), (
        "Duplicate or empty inventory"
    )
    assert all(
        isinstance(node, str) and node.startswith("tests/") and "::" in node
        for node in expected
    )
    return expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", choices=("source", "postgres"))
    parser.add_argument("--collection", type=Path)
    parser.add_argument("--junit", type=Path)
    parser.add_argument("--source-evidence", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.source_evidence:
        assert not any((args.group, args.collection, args.junit))
        result = record_source() | {"scope": SCOPE}
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
