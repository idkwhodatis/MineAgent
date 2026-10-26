"""No native imports: protocol/driver guards only, never actual Docker proof."""

import ast
import copy
import hashlib
import json

import pytest

from runtimes.factor_execution.adverse_acceptance import (
    OUTPUT_REJECTIONS,
    fixture_source,
    normalization_inputs,
    require_report,
)
from runtimes.factor_execution.adverse_fixture import CASES
from runtimes.factor_execution.protected_output import (
    MAGIC,
    parse_frame,
    rejection_metadata,
    validate_rejection,
)


@pytest.mark.parametrize("case", CASES)
def test_only_closed_maintained_sources(case):
    source = fixture_source(case)
    tree = ast.parse(source)
    assert ast.literal_eval(tree.body[0].value) == case
    assert b'create_case(CASE, Path("/output/result.h5"))' in source


@pytest.mark.parametrize("case", ["unknown", "'; import os", None, [], 1])
def test_unknown_source_rejected(case):
    with pytest.raises(ValueError):
        fixture_source(case)


def test_opaque_input_manifest_commits_native_bytes_without_parsing():
    files = normalization_inputs(b"not HDF", b"[]", 123)
    manifest = json.loads(files["/input/manifest.json"])
    assert manifest["hdf_sha256"] == hashlib.sha256(b"not HDF").hexdigest()
    assert files["/input/result.h5"] == b"not HDF"


@pytest.mark.parametrize("code", [0, 2, 42, -11, -9, 255])
def test_rejection_has_exact_observed_child_outcome(code):
    payload = rejection_metadata("values.parquet", code)
    validate_rejection(
        payload, expected_basename="values.parquet", expected_exit_code=code
    )
    if code != 2:
        with pytest.raises(ValueError):
            validate_rejection(
                payload, expected_basename="values.parquet", expected_exit_code=2
            )


@pytest.mark.parametrize(
    "change",
    [
        {"child_exit_code": True},
        {"child_exit_code": "2"},
        {"child_exit_code": -11},
        {"child_exit_code": 0},
        {"descendants_reaped": False},
        {"descendants_reaped": 1},
        {"basename": "result.h5"},
        {"schema": "mineagent.numeric-quiescence.v1"},
        {"error": "candidate bytes"},
    ],
)
def test_rejection_forged_metadata_rejected(change):
    data = json.loads(rejection_metadata("values.parquet", 2))
    data.update(change)
    with pytest.raises(ValueError):
        validate_rejection(
            json.dumps(data, sort_keys=True, separators=(",", ":")).encode(),
            expected_basename="values.parquet",
            expected_exit_code=2,
        )


@pytest.mark.parametrize("payload", [b"", b"{}", b"null", b"\xff", b"[]", b" " * 10000])
def test_empty_old_or_malformed_rejections_fail(payload):
    with pytest.raises(ValueError):
        validate_rejection(
            payload, expected_basename="values.parquet", expected_exit_code=2
        )


def test_wire_version_is_explicit_and_old_frames_fail():
    assert MAGIC == b"MANU2\0"
    with pytest.raises(ValueError):
        parse_frame(b"MANU1\0\x03\0\0\0\0")


def proof():
    cases = []
    for case in CASES:
        roles = (
            ["candidate"] if case in OUTPUT_REJECTIONS else ["candidate", "normalizer"]
        )
        units = []
        for role in roles:
            expected = OUTPUT_REJECTIONS.get(case) if role == "candidate" else 2
            unit = dict.fromkeys(
                (
                    "positive_stop_verified",
                    "original_deadline_verified",
                    "complete_output_stream_verified",
                    "frame_received_while_unit_alive",
                    "launch_owner_killed",
                    "all_observed_descendants_stopped",
                    "protected_quiescence_frame_verified",
                ),
                True,
            )
            unit.update(
                role=role,
                expected_rejection_exit_code=expected,
                observed_child_exit_code=0 if expected is None else expected,
                exit_code=124,
                elapsed_seconds=8.1,
                output_bytes=0 if expected is not None else 100,
            )
            units.append(unit)
        cases.append({"name": case, "units": units})
    return {
        "scope": "disabled_maintained_numeric_adverse_only",
        "arbitrary_candidate_admission": False,
        "cases": cases,
    }


def test_complete_closed_matrix():
    require_report(proof())


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "duplicate",
        "crash",
        "late",
        "not_stopped",
        "exported",
        "activation",
        "missing_unit",
    ],
)
def test_report_cannot_promote_missing_or_crashed_cases(mutation):
    data = copy.deepcopy(proof())
    if mutation == "missing":
        data["cases"].pop()
    if mutation == "duplicate":
        data["cases"][-1] = data["cases"][0]
    if mutation == "crash":
        data["cases"][0]["units"][1]["expected_rejection_exit_code"] = -11
    if mutation == "late":
        data["cases"][0]["units"][1]["elapsed_seconds"] = 10
    if mutation == "not_stopped":
        data["cases"][0]["units"][1]["positive_stop_verified"] = False
    if mutation == "exported":
        data["cases"][0]["units"][1]["output_bytes"] = 1
    if mutation == "activation":
        data["arbitrary_candidate_admission"] = True
    if mutation == "missing_unit":
        data["cases"][0]["units"].pop()
    with pytest.raises(ValueError):
        require_report(data)
