"""Project allowlisted source locations and typed HTTP diagnostics from private XML."""

import json
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from xml.etree import ElementTree as ET

NOTE_HTTP_PREFIX = "RESEARCH_NOTE_HTTP_DIAGNOSTIC_V1:"
NOTE_HTTP_CATEGORIES = {
    "research_note_create",
    "research_note_revision",
    "controlled_note_create",
}
NOTE_HTTP_CODES = {
    "admission_required",
    "artifact_missing",
    "comparison_unavailable",
    "database_unavailable",
    "diagnostic_parity_pending",
    "final_binding_mismatch",
    "forbidden",
    "idempotency_conflict",
    "independent_integrity",
    "independent_source_depth",
    "internal_error",
    "invalid_artifact",
    "invalid_final_config",
    "invalid_note_pin",
    "invalid_portable_config",
    "invalid_split",
    "note_admission_capacity",
    "note_admission_unavailable",
    "note_corpus_capacity",
    "note_evidence_capacity",
    "note_evidence_pin_mismatch",
    "note_evidence_unavailable",
    "note_integrity",
    "note_restore_mismatch",
    "note_revision_capacity",
    "note_revision_conflict",
    "note_revision_pin_mismatch",
    "not_found",
    "result_unavailable",
    "source_integrity",
    "spec_integrity",
    "spec_unavailable",
    "split_integrity",
    "trace_artifact_missing",
    "trace_integrity",
    "trace_unavailable",
    "unauthorized",
    "unrecognized",
    "unsafe_storage_path",
    "validation_error",
}
NOTE_HTTP_FIELDS = {
    "Idempotency-Key",
    "applicability",
    "arms",
    "base_revision",
    "base_revision_sha256",
    "behavior_evidence",
    "body",
    "change_note",
    "code_sha256",
    "comparison_id",
    "config_sha256",
    "content",
    "control_results",
    "controlled_experiment_id",
    "counterexamples",
    "decision_id",
    "evaluation_id",
    "event_pair_id",
    "evidence",
    "execution_attempt_id",
    "file",
    "generation",
    "hypothesis",
    "input_sha256",
    "instrument_id",
    "kind",
    "label",
    "leaf",
    "left",
    "line_end",
    "line_start",
    "manifest_sha256",
    "mechanism_index",
    "motivation",
    "next_steps",
    "note_id",
    "ordinal",
    "protocol_sha256",
    "record_id",
    "record_kind",
    "record_ordinal",
    "report_sha256",
    "request_sha256",
    "restore",
    "result",
    "result_manifest_sha256",
    "right",
    "rule_id",
    "rules",
    "run_id",
    "section",
    "selected_arm",
    "selection",
    "session_date",
    "source_id",
    "source_kind",
    "source_ref",
    "sources",
    "spec_revision",
    "spec_sha256",
    "stage_id",
    "status",
    "strategy_version_id",
    "symbol",
    "tags",
    "title",
    "trace_manifest_sha256",
}


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate diagnostic key")
        value[key] = item
    return value


TOKEN_DIAGNOSTIC_PREFIX = "TOKEN_DIAGNOSTIC_V1:"


def _token_diagnostics(text):
    """Closed phase/status projection; untrusted sentinel is not authorization."""
    values = {}
    for count, match in enumerate(
        re.finditer(re.escape(TOKEN_DIAGNOSTIC_PREFIX), text)
    ):
        if count >= 32:
            break
        payload = (
            text[match.end() : match.end() + 257].split("\n", 1)[0].removesuffix("\r")
        )
        if len(payload) > 256:
            continue
        try:
            value = json.loads(payload, object_pairs_hook=_unique_object)
        except (ValueError, RecursionError):
            continue
        if not isinstance(value, dict) or set(value) != {"phase", "http_status"}:
            continue
        phase, status = value["phase"], value["http_status"]
        if type(phase) is not str or phase not in {"request", "http", "decode"}:
            continue
        if phase == "request":
            if status is not None:
                continue
        elif type(status) is not int or not 100 <= status <= 599:
            continue
        values[json.dumps(value, sort_keys=True)] = value
    return [values[key] for key in sorted(values)[:4]]


