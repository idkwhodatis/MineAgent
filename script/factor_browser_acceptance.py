"""Hosted-only, fail-closed real factor browser acceptance.

The independent library needs a real PostgreSQL/API/Vite stack but does not run
legacy optimization workers. Reuse the reviewed process owner and credential
projection; no DB, Docker, model configuration or ambient Node hooks reach Node.
Raw reports remain private. Only fixed case identities and numeric outcomes are
exported. Listing, skipped tests and a stale report cannot establish acceptance.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from xml.etree import ElementTree as ET

from script.ci_application_browser import (
    API,
    FRONTEND,
    base_environment,
    service_environment,
    validate_source,
    wait_ready,
)
from script.ci_browser_diagnostics import failure_sites
from script.ci_process_owner import ProcessOwner
from script.compose_browser_launcher import browser_command, browser_environment
from script.summarize_ci_junit import summarize

TOTAL_SECONDS = 900
BROWSER_SECONDS = 600
MAX_REPORT_BYTES = 20_000_000
REQUIRED_BROWSER_CASES = (
    "real persisted factor values, missingness and sample survive exact URL refresh and history",
    "real parent comparison keeps incompatible bilateral values and suppresses arithmetic",
    "manual create and child source edits are immutable, repeat clicks are idempotent, presentation leaves source intact",
    "exact export and bundle use real API; foreign resources fail and import capability is explicit",
)
REQUIRED_IMPORT_BROWSER_CASE = "isolated package import preserves source evidence, derives exact local values and survives reload and retry"
REQUIRED_PG_CASES = (
    "test_create_exact_source_replay_conflict_and_owner",
    "test_fixture_actual_values_samples_comparison_and_export",
    "test_immutable_evidence_and_same_owner_snapshot_tuple_rejected",
    "test_tampered_partition_is_not_empty_success",
    "test_disabled_and_binary_import_fail_closed",
    "test_raw_import_queues_exact_archive_and_pinned_profile",
    "test_foreign_snapshot_and_wrong_factor_parent_are_indistinguishable",
    "test_concurrent_exact_create_never_duplicates",
    "test_imported_known_exposure_enters_owner_classifier_even_without_snapshot_binding",
    "test_completed_code_only_import_with_no_claims_keeps_holdout_history_unknown",
    "test_execution_record_navigation_is_separate_and_owner_scoped",
    "test_import_prepublication_owner_lock_requeues_then_same_admission_succeeds",
)
REQUIRED_RESTORE_CASES = (
    "test_real_factor_cold_restore_and_legacy_upgrade[011_note_pin_trigger]",
    "test_real_factor_cold_restore_and_legacy_upgrade[013_factor_import]",
    "test_real_factor_cold_restore_and_legacy_upgrade[014_run_identity]",
)
REQUIRED_FACTOR_MIGRATION_CASES = (
    "test_empty_factor_import_migration_round_trip_preserves_core",
    "test_empty_factor_library_migration_round_trip_preserves_legacy",
    "test_factor_import_migration_refuses_pending_admission",
    "test_factor_import_migration_refuses_retained_envelope",
    "test_factor_library_migration_refuses_orphan_object_registration",
    "test_factor_library_migration_refuses_retained_factor",
    "test_factor_library_migration_refuses_external_exposure_dependency",
    "test_factor_import_migration_refuses_external_exposure_dependency",
    "test_factor_library_migration_refuses_concurrent_insert",
    "test_factor_migration_refuses_shared_maintenance_admission[library]",
    "test_factor_migration_refuses_shared_maintenance_admission[import]",
    "test_factor_migration_refuses_dataset_only_dependency[library-storage]",
    "test_factor_migration_refuses_dataset_only_dependency[library-source]",
    "test_factor_migration_refuses_dataset_only_dependency[library-profile]",
    "test_factor_migration_refuses_dataset_only_dependency[import-source]",
    "test_factor_import_migration_refuses_provenance_only_dependency[version-provenance]",
    "test_factor_import_migration_refuses_provenance_only_dependency[snapshot-provenance]",
    "test_factor_import_migration_refuses_provenance_only_dependency[snapshot-verification]",
    "test_factor_import_migration_refuses_concurrent_raw_admission_insert",
    "test_factor_migration_rejects_stale_repeatable_read_snapshot[library]",
    "test_factor_migration_rejects_stale_repeatable_read_snapshot[import]",
    "test_factor_migration_refuses_locked_fk_parent[library-users]",
    "test_factor_migration_refuses_locked_fk_parent[library-dataset_versions]",
    "test_factor_migration_refuses_locked_fk_parent[library-strategy_versions]",
    "test_factor_migration_refuses_locked_fk_parent[import-users]",
    "test_factor_migration_refuses_locked_fk_parent[import-factor_objects]",
)
# Downgrades pass through the newest factor revisions before reaching legacy
# evidence guards. These regressions must run in the fast factor gate too.
REQUIRED_MIGRATION_CASES = (
    (
        "tests.integration.test_comparison_api",
        "test_comparison_migration_downgrade_refuses_evidence_loss",
    ),
    (
        "tests.integration.test_migrations",
        "test_migration_downgrade_upgrade_round_trip_preserves_schema_contract",
    ),
    (
        "tests.integration.test_note_pin_migration",
        "test_010_ambiguity_and_011_repair_preserve_pin_admission_and_history",
    ),
    (
        "tests.integration.test_workbench_api",
        "test_migration_refuses_to_drop_generation_context_without_descriptions",
    ),
    (
        "tests.integration.test_workspace_settings",
        "test_workspace_schema_round_trip_never_rewrites_immutable_runs",
    ),
)
REQUIRED_RUN_IDENTITY_CASES = {
    "test_real_legacy_run_identity_upgrade_preserves_bytes": 1,
    "test_real_identity_upgrade_rejects_unknown_history": 7,
    "test_real_run_identity_and_old_inputs_are_immutable": 8,
    "test_real_seed_identity_rejects_malformed_records": 10,
    "test_real_run_identity_insert_rejects_unregistered_pairs": 5,
}
REQUIRED_OUTBOX_CASES = (
    "test_stalled_recovery_rejects_invalid_persisted_binding_without_retry[extra-key]",
    "test_stalled_recovery_rejects_invalid_persisted_binding_without_retry[boolean-generation]",
    "test_stalled_recovery_rejects_invalid_persisted_binding_without_retry[wrong-run]",
    "test_stalled_recovery_rejects_invalid_persisted_binding_without_retry[wrong-generation]",
    "test_stalled_recovery_valid_then_invalid_batch_never_partially_retries",
    "test_outbox_dispatch_persists_one_procrastinate_job_and_is_replay_safe",
    "test_queue_insert_and_outbox_acknowledgement_roll_back_together",
    "test_concurrent_dispatchers_do_not_enqueue_twice",
    "test_cancelled_old_delivery_does_not_block_or_claim_resumed_generation",
    "test_crash_before_business_claim_is_redelivered_once",
    "test_recovery_includes_deleted_queue_worker",
    "test_recovery_does_not_touch_unrelated_or_healthy_jobs[healthy-worker]",
    "test_recovery_does_not_touch_unrelated_or_healthy_jobs[other-task]",
    "test_recovery_does_not_touch_unrelated_or_healthy_jobs[other-queue]",
    "test_recovery_does_not_touch_unrelated_or_healthy_jobs[finished-job]",
    "test_concurrent_stalled_job_recovery_retries_only_once",
    "test_stalled_job_recovery_rolls_back_and_preserves_business_fence",
)
REQUIRED_ISOLATION_CASES = {
    "test_real_factor_parser_roundtrip_and_removal": 1,
    "test_real_hostile_execution_stops_and_cannot_publish": 2,
    "test_real_network_environment_root_and_control_denial": 1,
    "test_real_supervisor_sigkill_retains_recovery_barrier": 1,
}


def verify_report(path: Path, kind: str) -> None:
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > MAX_REPORT_BYTES
    ):
        raise ValueError("Required fresh bounded factor report is unavailable")
    raw = path.read_bytes()
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise ValueError("Unsupported factor report framing")
    root = ET.fromstring(raw)
    cases = list(root.iter("testcase"))
    if not 1 <= len(cases) <= 512 or any(
        next(root.iter(tag), None) is not None
        for tag in ("failure", "error", "skipped")
    ):
        raise ValueError("Every required factor case must execute and pass")
    identities = [(case.get("classname", ""), case.get("name", "")) for case in cases]
    if len(set(identities)) != len(identities):
        raise ValueError(
            "Duplicate or retried factor cases cannot establish acceptance"
        )
    if kind in {"browser", "browser-import"}:
        expected = {
            ("factor-workbench.spec.ts", name) for name in REQUIRED_BROWSER_CASES
        }
        if kind == "browser-import":
            expected.add(("factor-workbench.spec.ts", REQUIRED_IMPORT_BROWSER_CASE))
        if set(identities) != expected:
            raise ValueError("Exact factor browser scenarios did not all execute")
    elif kind == "postgres":
        expected = {
            ("tests.integration.test_factor_library", name)
            for name in REQUIRED_PG_CASES
        }
        expected.update(
            ("tests.integration.test_factor_restore", name)
            for name in REQUIRED_RESTORE_CASES
        )
        expected.update(REQUIRED_MIGRATION_CASES)
        expected.update(
            ("tests.integration.test_outbox", name) for name in REQUIRED_OUTBOX_CASES
        )
        expected.update(
            ("tests.integration.test_factor_migrations", name)
            for name in REQUIRED_FACTOR_MIGRATION_CASES
        )
        if not expected.issubset(set(identities)):
            raise ValueError("Required real PostgreSQL factor cases did not execute")
        for name, count in REQUIRED_RUN_IDENTITY_CASES.items():
            if (
                sum(
                    classname == "tests.integration.test_run_identity"
                    and case.split("[", 1)[0] == name
                    for classname, case in identities
                )
                != count
            ):
                raise ValueError("Required real run identity cases did not execute")
    elif kind == "isolation":
        for name, count in REQUIRED_ISOLATION_CASES.items():
            if (
                sum(
                    classname == "tests.isolation.test_factor_parser"
                    and case.split("[", 1)[0] == name
                    for classname, case in identities
                )
                != count
            ):
                raise ValueError(
                    "Required real factor parser isolation cases did not execute"
                )
    else:
        raise ValueError("Unknown fixed factor acceptance profile")


def create_diagnostics(path: Path) -> list[dict]:
    """Project only closed, non-sensitive form/HTTP states from private failures."""
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > MAX_REPORT_BYTES
    ):
        raise ValueError("Bounded browser report required")
    raw = path.read_bytes()
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise ValueError("Unsupported report framing")
    pattern = re.compile(
        r"FACTOR_CREATE_DIAGNOSTIC_V1:phase=(response|navigation);"
        r"status=(none|[1-5][0-9]{2});requests=([012]);form=(valid|invalid);"
        r"alert=(yes|no);saving=(yes|no)(?![A-Za-z0-9_])"
    )
    result = []
    for index, case in enumerate(list(ET.fromstring(raw).iter("testcase"))[:512]):
        for failure in case:
            if failure.tag not in {"failure", "error"}:
                continue
            for match in list(pattern.finditer(failure.text or ""))[:4]:
                phase, status, requests, form, alert, saving = match.groups()
                result.append(
                    {
                        "case_index": index,
                        "phase": phase,
                        "http_status": None if status == "none" else int(status),
                        "requests": min(int(requests), 2),
                        "form": form,
                        "alert": alert,
                        "saving": saving,
                    }
                )
    return result[:16]


def response_diagnostics(path: Path) -> list[dict]:
    """Expose finite domain codes only, never a response message or body."""
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > MAX_REPORT_BYTES
    ):
        raise ValueError("Bounded browser report required")
    with path.open("rb") as stream:
        raw = stream.read(MAX_REPORT_BYTES + 1)
    if len(raw) > MAX_REPORT_BYTES or b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise ValueError("Unsupported report framing")
    codes = (
        "factor_busy|factor_import_busy|factor_capacity|factor_recovery_required|"
        "factor_storage_uncertain|factor_admission_lost|factor_admission_timeout|"
        "factor_import_unavailable|idempotency_conflict|unauthorized|"
        "maintenance_busy|factor_storage_unavailable|database_unavailable|none|other"
    )
    pattern = re.compile(
        r"FACTOR_RESPONSE_DIAGNOSTIC_V1:operation=(create|import|fixture);"
        r"status=(none|[1-5][0-9]{2});code=(" + codes + r")(?![A-Za-z0-9_-])"
    )
    result = []
    for index, case in enumerate(list(ET.fromstring(raw).iter("testcase"))[:512]):
        for failure in case:
            if failure.tag not in {"failure", "error"}:
                continue
            for match in list(pattern.finditer(failure.text or ""))[:4]:
                operation, status, code = match.groups()
                result.append(
                    {
                        "case_index": index,
                        "operation": operation,
                        "http_status": None if status == "none" else int(status),
                        "code": code,
                    }
                )
    return result[:16]


def read_lifecycle_diagnostics(path: Path) -> list[dict]:
    """Only fixed lifecycle reasons/counts; never request URLs or browser errors."""
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > MAX_REPORT_BYTES
    ):
        raise ValueError("Bounded browser report required")
    with path.open("rb") as stream:
        raw = stream.read(MAX_REPORT_BYTES + 1)
    if len(raw) > MAX_REPORT_BYTES or b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise ValueError("Unsupported report framing")
    count = r"(0|[1-9]|[1-5][0-9]|6[0-4])"
    pattern = re.compile(
        r"FACTOR_READ_LIFECYCLE_V1:reason=(deadline|interrupted|page_closed);pending="
        + count
        + r";failed="
        + count
        + r"(?![A-Za-z0-9_-])"
    )
    result = []
    for index, case in enumerate(list(ET.fromstring(raw).iter("testcase"))[:512]):
        for failure in case:
            if failure.tag not in {"failure", "error"}:
                continue
            for match in list(pattern.finditer(failure.text or ""))[:4]:
                reason, pending, failed = match.groups()
                result.append(
                    {
                        "case_index": index,
                        "reason": reason,
                        "pending": int(pending),
                        "failed": int(failed),
                    }
                )
    return result[:16]


def import_retry_diagnostics(path: Path) -> list[dict]:
    """Export only allowlisted retry outcome states, never server response text."""
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > MAX_REPORT_BYTES
    ):
        raise ValueError("Bounded browser report required")
    raw = path.read_bytes()
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise ValueError("Unsupported report framing")
    codes = (
        "factor_busy|factor_import_busy|factor_capacity|factor_recovery_required|"
        "factor_storage_uncertain|factor_admission_lost|factor_admission_timeout|"
        "factor_import_unavailable|idempotency_conflict|unauthorized|"
        "maintenance_busy|none|other"
    )
    pattern = re.compile(
        r"FACTOR_IMPORT_RETRY_DIAGNOSTIC_V1:status=([1-5][0-9]{2});code=("
        + codes
        + r");identity=(same|different)(?![A-Za-z0-9_])"
    )
    result = []
    for index, case in enumerate(list(ET.fromstring(raw).iter("testcase"))[:512]):
        for failure in case:
            if failure.tag not in {"failure", "error"}:
                continue
            for match in list(pattern.finditer(failure.text or ""))[:4]:
                status, code, identity = match.groups()
                result.append(
                    {
                        "case_index": index,
                        "http_status": int(status),
                        "code": code,
                        "identity": identity,
                    }
                )
    return result[:16]


def import_status_diagnostics(path: Path) -> list[dict]:
    """Failure-only finite observation; no response identity or exception text."""
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > MAX_REPORT_BYTES
    ):
        raise ValueError("Bounded browser report required")
    with path.open("rb") as stream:
        raw = stream.read(MAX_REPORT_BYTES + 1)
    if len(raw) > MAX_REPORT_BYTES or b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise ValueError("Unsupported report framing")
    codes = (
        "factor_busy|factor_import_busy|factor_capacity|factor_recovery_required|"
        "factor_storage_uncertain|factor_admission_lost|factor_admission_timeout|"
        "factor_import_unavailable|idempotency_conflict|unauthorized|"
        "maintenance_busy|factor_storage_unavailable|database_unavailable|none|other"
    )
    pattern = re.compile(
        r"FACTOR_IMPORT_STATUS_DIAGNOSTIC_V1:http=(none|[1-5][0-9]{2});"
        r"status=(unknown|queued|processing|succeeded|failed|blocked);error=("
        + codes
        + r");generation=(unknown|[0-3]);envelope=(true|false);binding=(true|false)(?![A-Za-z0-9_;=-])"
    )
    result = []
    for index, case in enumerate(list(ET.fromstring(raw).iter("testcase"))[:512]):
        for failure in case:
            if failure.tag not in {"failure", "error"}:
                continue
            for match in list(pattern.finditer(failure.text or ""))[:1]:
                http, status, error, generation, envelope, binding = match.groups()
                result.append(
                    {
                        "case_index": index,
                        "http_status": None if http == "none" else int(http),
                        "status": status,
                        "error": error,
                        "generation": None
                        if generation == "unknown"
                        else int(generation),
                        "envelope": envelope == "true",
                        "binding": binding == "true",
                    }
                )
    return result[:16]


def validated_source(source, root):
    if (
        source.get("GITHUB_ACTIONS") != "true"
        or source.get("MINEAGENT_REQUIRE_FACTOR_BROWSER") != "1"
        or source.get("FACTOR_LIBRARY_ENABLED") != "true"
        or source.get("DATA_PROVIDER_MODE") != "fixture"
    ):
        raise ValueError(
            "Explicit disposable enabled factor fixture acceptance required"
        )
    if source.get("FACTOR_IMPORT_ENABLED") not in {"true", "false"}:
        raise ValueError("Explicit factor import capability profile required")
    if source["FACTOR_IMPORT_ENABLED"] == "true" and not re.fullmatch(
        r"sha256:[0-9a-f]{64}", source.get("MINEAGENT_FACTOR_PARSER_IMAGE", "")
    ):
        raise ValueError(
            "Enabled import acceptance requires its immutable parser image"
        )
    checked = dict(source)
    checked.update(
        TRADEGRAD_REQUIRE_APPLICATION_BROWSER="1",
        TRADEGRAD_APPLICATION_STAGE="m5",
        E2E_LIVE_PROVIDERS="0",
        TRADEGRAD_BROWSER_DIAGNOSTIC="",
    )
    if validate_source(checked, root) != "fixture":
        raise ValueError("Only synthetic factor browser evidence is supported")
    return checked


def node_environment(source):
    result = browser_environment(source)
    result.update(
        CI="true",
        E2E_API_URL=API,
        E2E_BASE_URL=FRONTEND,
        E2E_FACTOR_ACCEPTANCE="1",
        E2E_FACTOR_IMPORT_ACCEPTANCE="1"
        if source.get("FACTOR_IMPORT_ENABLED") == "true"
        else "0",
        E2E_LIVE_PROVIDERS="0",
        E2E_MODEL_PROFILES="0",
    )
    return result


def exercise(owner, root, source, private, node):
    services = service_environment(source, "fixture")
    imports = source["FACTOR_IMPORT_ENABLED"] == "true"
    services.update(
        FACTOR_LIBRARY_ENABLED="true",
        FACTOR_IMPORT_ENABLED=source["FACTOR_IMPORT_ENABLED"],
    )
    if imports:
        services["FACTOR_PARSER_IMAGE_ID"] = source["MINEAGENT_FACTOR_PARSER_IMAGE"]
    # The HTTP API enqueues opaque archives only. Existing trusted research
    # workers own isolated parser admission; no parser is launched by the API.
    services.pop("TRADEGRAD_DOCKER_HOST", None)
    owner.spawn(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.app.asgi:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ],
        cwd=root,
        env=services,
        service=True,
    )
    if imports:
        research_python = root / ".venv-research/bin/python"
        if not research_python.is_file():
            raise ValueError(
                "Separately hash-locked research worker runtime is required"
            )
        worker_environment = dict(services)
        worker_environment["TRADEGRAD_DOCKER_HOST"] = "unix:///var/run/docker.sock"
        owner.spawn(
            [str(research_python), "-m", "workers.research", "worker"],
            cwd=root,
            env=worker_environment,
            service=True,
        )
        owner.spawn(
            [sys.executable, "-m", "workers.research", "reconciler"],
            cwd=root,
            env=services,
            service=True,
        )
    vite = base_environment(source)
    vite.update(API_PROXY_TARGET=API, VITE_DEV_HOST="127.0.0.1")
    owner.spawn(
        [
            node,
            str(root / "frontend/node_modules/vite/bin/vite.js"),
            "--host",
            "127.0.0.1",
            "--port",
            "5173",
            "--strictPort",
        ],
        cwd=root / "frontend",
        env=vite,
        service=True,
    )
    wait_ready(owner)
    report = private / "browser.xml"
    environment = node_environment(source)
    environment["PLAYWRIGHT_JUNIT_OUTPUT_FILE"] = str(report)
    command = browser_command(node, root)
    command[3] = "tests/e2e/factor-workbench.spec.ts"
    command[-1] = f"--global-timeout={BROWSER_SECONDS * 1000}"
    command += ["--reporter=junit", "--output", str(private / "browser-output")]
    try:
        owner.run(
            command,
            cwd=root / "frontend",
            env=environment,
            seconds=BROWSER_SECONDS + 15,
        )
    finally:
        if report.is_file() and not report.is_symlink():
            # Paths and test line numbers only; never dump browser/private errors.
            import json

            print(
                "FACTOR_BROWSER_SOURCE_DIAGNOSTICS:"
                + json.dumps(failure_sites(report, root), sort_keys=True)
            )
            print(
                "FACTOR_CREATE_DIAGNOSTICS:"
                + json.dumps(create_diagnostics(report), sort_keys=True)
            )
            print(
                "FACTOR_IMPORT_STATUS_DIAGNOSTICS:"
                + json.dumps(import_status_diagnostics(report), sort_keys=True)
            )
            print(
                "FACTOR_IMPORT_RETRY_DIAGNOSTICS:"
                + json.dumps(import_retry_diagnostics(report), sort_keys=True)
            )
            print(
                "FACTOR_RESPONSE_DIAGNOSTICS:"
                + json.dumps(response_diagnostics(report), sort_keys=True)
            )
            print(
                "FACTOR_READ_LIFECYCLE_DIAGNOSTICS:"
                + json.dumps(read_lifecycle_diagnostics(report), sort_keys=True)
            )
            summarize(report, evidence_path(root, source))
    verify_report(report, "browser-import" if imports else "browser")


def evidence_path(root, source):
    profile = (
        "browser-import" if source.get("FACTOR_IMPORT_ENABLED") == "true" else "browser"
    )
    return root / "var/ci-evidence" / f"factor-{profile}-junit.xml"


def parser_absence(owner, root, source, private):
    # Read-only inventory, never label-based termination. A surviving isolated
    # operation requires its exact durable journal recovery, not a guessed kill.
    config = private / "docker-config"
    config.mkdir(mode=0o700)
    (config / "config.json").write_text('{"auths":{}}')
    output = owner.run(
        [
            "docker",
            "--config",
            str(config),
            "--host",
            "unix:///var/run/docker.sock",
            "ps",
            "--all",
            "--quiet",
            "--no-trunc",
            "--filter",
            "label=io.mineagent.managed=mineagent-factor-parser-v1",
        ],
        cwd=root,
        env=base_environment(source),
        seconds=15,
        maximum=65536,
        cleanup=True,
    )
    if output.strip():
        raise ValueError(
            "Parser absence is unconfirmed; exact journal recovery is required"
        )


def run(source, root):
    checked = validated_source(source, root)
    node = shutil.which("node", path=checked["PATH"])
    if not node or evidence_path(root, checked).exists():
        raise ValueError("Matched Node and a fresh evidence destination are required")
    owner = ProcessOwner(time.monotonic() + TOTAL_SECONDS)
    owner.contain()
    passed = False
    stopped = False
    with tempfile.TemporaryDirectory(prefix="mineagent-factors-") as directory:
        try:
            exercise(owner, root, checked, Path(directory), node)
            owner.check()
            passed = True
        except BaseException:  # noqa: BLE001 - private failures still require cleanup.
            passed = False
        finally:
            try:
                stopped = owner.cleanup()
                if stopped and checked["FACTOR_IMPORT_ENABLED"] == "true":
                    parser_absence(owner, root, checked, Path(directory))
                    stopped = owner.cleanup()
            except BaseException:  # noqa: BLE001 - uncertainty must fail closed.
                stopped = False
            finally:
                try:
                    final_stop = owner.cleanup()
                    stopped = stopped and final_stop
                except BaseException:  # noqa: BLE001 - no private cleanup details.
                    stopped = False
            print(
                "FACTOR_BROWSER_OWNED_CLEANUP:"
                + ("confirmed" if stopped else "unconfirmed")
            )
    return 0 if passed and stopped and not owner.interrupted else 1


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--verify", choices=("browser", "browser-import", "postgres", "isolation")
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        if args.verify:
            if args.report is None:
                raise ValueError("Explicit report required")
            verify_report(args.report, args.verify)
            summarize(
                args.report, Path("var/ci-evidence") / f"factor-{args.verify}-junit.xml"
            )
            return 0
        if args.report:
            raise ValueError("Report path is valid only for verification")
        return run(os.environ, Path(__file__).resolve().parents[1])
    except BaseException:  # noqa: BLE001 - never emit private child diagnostics.
        print(
            "Factor acceptance failed or evidence/owned cleanup is unconfirmed",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
