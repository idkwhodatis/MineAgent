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
        "schema_state": "full032c_experimental_submission_hash_reuse",
        "migration_sql_sha256": candidate.PINS,
        "original_body_sha256": expected_bodies()[0],
        "installed_body_sha256": expected_bodies()[1],
        "catalog_properties_verified": True,
        "production_acceptance": False,
        "elapsed_seconds": 1.0,
    }
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