COMPARISON_STATUS_PREFIX = "COMPARISON_STATUS_DIAGNOSTIC_V1:"
COMPARISON_STATUSES = {
    "queued",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "interrupted",
    "paused",
    "cancel_requested",
    "pause_requested",
    "unrecognized",
}
COMPARISON_ERROR_CODES = {
    "comparison_failed",
    "comparison_cancelled",
    "comparison_interrupted",
    "comparison_timeout",
    "comparison_capacity",
    "comparison_integrity",
    "comparison_algorithm_unsupported",
    "comparison_source_unavailable",
    "comparison_pin_mismatch",
    "comparison_recovery_blocked",
    "comparison_query_too_large",
    "unrecognized",
}


def _comparison_status_diagnostics(text):
    values = {}
    for count, match in enumerate(
        re.finditer(re.escape(COMPARISON_STATUS_PREFIX), text)
    ):
        if count >= 32:
            break
        payload = (
            text[match.end() : match.end() + 514].split("\n", 1)[0].removesuffix("\r")
        )
        if (
            len(payload) > 512
            or not payload.startswith("{")
            or not payload.endswith("}")
        ):
            continue
        try:
            value = json.loads(payload, object_pairs_hook=_unique_object)
        except (ValueError, RecursionError):
            continue
        if not isinstance(value, dict) or set(value) != {
            "presence",
            "status",
            "error_code",
        }:
            continue
        if (
            type(value["presence"]) is not str
            or value["presence"] not in {"missing", "single", "multiple"}
            or type(value["status"]) is not str
            or value["status"] not in COMPARISON_STATUSES
            or (
                value["error_code"] is not None
                and (
                    type(value["error_code"]) is not str
                    or value["error_code"] not in COMPARISON_ERROR_CODES
                )
            )
            or (
                value["presence"] != "single"
                and (
                    value["status"] != "unrecognized" or value["error_code"] is not None
                )
            )
        ):
            continue
        values[json.dumps(value, sort_keys=True)] = value
    return [values[key] for key in sorted(values)[:4]]


def _note_http_diagnostics(text):
    """An explicit sentinel is not trusted: validate every key and value again."""
    values = {}
    for count, match in enumerate(re.finditer(re.escape(NOTE_HTTP_PREFIX), text)):
        if count >= 32:
            break
        # Limit candidate count as well as length; repeated malformed markers
        # must not trigger unbounded JSON parsing or regex backtracking.
        payload = (
            text[match.end() : match.end() + 2050].split("\n", 1)[0].removesuffix("\r")
        )
        if (
            len(payload) > 2048
            or not payload.startswith("{")
            or not payload.endswith("}")
        ):
            continue
        try:
            value = json.loads(payload, object_pairs_hook=_unique_object)
        except (ValueError, RecursionError):
            continue
        if (
            not isinstance(value, dict)
            or set(value)
            != {"category", "http_status", "error_code", "validation_fields"}
            or not isinstance(value["category"], str)
            or value["category"] not in NOTE_HTTP_CATEGORIES
            or type(value["http_status"]) is not int
            or not 100 <= value["http_status"] <= 599
            or (
                value["error_code"] is not None
                and (
                    not isinstance(value["error_code"], str)
                    or value["error_code"] not in NOTE_HTTP_CODES
                )
            )
            or not isinstance(value["validation_fields"], list)
            or len(value["validation_fields"]) > 16
            or any(
                not isinstance(field, str) or field not in NOTE_HTTP_FIELDS
                for field in value["validation_fields"]
            )
            or value["validation_fields"] != sorted(set(value["validation_fields"]))
            or (
                value["error_code"] != "validation_error" and value["validation_fields"]
            )
        ):
            continue
        # A repeated assertion message cannot inflate or encode ordering in output.
        values[json.dumps(value, sort_keys=True)] = value
        if len(values) == 4:
            break
    return [values[key] for key in sorted(values)]


REPLAY_MARKER_PREFIX = "REPLAY_MARKER_DIAGNOSTIC_V2:"
_REPLAY_PRESENCE = {"missing", "single", "multiple"}
_REPLAY_TARGET = {
    "not_unique",
    "not_visible",
    "disabled",
    "outside_viewport",
    "center_hits_button",
    "center_blocked",
}


