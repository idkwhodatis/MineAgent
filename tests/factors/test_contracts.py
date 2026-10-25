from uuid import UUID

import pytest
from pydantic import ValidationError

from packages.factors.contracts import *
from packages.factors.fixtures import demo_fixture, fixture_definition


def fixture():
    return demo_fixture(
        factor_version_id=UUID(int=1),
        dataset_version_id=UUID(int=2),
        snapshot_id=UUID(int=3),
    )


def rebuild(model, **changes):
    return type(model).model_validate({**model.model_dump(mode="json"), **changes})


def test_immutable_definition_parameters():
    d = fixture_definition("momentum")
    with pytest.raises(ValidationError):
        d.lookback_sessions = 3
    with pytest.raises(ValidationError):
        d.parameters[0].value = "other"
    assert rebuild(d, lookback_sessions=3).sha256 != d.sha256
    assert rebuild(d, output_unit="bp").sha256 != d.sha256


@pytest.mark.parametrize(
    "field,value",
    [
        ("lookback_sessions", True),
        ("lag_sessions", -1),
        ("frequency", "1m"),
        ("missing_policy", "fill_zero"),
        ("code_sha256", "A" * 64),
        ("surprise", 1),
    ],
)
def test_definition_rejects_invalid(field, value):
    with pytest.raises(ValueError):
        rebuild(fixture_definition("momentum"), **{field: value})


@pytest.mark.parametrize("code", ["", " ", "a\0b", "a" * 131073, "\ud800"])
def test_code_byte_contract(code):
    with pytest.raises(ValueError):
        source_sha256(code)


def test_source_not_normalized():
    assert source_sha256("x=1\r\n") != source_sha256("x=1\n")


def test_complete_input_digest():
    old = fixture().manifest.input
    for field, child in [
        ("dataset", "availability_revision_sha256"),
        ("dataset", "content_sha256"),
        ("dataset", "input_files_sha256"),
        ("calculation_universe", "membership_sha256"),
    ]:
        raw = old.model_dump(mode="json")
        raw[field][child] = "a" * 64
        assert SnapshotInput.model_validate(raw).sha256 != old.sha256
    for field in ("calendar_sha256", "environment_sha256"):
        assert rebuild(old, **{field: "a" * 64}).sha256 != old.sha256


def test_input_version_and_price_mismatch():
    old = fixture().manifest.input
    for field, child, value in [
        ("version", "definition_sha256", "a" * 64),
        ("dataset", "price_semantics", "split_adjusted"),
    ]:
        raw = old.model_dump(mode="json")
        raw[field][child] = value
        with pytest.raises(ValueError):
            SnapshotInput.model_validate(raw)


@pytest.mark.parametrize(
    "change",
    [
        {"value": False},
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": None},
        {"available_at": "2024-03-08T21:00:00"},
        {"available_at": "2024-03-08T20:59:59Z"},
        {"input_window_end": "2024-03-10"},
        {"missing_reason": "pretend_missing"},
    ],
)
def test_invalid_rows(change):
    with pytest.raises(ValueError):
        rebuild(fixture().rows[6], **change)


def test_valid_zero_and_explicit_missing():
    row = fixture().rows[6]
    assert rebuild(row, value=0).value == 0
    assert (
        rebuild(
            row, value=None, value_state="input_missing", missing_reason="missing"
        ).value
        is None
    )


def test_row_inventory_unique_order_owner_binding():
    f = fixture()
    for rows in [
        (f.rows[0], f.rows[0]),
        tuple(reversed(f.rows)),
        (rebuild(f.rows[0], listing_id="foreign"),),
        (rebuild(f.rows[0], factor_version_id=str(UUID(int=99))),),
    ]:
        with pytest.raises(ValueError):
            validate_rows(f.manifest.input, rows)


def test_manifest_identity_and_counts():
    m = fixture().manifest
    for changes in ({"valid_count": 1}, {"input_digest": "a" * 64}):
        with pytest.raises(ValueError):
            rebuild(m, **changes)
    obj = {
        "role": "code",
        "ordinal": 0,
        "sha256": "a" * 64,
        "size_bytes": 5,
        "name": "source.py",
    }
    with pytest.raises(ValueError):
        rebuild(m, objects=[obj, obj])


def test_verification_cannot_make_fixture_pit_source():
    assert imported_verification(evidence_sha256="a" * 64).source_pit_verified is False
    with pytest.raises(ValueError):
        LocalVerification(
            kind="fixed_fixture_recomputed",
            admission_profile="fixture",
            evidence_sha256="a" * 64,
            source_pit_verified=True,
        )


def test_known_and_unknown_exposure_retained():
    with pytest.raises(ValueError):
        validate_history_inheritance(
            ExposureHistory(), ExposureHistory(completeness="local_observed")
        )
    claim = ExposureClaim(
        origin_namespace="origin",
        evidence_id="screened",
        evidence_sha256="a" * 64,
        window={"start": "2024-01-01", "end": "2024-02-01"},
        purpose="screening",
        identity_mapping="verified_local_mapping",
        mapping_sha256="b" * 64,
    )
    parent = ExposureHistory(claims=(claim,), completeness="local_observed")
    with pytest.raises(ValueError):
        validate_history_inheritance(
            parent, ExposureHistory(completeness="local_observed")
        )
    validate_history_inheritance(parent, parent)


def test_analysis_types_and_failure_not_zero():
    p = AnalysisProtocol(
        kind="direct_factor_analysis",
        snapshot_id=UUID(int=1),
        snapshot_output_sha256="a" * 64,
        window={"start": "2024-01-01", "end": "2024-02-01"},
        label_sha256="b" * 64,
        label_horizon_sessions=1,
        calculation_universe_sha256="c" * 64,
        estimator_version="fixture-not-qlib",
    )
    assert AnalysisResult(protocol=p, status="not_evaluated").ic is None
    with pytest.raises(ValueError):
        AnalysisResult(protocol=p, status="failed", ic=0, error_code="failed")
    with pytest.raises(ValueError):
        AnalysisResult(protocol=p, status="succeeded", cumulative_return=0.2)
    with pytest.raises(ValueError):
        rebuild(p, kind="native_portfolio_analysis")


def test_stage_exact_identity_and_output_constraints():
    call = StageCall(
        request_id=UUID(int=1),
        decision_id="d",
        event_seq=1,
        stage="score",
        input_digest="a" * 64,
        listing_ids=("a", "b"),
    )
    reply = StageReply(
        request_id=call.request_id,
        decision_id=call.decision_id,
        event_seq=1,
        stage="score",
        input_digest=call.input_digest,
        values=({"listing_id": "a", "value": 0}, {"listing_id": "b", "value": 1}),
    )
    validate_stage_reply(call, reply)
    with pytest.raises(ValueError):
        validate_stage_reply(call, rebuild(reply, input_digest="b" * 64))
    with pytest.raises(ValueError):
        validate_stage_reply(
            call, rebuild(reply, values=[{"listing_id": "a", "value": 0}])
        )
    with pytest.raises(ValueError):
        rebuild(reply, values=[{"listing_id": "a", "value": True}])
    with pytest.raises(ValueError):
        rebuild(
            reply,
            stage="allocation",
            values=[
                {"listing_id": "a", "value": 0.8},
                {"listing_id": "b", "value": 0.8},
            ],
        )
