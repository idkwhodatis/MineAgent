"""Real protected export/journal files; DB scope doubled, no Docker assertion."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from test_native_evaluator_output_reader import fixture

from backend.app import openevolve_native_evidence as s
from runtimes.openevolve_evaluator.output_reader import read_stopped_native_bytes


def setup(monkeypatch, tmp_path):
    admission, path, _files = fixture(tmp_path)
    physical = read_stopped_native_bytes(tmp_path, admission)
    receipts = {
        kind: NS(id=uuid4(), payload=value.model_dump(mode="json"))
        for kind, value in (
            ("stop", physical.stop),
            ("launch_identity", physical.identity),
            ("extraction", physical.extraction),
        )
    }
    state = NS(status="stopped")
    evaluation = NS(id=admission.logical_operation_id)
    monkeypatch.setattr(
        s, "persisted", lambda *a: (physical.launch.intent, state, receipts)
    )
    monkeypatch.setattr(s, "resolve_native_input", lambda *a: (evaluation, None, None))
    return NS(
        admission=admission,
        path=path,
        receipts=receipts,
        state=state,
        settings=NS(storage_root=tmp_path),
        evaluation=evaluation,
    )


def test_verified_opaque_export_invalid_native_result_is_not_success(
    monkeypatch, tmp_path
):
    c = setup(monkeypatch, tmp_path)
    receipt, value = s.read_native_evidence(None, None, c.admission.id, c.settings)
    assert receipt == c.receipts["stop"].id
    assert value == {
        "schema_version": "mineagent.native-failure-object.v1",
        "evaluation_id": str(c.evaluation.id),
        "failure_code": "invalid_result",
    }


def test_no_extraction_does_not_invent_timeout_or_success(monkeypatch, tmp_path):
    c = setup(monkeypatch, tmp_path)
    del c.receipts["extraction"]
    assert (
        s.read_native_evidence(None, None, c.admission.id, c.settings)[1][
            "failure_code"
        ]
        == "invalid_result"
    )


@pytest.mark.parametrize(
    "fault", ["active", "stop", "identity", "extraction", "missing", "changed"]
)
def test_uncertain_metadata_or_physical_corruption_propagates(
    monkeypatch, tmp_path, fault
):
    c = setup(monkeypatch, tmp_path)
    if fault == "active":
        c.state.status = "uncertain"
    if fault in {"stop", "identity", "extraction"}:
        kind = "launch_identity" if fault == "identity" else fault
        c.receipts[kind].payload = {}
    if fault == "missing":
        (c.path / "output/native-export.bin").unlink()
    if fault == "changed":
        (c.path / "output/native-export.bin").write_bytes(b"changed")
    with pytest.raises((ValueError, RuntimeError, OSError)):
        s.read_native_evidence(None, None, c.admission.id, c.settings)


@pytest.mark.parametrize(
    "stdout",
    [
        b"",
        b"TRADEGRAD_RESULT_V3:!",
        b"TRADEGRAD_RESULT_V3:e30=\nTRADEGRAD_RESULT_V3:e30=",
        b"\xff",
    ],
)
def test_native_protocol_not_substituted_by_arbitrary_stdout(stdout):
    with pytest.raises(ValueError):
        s.decode_native_files(
            {"metrics.json": b"{}", "stdout.bin": stdout, "stderr.bin": b""}
        )