_REPLAY_VIEWPORT_EDGES = {"above", "below", "left", "right", "both_axes"}
_REPLAY_CLICK_REPORTS = {
    "unreported",
    "strict_locator",
    "waiting_for_locator",
    "waiting_for_actionability",
    "detached",
    "outside_viewport",
    "intercepted",
    "scroll_pending",
    "scrolled",
    "performing_click",
    "click_performed",
}


def _replay_marker_diagnostics(text):
    values = {}
    for count, match in enumerate(re.finditer(re.escape(REPLAY_MARKER_PREFIX), text)):
        if count >= 32:
            break
        payload = text[match.end() : match.end() + 385].split("\n", 1)[0]
        if len(payload) > 384:
            continue
        try:
            record = json.loads(payload, object_pairs_hook=_unique_object)
        except (ValueError, RecursionError):
            continue
        if type(record) is not dict or set(record) != {
            "table",
            "page_matches",
            "table_matches",
            "listing_matches",
            "window_matches",
            "target",
            "viewport_edge",
            "table_center_clipped",
            "click_report",
        }:
            continue
        if any(
            type(record[key]) is not str or record[key] not in _REPLAY_PRESENCE
            for key in ("table", "page_matches")
        ):
            continue
        scoped = record["table_matches"]
        if record["table"] == "single":
            if type(scoped) is not str or scoped not in _REPLAY_PRESENCE:
                continue
        elif scoped is not None:
            continue
        if any(
            record[key] is not None and type(record[key]) is not bool
            for key in ("listing_matches", "window_matches")
        ):
            continue
        target = record["target"]
        if (
            type(target) is not str
            or target not in _REPLAY_TARGET
            or ((scoped == "single") == (target == "not_unique"))
        ):
            continue
        edge = record["viewport_edge"]
        table_center_clipped = record["table_center_clipped"]
        click_report = record["click_report"]
        if (
            (
                (type(edge) is not str or edge not in _REPLAY_VIEWPORT_EDGES)
                if target == "outside_viewport"
                else edge is not None
            )
            or (
                table_center_clipped is not None
                and (
                    type(table_center_clipped) is not bool
                    or target in {"not_unique", "not_visible", "disabled"}
                )
            )
            or type(click_report) is not str
            or click_report not in _REPLAY_CLICK_REPORTS
        ):
            continue
        values[json.dumps(record, sort_keys=True)] = record
    return [values[key] for key in sorted(values)[:2]]


@contextmanager
def private_browser_report(root: Path):
    """Keep raw browser evidence outside uploaded paths and delete it on exit."""
    with tempfile.TemporaryDirectory(
        prefix="operator-browser-report-", dir=root
    ) as folder:
        yield Path(folder) / "junit.xml"


def failure_diagnostic(source: Path, root: Path, *, source_suite="e2e") -> str:
    """Diagnostic failure can reveal neither report content nor exception text."""
    try:
        detail = json.dumps(
            failure_sites(source, root, source_suite=source_suite), sort_keys=True
        )
    except Exception:  # noqa: BLE001 - report/exception payloads are private
        detail = "unavailable"
    return "BROWSER_SOURCE_DIAGNOSTICS_V1:" + detail


