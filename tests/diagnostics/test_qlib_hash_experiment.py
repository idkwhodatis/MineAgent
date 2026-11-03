"""Offline candidate assembly only; real PG equivalence is separate evidence."""

from hashlib import sha256
from pathlib import Path

import pytest

from tests.integration import rd_hash_reuse_experiment as candidate


def test_candidate_has_exactly_two_invocation_local_hashes_and_no_other_change():
    old, new = candidate.previous_function(), candidate.candidate_sql()
    assert new.count(candidate.CONFIG_HASH) == new.count(candidate.INPUT_HASH) == 1
    assert candidate.LOCALS in new
    assert candidate.ASSIGNMENTS in new
    # Exact inverse proves every relation, validation call and exception remains.
    restored = (
        new.replace(candidate.ASSIGNMENTS, "")
        .replace(candidate.LOCALS, candidate.DECLARATION)
        .replace("cfg_hash", candidate.CONFIG_HASH)
        .replace("input_hash", candidate.INPUT_HASH)
    )
    assert restored == old == candidate.restoration_sql()
    assert new.index(candidate.ASSIGNMENTS) > new.index("cfg:=r.config;")
    for word in ("CREATE TABLE", "pg_temp", "SET LOCAL", "cache", "ON CONFLICT"):
        assert word not in new


@pytest.mark.parametrize("revision", candidate.PINS)
def test_candidate_rejects_changed_predecessors(monkeypatch, revision):
    read = candidate.historical
    monkeypatch.setattr(
        candidate,
        "historical",
        lambda r, n: read(r, n) + ("\n" if r == revision else ""),
    )
    with pytest.raises(ValueError, match="Exact predecessor"):
        candidate.candidate_sql()


def test_candidate_rejects_even_reviewed_unexpected_replacement(monkeypatch):
    read = candidate.historical
    altered = read("032a", "UPGRADE_SQL") + "\n" + candidate.previous_function()
    monkeypatch.setattr(
        candidate, "historical", lambda r, n: altered if r == "032a" else read(r, n)
    )
    monkeypatch.setitem(candidate.PINS, "032a", sha256(altered.encode()).hexdigest())
    with pytest.raises(ValueError, match="Unexpected submission"):
        candidate.candidate_sql()


def test_candidate_rejects_layout_drift_and_is_not_active_migration(monkeypatch):
    old = candidate.previous_function()
    monkeypatch.setattr(
        candidate,
        "previous_function",
        lambda: old.replace(candidate.CONFIG_HASH, "different_hash(cfg)", 1),
    )
    with pytest.raises(ValueError, match="Exact hash-expression"):
        candidate.candidate_sql()
    migrations = Path(__file__).resolve().parents[2] / "backend/alembic/versions"
    assert len(list(migrations.glob("032b_*.py"))) == 1
    assert not any(
        "rd_hash_reuse_experiment" in p.read_text() for p in migrations.glob("*.py")
    )


@pytest.mark.parametrize("value", ["", "1", "true", "production", "Candidate"])
def test_experiment_mode_is_fail_closed(monkeypatch, value):
    monkeypatch.setenv(candidate.ENV, value)
    with pytest.raises(ValueError, match="explicit"):
        candidate.mode()


def test_default_cannot_mutate_any_database(monkeypatch):
    monkeypatch.delenv(candidate.ENV, raising=False)
    assert candidate.prepare(object(), object()) is None
    assert candidate.probes(object(), object()) is None


def test_fixture_identity_gate_precedes_any_replacement(monkeypatch):
    from types import SimpleNamespace as NS

    from sqlalchemy.engine import make_url

    monkeypatch.setenv(candidate.ENV, "candidate")
    url = make_url("postgresql+psycopg://localhost/production")
    with pytest.raises(ValueError, match="fixture identity"):
        candidate._fixture(object(), NS(url=url), NS(source=url))


def test_branch_only_workflow_keeps_runtime_and_build_prerequisites():
    root = Path(__file__).resolve().parents[2]
    content = (root / ".github/workflows/mineagent-configured-analysis.yml").read_text()
    assert content.startswith("name: EXPERIMENT ONLY")
    assert "on:\n  workflow_dispatch:\n\npermissions:" in content
    assert "  push:" not in content and "  pull_request:" not in content
    assert "timeout-minutes: 120" in content
    control = content.index("        id: control")
    second = content.index("        id: candidate")
    end = content.index("      - name: Require BOTH")
    assert control < second < end
    for section in (content[control:second], content[second:end]):
        assert "continue-on-error: true" in section
        assert "timeout-minutes: 40" in section
        assert "test_rdagent_workflow_user_flow.py" in section
    assert "!cancelled() && steps.control.outcome != 'skipped'" in content
    for name in (
        "Build locked frontend",
        "Install matched Chromium",
        "Build separate fixed candidate",
        "Reproduce exact installed controller",
        "Reproduce exact accepted installed Qlib",
        "Bind streaming backup",
    ):
        assert name in content
    assert (
        "Require BOTH experiment outcomes - never normal acceptance\n        if: always()"
        in content
    )
    assert "-c track_functions=pl" in content
    assert "ci_rd_provider_compose.sh" not in content


