"""Workflow/pair contracts. No runtime or runner calls."""

import json
from pathlib import Path

import pytest

from tests.sql_probes import qlib_ab_pair as pair

REV = "a" * 40


def arm(root, key, *, accept="passed", nudge=0, missing=False, wrong_stage=False):
    rows = []
    for child, ordinal in sorted(pair.SLOTS):
        publication, stage = pair.ATTACHED_STAGES[ordinal]
        rows.append(
            {
                "child": child,
                "method": "generation_boundary",
                "rpc": 75,
                "transaction": ordinal,
                "publication": publication,
                "stage": stage,
                "dbapi_count": 1,
                "dbapi_ms": ordinal + nudge,
            }
        )
    if missing:
        rows.pop()
    if wrong_stage:
        rows[0]["stage"] = "finalize_object"
    (root / f"{key}.status").write_text("1\n" if accept == "failure" else "0\n")
    (root / f"{key}-closed.json").write_text(
        json.dumps(
            {
                "schema": "QLIB_ASCII_AB_V1",
                "revision": REV,
                "arm": key,
                "acceptance": accept,
                "phase": "accepted" if accept == "passed" else "fixture_before_restore",
                "source_attested": True,
                "restore_attested": accept == "passed",
                "commits": rows,
            }
        )
    )


def test_complete_generation_pair_only(tmp_path):
    arm(tmp_path, "baseline")
    arm(tmp_path, "ascii_reuse", nudge=2)
    result = pair.compare(tmp_path, REV)
    assert result["comparison"] == "generation_commit_slots"
    assert len(result["slots"]) == 8
    assert {r["child"] for r in result["slots"]} == {"first", "seeded"}
    assert all(
        r["ascii_reuse_dbapi_ms"] - r["baseline_dbapi_ms"] == 2 for r in result["slots"]
    )


@pytest.mark.parametrize("case", [{"accept": "failure"}, {"missing": True}])
def test_incomplete_pair_does_not_publish_any_timing(tmp_path, case):
    arm(tmp_path, "baseline")
    arm(tmp_path, "ascii_reuse", **case)
    result = pair.compare(tmp_path, REV)
    assert result["comparison"] == "unavailable" and result["slots"] == []
    assert result["baseline_phase"] == "accepted"
    assert result["ascii_reuse_phase"] == (
        "fixture_before_restore" if "accept" in case else "accepted"
    )


def test_mismatched_slot_and_checkout_rejected(tmp_path):
    arm(tmp_path, "baseline")
    arm(tmp_path, "ascii_reuse", wrong_stage=True)
    with pytest.raises(ValueError):
        pair.compare(tmp_path, REV)
    with pytest.raises(ValueError):
        pair.compare(tmp_path, "b" * 40)


def test_branch_only_pair_copies_frozen_early_setup_and_stops_before_release():
    root = Path(".github/workflows")
    workflow = (root / "mineagent-qlib-ascii-ab-diagnostic.yml").read_text()
    original = (root / "mineagent-configured-analysis.yml").read_text()
    assert (
        original.split("    env:\n", 1)[1].split(
            "      - name: Generate masked disposable database credentials", 1
        )[0]
        == workflow.split("    env:\n", 1)[1].split(
            "      - name: Confirm exact numeric units", 1
        )[0]
    )
    assert "branches: [chatgpt-dot/qlib-ascii-ab-20261122]" in workflow
    assert "contents: read" in workflow
    for token in (
        "workflow_dispatch:",
        "pull_request:",
        "schedule:",
        "secrets.",
        "upload-artifact",
        "Require installed workflow predicates",
        "Require actual configured results browser",
        "Require exact key-set successor",
    ):
        assert token not in workflow
    assert workflow.count("::test_normal_workflow_children_consume_") == 2
    assert workflow.count("|| status=$?") >= 2
    assert workflow.count("junit_logging=system-out") == 2
    assert workflow.count("2>&1 || status=$?") == 2
    assert workflow.count("python -m tests.sql_probes.qlib_ab_evidence") == 2
    assert workflow.index("AB_ARM: baseline") < workflow.index("AB_ARM: ascii_reuse")
    assert workflow.count("postgres:18.6 -c track_functions=pl") == 2
    assert "Require clean inter-arm boundary" in workflow
    assert workflow.count("Confirm exact fresh PostgreSQL identity for") == 2
    assert 'test "$baseline_cid" != "$cid"' in workflow
    assert 'test "$baseline_image" = "$image"' in workflow
    assert (
        workflow.index("id: baseline_pg_cleanup")
        < workflow.index("Require clean inter-arm boundary")
        < workflow.index("Confirm exact fresh PostgreSQL identity for ascii_reuse")
        < workflow.index("id: treatment_case")
        < workflow.index("id: treatment_pg_cleanup")
        < workflow.index("Publish only matched complete native generation timings")
    )
    assert workflow.count('docker rm --force --volumes "$id"') == 2
    for marker in ("numeric.admission", "analysis.admission", "orchestrator-v2"):
        assert workflow.count(marker) == 3
