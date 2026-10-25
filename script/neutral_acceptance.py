"""Exact, no-skip hosted acceptance for neutral infrastructure, not engine loops.

Only fixed test identities and outcome summaries leave the disposable runner.
The independent legacy application/Compose browser workflows remain mandatory.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from types import MappingProxyType
from xml.etree import ElementTree as ET

from script.ci_process_owner import ProcessOwner
from script.summarize_ci_junit import summarize

MAX_REPORT_BYTES = 20_000_000
PROFILE_SECONDS = 1200
# Closed inventory is integrated with the real test owners before publication.
# Collection and unit substitutes cannot satisfy any real profile.
REQUIRED = MappingProxyType(
    {
        "postgres": (
            "tests/integration/test_neutral_lifecycle.py::test_neutral_nested_transaction_never_erases_outer_work",
            "tests/integration/test_neutral_lifecycle.py::test_neutral_helper_cannot_commit_or_reuse_released_authority",
            "tests/integration/test_neutral_lifecycle.py::test_neutral_real_scope_substitution_is_rejected[owner_id]",
            "tests/integration/test_neutral_lifecycle.py::test_neutral_real_scope_substitution_is_rejected[run_id]",
            "tests/integration/test_neutral_lifecycle.py::test_neutral_real_scope_substitution_is_rejected[attempt_id]",
            "tests/integration/test_neutral_lifecycle.py::test_neutral_real_scope_substitution_is_rejected[lease_token]",
            "tests/integration/test_neutral_lifecycle.py::test_neutral_real_scope_substitution_is_rejected[generation]",
            "tests/integration/test_neutral_lifecycle.py::test_neutral_resource_scope_is_not_implicitly_promoted",
            "tests/integration/test_neutral_lifecycle.py::test_neutral_database_loss_does_not_reconnect",
            "tests/integration/test_neutral_objects.py::test_neutral_real_equal_bytes_different_kinds_are_separate_and_charged",
            "tests/integration/test_neutral_objects.py::test_neutral_real_exact_republication_reuses_same_kind_object",
            "tests/integration/test_neutral_objects.py::test_neutral_real_object_identity_is_immutable[owner_id]",
            "tests/integration/test_neutral_objects.py::test_neutral_real_object_identity_is_immutable[object_kind]",
            "tests/integration/test_neutral_objects.py::test_neutral_real_object_identity_is_immutable[sha256]",
            "tests/integration/test_neutral_objects.py::test_neutral_real_object_identity_is_immutable[storage_key]",
            "tests/integration/test_neutral_objects.py::test_neutral_real_publication_budget_cannot_substitute_scope",
            "tests/integration/test_neutral_objects.py::test_neutral_real_pending_peak_reservations_count_toward_physical_quota",
            "tests/integration/test_neutral_objects.py::test_neutral_real_database_loss_after_fsync_preserves_charged_orphan",
            "tests/integration/test_neutral_objects.py::test_neutral_real_lease_loss_after_file_write_leaves_charged_evidence",
            "tests/integration/test_neutral_objects.py::test_neutral_real_pending_object_writer_blocks_terminal_transition[run_terminal]",
            "tests/integration/test_neutral_objects.py::test_neutral_real_pending_object_writer_blocks_terminal_transition[attempt_terminal]",
            "tests/integration/test_neutral_objects.py::test_neutral_real_pending_object_writer_blocks_terminal_transition[generation]",
            "tests/integration/test_neutral_objects.py::test_neutral_real_crash_temporary_blocks_every_retry_without_growth",
            "tests/integration/test_neutral_objects.py::test_neutral_real_success_retains_peak_charge_and_allows_contained_terminal",
            "tests/integration/test_research_process_recovery.py::test_process_admission_rejects_wrong_scope[owner_id]",
            "tests/integration/test_research_process_recovery.py::test_process_admission_rejects_wrong_scope[run_id]",
            "tests/integration/test_research_process_recovery.py::test_process_admission_rejects_wrong_scope[attempt_id]",
            "tests/integration/test_research_process_recovery.py::test_process_admission_rejects_wrong_scope[generation]",
            "tests/integration/test_research_process_recovery.py::test_process_unknown_stop_blocks_terminal_and_keeps_reservation",
            "tests/integration/test_research_process_recovery.py::test_process_cannot_reuse_launch_or_wrong_reservation",
            "tests/integration/test_research_process_recovery.py::test_real_process_stop_after_lease_loss_is_containment_only",
            "tests/integration/test_research_process_recovery.py::test_process_insert_enforces_composite_scope[owner_id]",
            "tests/integration/test_research_process_recovery.py::test_process_insert_enforces_composite_scope[run_id]",
            "tests/integration/test_research_process_recovery.py::test_process_insert_enforces_composite_scope[attempt_id]",
            "tests/integration/test_research_process_recovery.py::test_process_insert_enforces_composite_scope[generation]",
            "tests/integration/test_research_process_recovery.py::test_process_database_terminal_and_generation_fail_closed",
            "tests/integration/test_research_process_recovery.py::test_process_profile_requires_every_reserved_peak[process_slots]",
            "tests/integration/test_research_process_recovery.py::test_process_profile_requires_every_reserved_peak[wall_milliseconds]",
            "tests/integration/test_research_process_recovery.py::test_process_profile_requires_every_reserved_peak[storage_bytes]",
            "tests/integration/test_research_process_recovery.py::test_process_launch_requires_committed_current_one_use_authority[absent]",
            "tests/integration/test_research_process_recovery.py::test_process_launch_requires_committed_current_one_use_authority[rolled_back]",
            "tests/integration/test_research_process_recovery.py::test_process_launch_requires_committed_current_one_use_authority[stale]",
            "tests/integration/test_research_process_recovery.py::test_process_launch_requires_committed_current_one_use_authority[consumed]",
            "tests/integration/test_model_broker_accounting.py::test_neutral_identical_physical_failures_charge_separately_and_completion_replays",
            "tests/integration/test_model_broker_accounting.py::test_neutral_request_drift_cannot_create_another_logical_operation",
            "tests/integration/test_model_broker_accounting.py::test_neutral_lost_submission_remains_reserved_and_cannot_retry",
            "tests/integration/test_model_broker_accounting.py::test_neutral_concurrent_reservations_cannot_overspend_run_cap",
            "tests/integration/test_model_broker_accounting.py::test_neutral_budget_survives_generation_change_without_reset",
            "tests/integration/test_model_broker_accounting.py::test_neutral_nonmodel_refund_requires_domain_stop_proof[process_unit]",
            "tests/integration/test_model_broker_accounting.py::test_neutral_nonmodel_refund_requires_domain_stop_proof[object_storage]",
            "tests/integration/test_model_broker_accounting.py::test_neutral_cancellation_interrupts_real_retry_sleep",
            "tests/integration/test_model_broker_accounting.py::test_neutral_direct_sql_cannot_rewrite_ledger[caps]",
            "tests/integration/test_model_broker_accounting.py::test_neutral_direct_sql_cannot_rewrite_ledger[reservation_amount]",
            "tests/integration/test_model_broker_accounting.py::test_neutral_direct_sql_cannot_rewrite_ledger[delete_reservation]",
            "tests/integration/test_model_broker_accounting.py::test_neutral_direct_sql_cannot_rewrite_ledger[projection]",
            "tests/integration/test_model_broker_accounting.py::test_neutral_direct_sql_cannot_forge_completed_response_tuple",
            "tests/integration/test_model_broker_accounting.py::test_neutral_direct_sql_purpose_cannot_relabel_chat_as_embedding",
            "tests/integration/test_model_broker_accounting.py::test_neutral_context_abort_persists_and_blocks_all_new_work",
            "tests/integration/test_model_broker_accounting.py::test_neutral_empty_context_abort_prevents_later_first_operation",
            "tests/integration/test_model_broker_accounting.py::test_neutral_direct_sql_cannot_reverse_context_abort[reason]",
            "tests/integration/test_model_broker_accounting.py::test_neutral_direct_sql_cannot_reverse_context_abort[new_operation]",
            "tests/integration/test_model_broker_accounting.py::test_neutral_direct_sql_cannot_reverse_context_abort[delete]",
        ),
        "isolation": (
            "tests/integration/test_research_supervisor_isolation.py::test_real_whole_unit_child_survives_controller_then_verified_stop",
            "tests/integration/test_research_supervisor_isolation.py::test_real_peer_authentication_rejects_outside_pid_and_wrong_ticket",
            "tests/integration/test_research_supervisor_isolation.py::test_real_uncertain_create_is_recovered_by_exact_journal[create]",
            "tests/integration/test_research_supervisor_isolation.py::test_real_uncertain_create_is_recovered_by_exact_journal[start]",
            "tests/integration/test_research_supervisor_isolation.py::test_real_tampered_journal_never_authorizes_stop",
            "tests/integration/test_research_supervisor_isolation.py::test_real_host_deadline_stops_still_running_fixed_fixture",
            "tests/integration/test_research_supervisor_isolation.py::test_real_pid1_deadline_survives_host_caller_death",
            "tests/integration/test_research_supervisor_isolation.py::test_real_live_peer_cannot_submit_after_process_projection_uncertain",
            "tests/integration/test_research_supervisor_isolation.py::test_real_recovery_manifest_rejects_unsafe_projection_drift",
        ),
        "restore": (
            "tests/integration/test_neutral_restore.py::test_neutral_cold_restore_preserves_rows_bytes_and_uncertain_accounting",
            "tests/integration/test_neutral_restore.py::test_historical_upgrade_preserves_legacy_pins_and_allows_neutral_publication[011_note_pin_trigger]",
            "tests/integration/test_neutral_restore.py::test_historical_upgrade_preserves_legacy_pins_and_allows_neutral_publication[013_factor_import]",
        ),
    }
)

REQUIRED_SOURCES = MappingProxyType(
    {
        "postgres": frozenset(
            {
                "tests/integration/test_neutral_lifecycle.py",
                "tests/integration/test_research_process_recovery.py",
                "tests/integration/test_model_broker_accounting.py",
                "tests/integration/test_neutral_objects.py",
            }
        ),
        "isolation": frozenset({"tests/integration/test_research_supervisor_isolation.py"}),
        "restore": frozenset({"tests/integration/test_neutral_restore.py"}),
    }
)


def required_cases(profile):
    nodes = REQUIRED.get(profile)
    if not nodes or len(nodes) != len(set(nodes)):
        raise ValueError("Closed nonempty neutral case inventory required")
    if {node.split("::")[0] for node in nodes} != REQUIRED_SOURCES[profile]:
        raise ValueError("Every neutral acceptance boundary must be represented")
    identities = []
    for node in nodes:
        path, name = node.split("::")
        if not path.startswith("tests/") or not path.endswith(".py"):
            raise ValueError("Unsupported neutral test source")
        identities.append((path.removesuffix(".py").replace("/", "."), name))
    return tuple(identities)


def report_root(path):
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > MAX_REPORT_BYTES
    ):
        raise ValueError("Fresh bounded neutral report required")
    raw = path.read_bytes()
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise ValueError("Unsupported report framing")
    return ET.fromstring(raw)


def verify_report(path, profile, exit_code):
    expected = required_cases(profile)
    root = report_root(path)
    suites = list(root)
    cases = list(root.iter("testcase"))
    if (
        exit_code != 0
        or root.tag != "testsuites"
        or len(suites) != 1
        or suites[0].tag != "testsuite"
        or Counter((case.get("classname"), case.get("name")) for case in cases)
        != Counter(expected)
        or len(suites[0].findall("testcase")) != len(expected)
        or any(
            next(root.iter(tag), None) is not None
            for tag in ("skipped", "failure", "error")
        )
        or any(
            suites[0].get(key) != value
            for key, value in {
                "tests": str(len(expected)),
                "failures": "0",
                "errors": "0",
                "skipped": "0",
            }.items()
        )
    ):
        raise ValueError("Exact real neutral cases must all execute and pass")


SAFE_EXCEPTION_CLASSES = frozenset(
    {
        "AssertionError",
        "IntegrityError",
        "OperationalError",
        "ProgrammingError",
        "DataError",
        "DBAPIError",
        "DomainError",
        "ProcessError",
        "BrokerAbort",
        "ValueError",
        "TypeError",
        "KeyError",
        "AttributeError",
        "ImportError",
        "TimeoutError",
        "FileNotFoundError",
        "PermissionError",
        "MaintenanceBusy",
        "CheckViolation",
        "ForeignKeyViolation",
        "NotNullViolation",
        "UniqueViolation",
        "UndefinedTable",
        "UndefinedColumn",
        "RaiseException",
    }
)


def failure_outcomes(path, profile):
    """Known identifiers/locations/classes only; never raw SQL, text or values."""
    allowed = set(required_cases(profile))
    root = Path(__file__).resolve().parents[1]
    sources = {}
    for directory in ("backend", "packages", "runtimes", "workers", "tests"):
        for source in (root / directory).rglob("*.py"):
            if (
                source.is_symlink()
                or not source.is_file()
                or source.stat().st_size > 1_000_000
            ):
                continue
            sources[source.relative_to(root).as_posix()] = len(
                source.read_bytes().splitlines()
            )
    result = []
    for case in report_root(path).iter("testcase"):
        identity = (case.get("classname"), case.get("name"))
        if identity not in allowed:
            continue
        for outcome in ("failure", "error", "skipped"):
            failure = case.find(outcome)
            if failure is None:
                continue
            text = failure.text or ""
            frames = set()
            for match in re.finditer(
                r"((?:backend|packages|runtimes|workers|tests)/[A-Za-z0-9_/.-]+\.py):([0-9]{1,6})(?=\D|$)",
                text,
            ):
                filename, line = match[1], int(match[2])
                if filename in sources and 1 <= line <= sources[filename]:
                    frames.add((filename, line))
            classes = sorted(
                name
                for name in SAFE_EXCEPTION_CLASSES
                if re.search(r"\b" + name + r"\b", text)
            )
            item = {"source": identity[0], "case": identity[1], "outcome": outcome}
            if frames:
                item["source_frames"] = [
                    {"file": filename, "line": line}
                    for filename, line in sorted(frames)[:12]
                ]
            if classes:
                item["exception_classes"] = classes[:8]
            result.append(item)
    return result[: len(allowed)]


def validate_source(source):
    if (
        source.get("GITHUB_ACTIONS") != "true"
        or source.get("MINEAGENT_REQUIRE_NEUTRAL_ACCEPTANCE") != "1"
        or source.get("MINEAGENT_RESEARCH_ISOLATION_REQUIRED") != "1"
        or source.get("DATA_PROVIDER_MODE") != "fixture"
        or source.get("E2E_LIVE_PROVIDERS") != "0"
        or source.get("RUN_BACKUP_RESTORE_DRILL") != "1"
        or not source.get("TEST_DATABASE_URL")
        or not source.get("DATABASE_URL")
        or re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            source.get("MINEAGENT_RESEARCH_SUPERVISOR_IMAGE", ""),
        )
        is None
    ):
        raise ValueError("Explicit disposable neutral fixture admission required")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=tuple(REQUIRED), required=True)
    args = parser.parse_args()
    profile = args.profile
    root = Path(__file__).resolve().parents[1]
    destination = root / "var/ci-evidence" / f"neutral-{profile}-junit.xml"
    owner = ProcessOwner(time.monotonic() + PROFILE_SECONDS + 30)
    passed = False
    cleaned = False
    private = None
    report = None
    try:
        validate_source(os.environ)
        required_cases(profile)
        if destination.exists() or destination.is_symlink():
            raise ValueError("Stale neutral evidence destination")
        owner.contain()
        private = Path(tempfile.mkdtemp(prefix="mineagent-neutral-"))
        report = private / "report.xml"
        environment = dict(os.environ)
        # No external pytest selection, injection or plugin autoload may turn
        # the exact inventory into a skipped/filtered or forged successful run.
        for key in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTHONPATH", "PYTHONSTARTUP"):
            environment.pop(key, None)
        environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        owner.run(
            [
                sys.executable,
                "-m",
                "pytest",
                *REQUIRED[profile],
                "-q",
                "-o",
                "addopts=",
                "--basetemp",
                str(private / "pytest"),
                f"--junitxml={report}",
            ],
            cwd=root,
            env=environment,
            seconds=PROFILE_SECONDS,
        )
        verify_report(report, profile, 0)
        passed = True
    except Exception:  # noqa: BLE001 - private fixture diagnostics never escape
        print("Neutral acceptance failed; inspect private runner evidence")
    finally:
        if owner.started:
            try:
                cleaned = owner.cleanup()
            except Exception:  # noqa: BLE001 - containment uncertainty is failure
                cleaned = False
        if report is not None and report.is_file() and not report.is_symlink():
            try:
                print(
                    "NEUTRAL_OUTCOMES:"
                    + json.dumps(failure_outcomes(report, profile), sort_keys=True)
                )
                summarize(report, destination)
            except Exception:  # noqa: BLE001 - no raw report output
                passed = False
        # Never erase a path while its writer's containment is unconfirmed.
        if cleaned and private is not None:
            try:
                shutil.rmtree(private)
            except OSError:
                passed = False
    if not passed or not cleaned:
        return 1
    print("Exact neutral profile passed with owned subprocess cleanup")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