def test_report_never_echoes_arbitrary_diagnostics():
    from script.qlib_hash_experiment_report import closed_record

    for prefix in ("QLIB_HASH_EXPERIMENT", "QLIB_HASH_PROBES", "RD_PG_FUNCTIONS"):
        assert closed_record(prefix, {"secret": "must-not-leak"}, "candidate") is None
    assert closed_record(
        "RD_PG_FUNCTIONS",
        {"stage": "workflow_first", "status": "unavailable"},
        "candidate",
    ) == {"stage": "workflow_first", "status": "unavailable"}
    assert (
        closed_record(
            "RD_PG_FUNCTIONS",
            {
                "stage": "workflow_first",
                "status": "sampled_natural_flush_may_lag",
                "functions": [{"function": "secret", "status": "missing_or_untracked"}],
            },
            "candidate",
        )
        is None
    )


def test_two_cases_and_no_skips_are_required(tmp_path, monkeypatch):
    from script import qlib_hash_experiment_report as report

    monkeypatch.setattr(report, "PRIVATE", tmp_path)
    monkeypatch.setattr(report, "PROOF", tmp_path / "proof")
    (tmp_path / "hash-control.xml").write_text(
        '<testsuites><testsuite><testcase name="unexpected-secret"><skipped/></testcase></testsuite></testsuites>'
    )
    assert report.report("control", 0) is False
    value = (tmp_path / "proof/hash-control.json").read_text()
    assert "unexpected-secret" not in value
    assert '"skipped"' in value


def catalog_row():
    return (
        candidate._body(candidate.previous_function()),
        "plpgsql",
        "v",
        "u",
        False,
        False,
        False,
        False,
        "f",
        100,
        0,
        ["search_path=public, pg_catalog"],
        "void",
        "2950",
        ["sid"],
        10,
        None,
        12345,
        "-",
    )


@pytest.mark.parametrize("index", list(range(1, 15)) + [16, 18])
def test_original_catalog_properties_reject_drift(index):
    from types import SimpleNamespace as NS

    row = list(catalog_row())
    row[index] = "drift"
    connection = NS(execute=lambda *args: NS(all=lambda: [row]))
    with pytest.raises(ValueError, match="catalog drift"):
        candidate._catalog(connection)


def test_exact_catalog_is_accepted_but_overload_is_not():
    from types import SimpleNamespace as NS

    row = catalog_row()
    assert candidate._catalog(NS(execute=lambda *args: NS(all=lambda: [row]))) == row
    with pytest.raises(ValueError, match="identity"):
        candidate._catalog(NS(execute=lambda *args: NS(all=lambda: [row, row])))


def test_report_requires_expected_body_not_arbitrary_hash():
    from script.qlib_hash_experiment_report import closed_record, expected_bodies

    value = {
        "mode": "candidate",
        "policy": "default-off-waits",
        "schema_revision": candidate.REVISION,
        "schema_state": "full032c_experimental_hash_and_canonical_reuse",
        "migration_sql_sha256": candidate.PINS,
        "original_body_sha256": expected_bodies()[0],
        "installed_body_sha256": expected_bodies()[1],
        "catalog_properties_verified": True,
        "production_acceptance": False,
        "elapsed_seconds": 1.0,
    }
    from tests.integration.rd_canonical_reuse_experiment import provenance

    value.update(provenance("candidate"))
    assert closed_record("QLIB_HASH_EXPERIMENT", value, "candidate") == value
    assert (
        closed_record(
            "QLIB_HASH_EXPERIMENT",
            {**value, "installed_body_sha256": "f" * 64},
            "candidate",
        )
        is None
    )


def test_duplicate_probe_labels_are_not_evidence():
    from script.qlib_hash_experiment_report import closed_record

    value = {
        "mode": "control",
        "policy": "default-off-waits",
        "phase": "first",
        "baseline": "accepted",
        "rejections": [{"probe": "missing_submission", "sqlstate": "P0002"}] * 3,
    }
    assert closed_record("QLIB_HASH_PROBES", value, "control") is None


