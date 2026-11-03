"""Closed stdout/artifact boundaries, markers, caps and missing-data honesty."""

import json
from pathlib import Path

import pytest

from script import oe_raw_ascii_experiment_report as d
from tests.integration import oe_pg_diagnostic as pg

PRIVATE = "private_sql_payload_token_run_uuid"


def cost():
    return {
        "records": [
            {
                "phase": "intent",
                "operation": "publication_transaction",
                "entered": True,
                "entry_us": 1,
                "body_us": 2,
                "exit_us": 3210000,
            }
        ],
        "overflow": False,
        "units": "microseconds",
        "file_times_may_overlap_body": True,
    }


def counters():
    return {
        "stage": "normal",
        "status": "sampled_natural_flush_may_lag",
        "functions": [
            {"function": name, "status": "unchanged_unknown", "delta": None}
            for name in pg.FUNCTIONS
        ],
    }


def test_valid_provenance_cost_counters():
    for mode in ("control", "candidate"):
        p = d.expected_provenance(mode)
        assert d.closed(d.PREFIXES[0], p, mode) == p
        assert (
            d.closed(d.PREFIXES[0], p, "candidate" if mode == "control" else "control")
            is None
        )
    for prefix, v in ((d.PREFIXES[1], cost()), (d.PREFIXES[2], counters())):
        assert d.closed(prefix, v, "control") == v
        assert d.closed(prefix, {**v, PRIVATE: PRIVATE}, "control") is None


@pytest.mark.parametrize(
    "key,value",
    [
        ("phase", PRIVATE),
        ("operation", PRIVATE),
        ("entered", 1),
        ("exit_us", float("nan")),
        ("exit_us", True),
        ("exit_us", 600000001),
        ("entry_us", -1),
    ],
)
def test_cost_unknown_values_rejected(key, value):
    c = cost()
    c["records"][0][key] = value
    assert d.closed(d.PREFIXES[1], c, "control") is None


def test_caps_and_pg_inventory():
    c = cost()
    c["records"] *= 129
    assert d.closed(d.PREFIXES[1], c, "control") is None
    p = counters()
    p["functions"][-1] = p["functions"][0]
    assert d.closed(d.PREFIXES[2], p, "control") is None
    p = counters()
    p["functions"][0] = {
        "function": pg.FUNCTIONS[0],
        "status": "sampled",
        "calls": 1,
        "total_ms": 3210.1,
        "self_ms": 5,
    }
    assert d.closed(d.PREFIXES[2], p, "control") == p
    p["functions"][0]["calls"] = True
    assert d.closed(d.PREFIXES[2], p, "control") is None


def test_failure_projection_never_retains_exception_details():
    f = {
        "family": "process",
        "code": "provider_timeout",
        "stage": "provider",
        "database": {PRIVATE: PRIVATE},
    }
    result = d.closed("OE_QUEUE_FAILURE_V1", f, "control")
    assert result == {
        "family": "process",
        "code": "provider_timeout",
        "stage": "provider",
    }
    for key in ("family", "code", "stage"):
        assert d.closed("OE_QUEUE_FAILURE_V1", {**f, key: PRIVATE}, "control") is None
    assert d.closed("OE_QUEUE_FAILURE_V1", {**f, PRIVATE: PRIVATE}, "control") is None


def test_provider_closed_timing():
    p = {
        "count": 1,
        "overflow": False,
        "synthetic_http_count": 0,
        "calls": [
            {
                "entry_remaining_ms": -479,
                "wire": "not_entered",
                "validation": "not_entered",
            }
        ],
    }
    assert d.closed("OE_PROVIDER_PROGRESS_V1", p, "control") == p
    p["calls"][0]["envelope"] = "success_envelope"
    assert d.closed("OE_PROVIDER_PROGRESS_V1", p, "control") == p
    p["calls"][0]["wire_remaining_ms"] = PRIVATE
    assert d.closed("OE_PROVIDER_PROGRESS_V1", p, "control") is None


def test_private_logs_to_closed_comparison(tmp_path, monkeypatch, capsys):
    private = tmp_path / "private"
    private.mkdir()
    proof = tmp_path / "proof"
    monkeypatch.setattr(d, "PRIVATE", private)
    monkeypatch.setattr(d, "PROOF", proof)
    for mode in ("control", "candidate"):
        text = PRIVATE + "\n"
        for prefix, value in (
            (d.PREFIXES[0], d.expected_provenance(mode)),
            (d.PREFIXES[1], cost()),
            (d.PREFIXES[2], counters()),
        ):
            text += prefix + ":" + json.dumps(value) + "\n"
        text += (
            "OE_QUEUE_FAILURE_V1:"
            + json.dumps(
                {"family": "process", "code": "provider_timeout", "stage": "provider"}
            )
            + "\n"
        )
        text += 'OE_QUEUE_FAILURE_V1:{"family":"process","family":"private"}\n'
        (private / f"normal-027-{mode}.log").write_text(text)
        (private / f"normal-027-{mode}.exit").write_text(
            "1" if mode == "control" else "0"
        )
        (private / f"normal-027-{mode}.xml").write_text(
            f'<testsuite><testcase name="{d.NORMAL}" time="4.2"><failure message="{PRIVATE}">{PRIVATE}</failure></testcase></testsuite>'
        )
    result = d.comparison()
    output = capsys.readouterr().out
    assert PRIVATE not in output
    assert "3210000" in output and "unchanged_unknown" in output
    assert result["variants"]["control"]["exit_code"] == 1
    assert result["variants"]["candidate"]["cases"][0]["outcome"] == "failure"
    assert (
        result["variants"]["control"]["case_inventory"] == "unavailable_or_unexpected"
    )
    assert result["variants"]["candidate"]["case_inventory"] == "exact"
    assert result["variants"]["candidate"]["invalid_records"] == 1
    assert PRIVATE not in (proof / "oe-raw-comparison.json").read_text()
    (private / "normal-027-candidate.xml").unlink()
    (private / "normal-027-candidate.exit").unlink()
    missing = d.comparison()["variants"]["candidate"]
    assert missing["exit_code"] is None and missing["cases"] == []