def failure_sites(source: Path, root: Path, *, source_suite="e2e") -> list[dict]:
    # Fixed public source sets only; callers cannot select a filesystem path.
    suites = {
        "e2e": "e2e",
        "engine-results": "engine-results-acceptance",
        "structured-results": "structured-results-acceptance",
    }
    if type(source_suite) is not str or source_suite not in suites:
        raise ValueError("Unsupported browser source suite")
    if (
        source.is_symlink()
        or not source.is_file()
        or source.stat().st_size > 20_000_000
    ):
        raise ValueError("Browser report is unavailable")
    with source.open("rb") as stream:
        content = stream.read(20_000_001)
    if len(content) > 20_000_000 or b"<!DOCTYPE" in content or b"<!ENTITY" in content:
        raise ValueError("Unsupported report declarations")
    report = ET.fromstring(content)
    cases = list(report.iter("testcase"))
    if len(cases) > 512:
        raise ValueError("Browser case bound exceeded")
    allowed = {
        path.name: len(path.read_text().splitlines())
        for path in (root / "frontend/tests" / suites[source_suite]).glob("*.spec.ts")
        if path.is_file() and not path.is_symlink() and path.stat().st_size <= 1_000_000
    }
    # Explicit public helper sources only. A spec callsite does not identify
    # which navigation assertion failed; never widen this to arbitrary TS files.
    helper_names = {
        "engine-results": ("factor-library.ts",),
        "e2e": ("helpers.ts", "token-diagnostics.ts"),
    }
    for name in helper_names.get(source_suite, ()):
        path = root / "frontend/tests" / suites[source_suite] / name
        if (
            path.is_file()
            and not path.is_symlink()
            and path.stat().st_size <= 1_000_000
        ):
            allowed[name] = len(path.read_text().splitlines())
    result = []
    for index, case in enumerate(cases):
        failures = [child for child in case if child.tag in {"failure", "error"}]
        if not failures:
            continue
        frames = set()
        http_diagnostics = {}
        token_diagnostics = {}
        for failure in failures:
            # Only a known public source basename and bounded numeric line/column
            # escape. Never copy a title, message, argument, URL, body or raw stack.
            for match in re.finditer(
                r"(?:^|[\s(/])([A-Za-z0-9_-]+(?:\.spec)?\.ts):(\d{1,6}):(\d{1,5})(?=\D|$)",
                failure.text or "",
            ):
                name, line, column = match[1], int(match[2]), int(match[3])
                if (
                    name in allowed
                    and 1 <= line <= allowed[name]
                    and 1 <= column <= 10000
                ):
                    frames.add((name, line, column))
            for diagnostic in (
                _note_http_diagnostics(failure.text or "")
                if source_suite == "e2e"
                else ()
            ):
                http_diagnostics[json.dumps(diagnostic, sort_keys=True)] = diagnostic
            for diagnostic in (
                _token_diagnostics(failure.text or "") if source_suite == "e2e" else ()
            ):
                token_diagnostics[json.dumps(diagnostic, sort_keys=True)] = diagnostic
        comparison_diagnostics = {}
        replay_marker_diagnostics = {}
        if source_suite == "e2e":
            # Only failed-case per-test stdout; never suite output or raw text.
            for output in case.findall("system-out")[:4]:
                for diagnostic in _comparison_status_diagnostics(
                    (output.text or "")[:65536]
                ):
                    comparison_diagnostics[json.dumps(diagnostic, sort_keys=True)] = (
                        diagnostic
                    )
                for diagnostic in _replay_marker_diagnostics(
                    (output.text or "")[:65536]
                ):
                    replay_marker_diagnostics[
                        json.dumps(diagnostic, sort_keys=True)
                    ] = diagnostic
        result.append(
            {
                "case_index": index,
                **(
                    {
                        "comparison_diagnostics": [
                            comparison_diagnostics[key]
                            for key in sorted(comparison_diagnostics)[:4]
                        ]
                    }
                    if comparison_diagnostics
                    else {}
                ),
                "outcome": "failed",
                **(
                    {
                        "replay_marker_diagnostics": [
                            replay_marker_diagnostics[key]
                            for key in sorted(replay_marker_diagnostics)[:2]
                        ]
                    }
                    if replay_marker_diagnostics
                    else {}
                ),
                **(
                    {
                        "token_diagnostics": [
                            token_diagnostics[key]
                            for key in sorted(token_diagnostics)[:4]
                        ]
                    }
                    if token_diagnostics
                    else {}
                ),
                "source_frames": [
                    {"file": name, "line": line, "column": column}
                    for name, line, column in sorted(frames)[:12]
                ],
                **(
                    {
                        "http_diagnostics": [
                            http_diagnostics[key]
                            for key in sorted(http_diagnostics)[:4]
                        ]
                    }
                    if http_diagnostics
                    else {}
                ),
            }
        )
    return result