def test_failed_control_is_preserved_without_masking_candidate_success(
    tmp_path, monkeypatch
):
    import json

    from script import qlib_hash_experiment_report as report

    monkeypatch.setattr(report, "PROOF", tmp_path)
    rows = [{"policy": p, "phase": "first", "rejections": []} for p in report.POLICIES]
    for mode, passed, probes in (
        ("control", False, rows),
        (
            "candidate",
            True,
            rows
            + [{"policy": report.POLICIES[1], "phase": "seeded", "rejections": []}],
        ),
    ):
        (tmp_path / f"hash-{mode}.json").write_text(
            json.dumps(
                {
                    "passed": passed,
                    "complete": True,
                    "diagnostics": {"QLIB_HASH_PROBES": probes},
                }
            )
        )
    assert report.verify() is False
    result = json.loads((tmp_path / "hash-comparison.json").read_text())
    assert result["candidate_passed"] and not result["control_passed"]
    assert result["experiment_complete"] and result["paired_probes_equal"]
    assert result["unpaired_phases"] == [[report.POLICIES[1], "seeded"]]
    assert result["whole_graph_equivalence"] == "not_established"


def rpc_timing():
    return {
        "summary": [
            {
                "method": "submit_embedding",
                "stage": "handle",
                "count": 1,
                "total_ms": 12,
                "max_ms": 12,
            }
        ],
        "tail": [
            {
                "method": "submit_embedding",
                "sequence": 9,
                "stage": "handle",
                "elapsed_ms": 12,
                "remaining_ms": -1,
                "ok": False,
            }
        ],
    }


def queue_failure():
    return {
        "family": "rpc",
        "code": "deadline",
        "stage": "observer",
        "sqlstate": "none",
        "rpc_method": "submit_embedding",
        "observer_failure": "deadline",
    }


@pytest.mark.parametrize(
    "prefix,record",
    [("RD_QUEUE_FAILURE", queue_failure()), ("RD_RPC_TIMING", rpc_timing())],
)
def test_existing_closed_rpc_records_survive_report(prefix, record):
    from script.qlib_hash_experiment_report import closed_record

    assert closed_record(prefix, record, "candidate") == record
    assert closed_record(prefix, {**record, "payload": "private"}, "candidate") is None


@pytest.mark.parametrize(
    "key", ["family", "code", "stage", "sqlstate", "rpc_method", "observer_failure"]
)
def test_queue_failure_rejects_unlisted_values_and_payloads(key):
    from script.qlib_hash_experiment_report import closed_record

    for value in ("private-token", ["private-token"], {"message": "private"}, 17):
        assert (
            closed_record(
                "RD_QUEUE_FAILURE", {**queue_failure(), key: value}, "candidate"
            )
            is None
        )


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("summary", "method", "private"),
        ("tail", "method", "private"),
        ("summary", "stage", "private"),
        ("tail", "stage", "private"),
        ("summary", "count", True),
        ("summary", "total_ms", 3_600_001),
        ("tail", "remaining_ms", -3_600_001),
        ("tail", "elapsed_ms", float("nan")),
        ("tail", "sequence", "private"),
        ("tail", "ok", 1),
    ],
)
def test_rpc_timing_rejects_unbounded_or_unlisted_values(section, key, value):
    from script.qlib_hash_experiment_report import closed_record

    record = rpc_timing()
    record[section][0][key] = value
    assert closed_record("RD_RPC_TIMING", record, "candidate") is None


def test_rpc_timing_rejects_duplicates_and_overflow():
    from script.qlib_hash_experiment_report import closed_record

    record = rpc_timing()
    record["summary"] *= 2
    assert closed_record("RD_RPC_TIMING", record, "candidate") is None
    record = rpc_timing()
    record["tail"] *= 65
    assert closed_record("RD_RPC_TIMING", record, "candidate") is None


def test_report_retains_closed_boundary_without_raw_errors(tmp_path, monkeypatch):
    import json

    from script import qlib_hash_experiment_report as report

    monkeypatch.setattr(report, "PRIVATE", tmp_path)
    monkeypatch.setattr(report, "PROOF", tmp_path / "proof")
    (tmp_path / "hash-candidate.log").write_text(
        "RD_QUEUE_FAILURE:"
        + json.dumps(queue_failure())
        + "\nRD_RPC_TIMING:"
        + json.dumps(rpc_timing())
        + '\nRD_QUEUE_FAILURE:{"message":"private-token"}\nprivate traceback'
    )
    assert report.report("candidate", 1) is False
    output = (tmp_path / "proof/hash-candidate.json").read_text()
    value = json.loads(output)
    assert value["diagnostics"]["RD_QUEUE_FAILURE"] == [queue_failure()]
    assert value["diagnostics"]["RD_RPC_TIMING"] == [rpc_timing()]
    assert "private-token" not in output and "traceback" not in output
