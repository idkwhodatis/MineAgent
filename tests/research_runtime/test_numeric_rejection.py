"""Exact protected-byte and filesystem regressions; not Docker acceptance."""

import hashlib
from datetime import timedelta

import pytest

from packages.research_runtime.numeric_rejection import (
    NumericRejection,
    parse_rejection_payload,
    validate_rejection_journal,
)
from packages.research_runtime.process_contracts import ProcessError, digest
from runtimes.factor_execution.protected_output import rejection_metadata
from runtimes.research_supervisor.numeric_rejection import read_rejection_journal
from runtimes.research_supervisor.supervisor import write_record
from tests.research_runtime.test_numeric_process_boundary import journal


def rejection(launch, identity, stop, code=1):
    raw = rejection_metadata("result.h5", code)
    return NumericRejection(
        schema_version="mineagent.numeric-process-rejection.v1",
        launch_sha256=digest(launch.model_dump(mode="json")),
        identity_sha256=digest(identity.model_dump(mode="json")),
        stop_sha256=digest(stop.model_dump(mode="json")),
        container_id=identity.container_id,
        protocol="MANU2-kind3",
        basename="result.h5",
        payload_utf8=raw.decode(),
        payload_sha256=hashlib.sha256(raw).hexdigest(),
        child_exit_code=code,
        descendants_reaped=True,
        complete_eof=True,
    )


def retained(root, code=1):
    admission, launch, identity, stop, extraction, _raw, path = journal(root)
    (path / "control" / "extraction.json").unlink()
    (path / "output" / extraction.basename).unlink()
    value = rejection(launch, identity, stop, code)
    write_record(path / "control" / "rejection.json", value.model_dump(mode="json"))
    return admission, launch, identity, stop, value, path


@pytest.mark.parametrize("code", [-64, -9, 0, 1, 124, 255])
def test_protected_child_exit_not_guardian_exit(tmp_path, code):
    admission, launch, identity, stop, value, _ = retained(tmp_path, code)
    assert read_rejection_journal(tmp_path, admission) == (
        launch,
        identity,
        stop,
        value,
    )
    assert value.outcome == ("output_rejected" if code == 0 else "child_nonzero")


@pytest.mark.parametrize(
    "raw",
    [
        b"null",
        b"[]",
        b"{}",
        b"",
        b"x" * 1025,
        rejection_metadata("values.parquet", 1),
        rejection_metadata("result.h5", 1) + b"\n",
        rejection_metadata("result.h5", 1).replace(
            b'"child_exit_code":1', b'"child_exit_code":true'
        ),
        rejection_metadata("result.h5", 1).replace(
            b'"child_exit_code":1', b'"child_exit_code":1.0'
        ),
        rejection_metadata("result.h5", 1).replace(
            b'"child_exit_code":1', b'"child_exit_code":1,"child_exit_code":1'
        ),
        rejection_metadata("result.h5", 1).replace(
            b'"descendants_reaped":true', b'"descendants_reaped":false'
        ),
    ],
)
def test_noncanonical_or_wrong_scope_frame_rejected(raw):
    with pytest.raises(ProcessError, match="rejection_payload"):
        parse_rejection_payload(raw)


@pytest.mark.parametrize(
    "field", ["launch_sha256", "identity_sha256", "stop_sha256", "container_id"]
)
def test_rehashed_detached_record_does_not_match_full_chain(tmp_path, field):
    _, launch, identity, stop, value, _ = retained(tmp_path)
    value = value.model_copy(update={field: "f" * 64})
    with pytest.raises(ProcessError, match="rejection_identity"):
        validate_rejection_journal(launch, identity, stop, value)


@pytest.mark.parametrize("mutation", ["late", "overdue", "normalizer"])
def test_untimely_or_wrong_role_stop_not_rejection_authority(tmp_path, mutation):
    _, launch, identity, stop, _, _, _ = journal(
        tmp_path,
        "factor_normalizer" if mutation == "normalizer" else "factor_cutoff_candidate",
    )
    if mutation == "late":
        stop = stop.model_copy(
            update={"observed_at": launch.intent.not_after + timedelta(seconds=2)}
        )
    if mutation == "overdue":
        stop = stop.model_copy(update={"deadline_bound_met": False})
    value = rejection(launch, identity, stop)
    with pytest.raises(ProcessError):
        validate_rejection_journal(launch, identity, stop, value)


@pytest.mark.parametrize(
    "mutation", ["output", "extraction", "extra", "missing", "symlink"]
)
def test_rejection_retained_inventory_is_closed(tmp_path, mutation):
    admission, _, _, _, _, path = retained(tmp_path)
    target = path / "control" / "rejection.json"
    if mutation == "output":
        (path / "output" / "result.h5").write_bytes(b"unqualified")
    elif mutation in {"extraction", "extra"}:
        write_record(path / "control" / (mutation + ".json"), {})
    elif mutation == "missing":
        target.unlink()
    else:
        raw = target.read_bytes()
        target.unlink()
        (path / "other").write_bytes(raw)
        target.symlink_to(path / "other")
    with pytest.raises((OSError, ProcessError)):
        read_rejection_journal(tmp_path, admission)


@pytest.mark.parametrize("flag", ["descendants_reaped", "complete_eof"])
@pytest.mark.parametrize("value", [1, "true", False, None])
def test_rejection_flags_never_accept_truthy_substitutions(tmp_path, flag, value):
    _, _, _, _, proof, _ = retained(tmp_path)
    raw = proof.model_dump(mode="json")
    raw[flag] = value
    with pytest.raises(ValueError):
        NumericRejection.model_validate(raw)


@pytest.mark.parametrize("flag", ["descendants_reaped", "complete_eof"])
def test_forged_model_copy_cannot_bypass_literal_true(tmp_path, flag):
    _, _, _, _, proof, _ = retained(tmp_path)
    with pytest.raises(ValueError):
        NumericRejection.model_validate(proof.model_copy(update={flag: 1}))