def test_workflow_private_capture_and_no_unrelated_post_gates():
    flow = (
        Path(__file__).parents[2] / ".github/workflows/mineagent-structured-factor.yml"
    ).read_text()
    assert '>"var/numeric-private/normal-027-$variant.log" 2>&1' in flow
    assert (
        "if: always()\n        run: python -m script.oe_raw_ascii_experiment_report"
        in flow
    )
    assert "Require actual declared-upload" not in flow
    assert "test_workflow_structured_stage_flow.py" not in flow
    assert "Confirm exact normal OE controller units are absent" in flow


@pytest.mark.parametrize(
    "mutation",
    [
        "valid",
        "missing",
        "duplicate",
        "wrong_mode",
        "malformed",
        "missing_timing",
        "oversized_duplicate",
        "marker_overflow",
        "nan_seconds",
        "missing_seconds",
        "infinite_seconds",
        "negative_seconds",
        "case_overflow",
    ],
)
def test_complete_and_passed_require_exact_evidence(tmp_path, monkeypatch, mutation):
    private = tmp_path / "private"
    private.mkdir()
    monkeypatch.setattr(d, "PRIVATE", private)
    monkeypatch.setattr(d, "PROOF", tmp_path / "proof")
    for mode in ("control", "candidate"):
        provenance = d.expected_provenance(mode)
        text = d.PREFIXES[0] + ":" + json.dumps(provenance) + "\n"
        if mode == "candidate":
            if mutation == "missing":
                text = ""
            elif mutation == "duplicate":
                text *= 2
            elif mutation == "oversized_duplicate":
                text += text.rstrip("\n") + " " * 66000 + "\n"
            elif mutation == "marker_overflow":
                text += 9 * (
                    "OE_QUEUE_FAILURE_V1:"
                    + json.dumps(
                        {
                            "family": "process",
                            "code": "provider_timeout",
                            "stage": "provider",
                        }
                    )
                    + "\n"
                )
            elif mutation == "wrong_mode":
                text = (
                    d.PREFIXES[0]
                    + ":"
                    + json.dumps(d.expected_provenance("control"))
                    + "\n"
                )
            elif mutation == "malformed":
                text = d.PREFIXES[0] + ':{"secret":"' + PRIVATE + '"}\n'
        for prefix, value in (
            (d.PREFIXES[1], cost()),
            (d.PREFIXES[2], counters()),
            (
                "OE_PROVIDER_PROGRESS_V1",
                {
                    "count": 1,
                    "overflow": False,
                    "calls": [
                        {
                            "entry_remaining_ms": 1,
                            "wire": "returned",
                            "validation": "accepted",
                        }
                    ],
                    "synthetic_http_count": 1,
                },
            ),
        ):
            if (
                mutation != "missing_timing"
                or mode != "candidate"
                or prefix != d.PREFIXES[1]
            ):
                text += prefix + ":" + json.dumps(value) + "\n"
        (private / f"normal-027-{mode}.log").write_text(text)
        (private / f"normal-027-{mode}.exit").write_text("0")
        names = [d.NORMAL, d.REJECTION] if mode == "control" else [d.NORMAL]
        (private / f"normal-027-{mode}.xml").write_text(
            "<testsuite>"
            + "".join(f'<testcase name="{name}" time="1"/>' for name in names)
            + "</testsuite>"
        )
    candidate_xml = private / "normal-027-candidate.xml"
    time_values = {
        "nan_seconds": 'time="nan"',
        "missing_seconds": "",
        "infinite_seconds": 'time="inf"',
        "negative_seconds": 'time="-1"',
    }
    if mutation in time_values:
        candidate_xml.write_text(
            candidate_xml.read_text().replace('time="1"', time_values[mutation])
        )
    if mutation == "case_overflow":
        candidate_xml.write_text(
            "<testsuite>"
            + 4 * f'<testcase name="{d.NORMAL}" time="1"/>'
            + "</testsuite>"
        )
    result = d.comparison()
    assert result["variants"]["control"]["complete"] is True
    assert result["variants"]["control"]["passed"] is True
    assert result["variants"]["candidate"]["complete"] is (mutation == "valid")
    assert result["passed"] is (mutation == "valid")
    assert (d.PROOF / "oe-raw-comparison.json").is_file()
    if mutation in ("oversized_duplicate", "marker_overflow"):
        assert result["variants"]["candidate"]["invalid_records"] > 0
