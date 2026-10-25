from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from packages.factors.contracts import (
    ExposureHistory,
    FactorBundle,
    FactorRunBinding,
    SnapshotBinding,
    StageReply,
    digest,
    rows_digest,
)
from packages.factors.fixtures import demo_fixture
from packages.factors.provider import (
    DecisionContext,
    FeatureProvider,
    StageController,
    VerifiedSnapshot,
)


def setup(lag=0):
    r = demo_fixture(
        factor_version_id=UUID(int=1),
        dataset_version_id=UUID(int=2),
        snapshot_id=UUID(int=3),
        lag_sessions=lag,
    )
    m = r.manifest
    b = FactorBundle(
        bundle_id=UUID(int=4),
        members=(m.input.version,),
        exposure_history=ExposureHistory(completeness="local_observed"),
    )
    binding = FactorRunBinding(
        bundle=b,
        dataset=m.input.dataset,
        snapshots=(
            SnapshotBinding(
                version=m.input.version,
                snapshot_id=m.snapshot_id,
                input_digest=m.input_digest,
                output_digest=m.output_digest,
                manifest_sha256=m.sha256,
                preprocessing_sha256=digest(m.input.definition.preprocessing),
            ),
        ),
        exposure_history=ExposureHistory(completeness="local_observed"),
    )
    p = FeatureProvider(binding, (VerifiedSnapshot(m, r.rows, r.verification),))
    c = DecisionContext(
        decision_id="d1",
        event_seq=1,
        decision_time=datetime(2024, 3, 8, 21, tzinfo=UTC),
        information_cutoff=datetime(2024, 3, 8, 21, tzinfo=UTC),
        stage_id="eligibility",
        listing_ids=m.input.calculation_universe.listing_ids,
    )
    return r, binding, p, c


def test_cutoff_and_exact_idempotent_receipts():
    r, _b, p, c = setup()
    v = p.read_factor(c, UUID(int=1))
    assert len(v.rows) == 3 and v.rows[0].value == pytest.approx(0.21)
    assert all(x.event_time <= c.information_cutoff for x in v.rows)
    receipt = v.receipts[0]
    assert (
        receipt.level == "read_requested"
        and receipt.causal_contribution == "not_established"
    )
    assert (
        receipt.rows_digest == rows_digest(v.rows)
        and receipt.output_digest == r.manifest.output_digest
    )
    assert p.read_factor(c, UUID(int=1)).receipts == v.receipts and len(p.receipts) == 1


def test_batch_not_actual_read():
    _, _, p, c = setup()
    a = p.view(c, (UUID(int=1),)).receipts[0]
    b = p.read_factor(c, UUID(int=1)).receipts[0]
    assert (
        a.level == "provided"
        and b.level == "read_requested"
        and a.receipt_id != b.receipt_id
    )


def test_future_clock_unknown_factor_listing_history():
    _, _, p, c = setup()
    with pytest.raises(ValueError):
        DecisionContext.model_validate(
            {
                **c.model_dump(),
                "information_cutoff": c.decision_time + timedelta(seconds=1),
            }
        )
    with pytest.raises(ValueError):
        p.read_factor(c, UUID(int=99))
    with pytest.raises(ValueError):
        p.read_factor(c, UUID(int=1), listing_ids=("foreign",))
    for value in (True, -1, 21):
        with pytest.raises(ValueError):
            p.read_factor(c, UUID(int=1), history_sessions=value)
    with pytest.raises(ValueError):
        p.view(c.model_dump(), (UUID(int=1),))


def test_no_latest_or_wrong_bytes_fallback():
    r, b, _, _ = setup()
    with pytest.raises(ValueError):
        FeatureProvider(
            b,
            (
                VerifiedSnapshot(
                    r.manifest.model_copy(update={"snapshot_id": UUID(int=99)}),
                    r.rows,
                    r.verification,
                ),
            ),
        )
    rows = (r.rows[0].model_copy(update={"missing_reason": "changed"}), *r.rows[1:])
    with pytest.raises(ValueError):
        FeatureProvider(b, (VerifiedSnapshot(r.manifest, rows, r.verification),))


def test_lag_projects_only_available_sessions():
    _, _, p, c = setup(1)
    v = p.read_factor(c, UUID(int=1), history_sessions=20)
    assert all(r.session_date.isoformat() < "2024-03-08" for r in v.rows)
    early = c.model_copy(
        update={
            "decision_time": datetime(2024, 3, 6, 20, tzinfo=UTC),
            "information_cutoff": datetime(2024, 3, 6, 20, tzinfo=UTC),
        }
    )
    empty = p.read_factor(early, UUID(int=1))
    assert empty.rows == () and len(empty.unavailable) == 3


def reply(call, **values):
    return StageReply(
        request_id=call.request_id,
        decision_id=call.decision_id,
        event_seq=call.event_seq,
        stage=call.stage,
        input_digest=call.input_digest,
        **values,
    )


def test_controller_consumes_actual_state_and_links():
    _, _, p, c = setup()
    a = p.read_factor(c, UUID(int=1)).receipts[0]
    host = StageController(c, p)
    call = host.begin(access_receipt_ids=(a.receipt_id,))
    assert host.consumed == ()
    receipt = host.consume(reply(call, eligible_listing_ids=c.listing_ids[:2]))
    assert host.eligible == c.listing_ids[:2]
    call = host.begin()
    assert call.predecessor_output_digest == receipt.output_digest
    host.consume(
        reply(
            call,
            values=tuple(
                {"listing_id": x, "value": i + 1} for i, x in enumerate(host.eligible)
            ),
        )
    )
    call = host.begin()
    host.consume(
        reply(
            call,
            values=(
                {"listing_id": host.eligible[0], "value": 0},
                {"listing_id": host.eligible[1], "value": 1},
            ),
        )
    )
    assert host.target_weights[1].value == 1 and len(host.consumed) == 3
    with pytest.raises(ValueError):
        host.begin()


def test_discard_and_forged_receipts_never_consumed_empty_decision_supported():
    _, _, p, c = setup()
    host = StageController(c, p)
    with pytest.raises(ValueError):
        host.begin(access_receipt_ids=(UUID(int=99),))
    first = host.begin()
    old = reply(first, eligible_listing_ids=())
    host.discard()
    assert host.consumed == ()
    with pytest.raises(ValueError):
        host.consume(old)
    second = host.begin()
    assert second.request_id != first.request_id
    with pytest.raises(ValueError):
        host.consume(old)
    host.consume(reply(second, eligible_listing_ids=()))
    host.consume(reply(host.begin(), values=()))
    host.consume(reply(host.begin(), values=()))
    assert host.target_weights == ()


def test_access_bound_to_current_decision_and_stage():
    _, _, p, c = setup()
    host = StageController(c, p)
    old = p.read_factor(
        c.model_copy(update={"decision_id": "old"}), UUID(int=1)
    ).receipts[0]
    with pytest.raises(ValueError):
        host.begin(access_receipt_ids=(old.receipt_id,))
    host.consume(reply(host.begin(), eligible_listing_ids=c.listing_ids))
    wrong = p.read_factor(c, UUID(int=1)).receipts[0]
    with pytest.raises(ValueError):
        host.begin(access_receipt_ids=(wrong.receipt_id,))
    correct = p.read_factor(
        c.model_copy(update={"stage_id": "score"}), UUID(int=1)
    ).receipts[0]
    host.begin(access_receipt_ids=(correct.receipt_id,))


def test_provider_cannot_accept_fitted_future_transform_or_erase_history():
    from packages.factors.contracts import (
        FactorDefinition,
        FeatureSnapshotManifest,
        SnapshotInput,
    )

    r, b, _, _ = setup()
    raw = r.manifest.input.model_dump(mode="json")
    raw["definition"]["preprocessing"] = {
        "kind": "fitted",
        "transform_id": "scaler",
        "code_sha256": "a" * 64,
        "parameters_sha256": "b" * 64,
        "fitted_artifact_sha256": "c" * 64,
        "fit_window": {"start": "2024-03-01", "end": "2024-03-20"},
        "last_input_at": "2024-03-19T20:00:00Z",
        "available_at": "2024-03-19T21:00:00Z",
    }
    d = FactorDefinition.model_validate(raw["definition"])
    raw["version"]["definition_sha256"] = d.sha256
    inputs = SnapshotInput.model_validate(raw)
    m = FeatureSnapshotManifest(
        snapshot_id=r.manifest.snapshot_id,
        input=inputs,
        input_digest=inputs.sha256,
        output_digest=r.manifest.output_digest,
        row_count=24,
        valid_count=18,
        missing_count=6,
    )
    bundle = FactorBundle(
        bundle_id=UUID(int=4),
        members=(inputs.version,),
        exposure_history=b.bundle.exposure_history,
    )
    bound = FactorRunBinding(
        bundle=bundle,
        dataset=inputs.dataset,
        snapshots=(
            SnapshotBinding(
                version=inputs.version,
                snapshot_id=m.snapshot_id,
                input_digest=m.input_digest,
                output_digest=m.output_digest,
                manifest_sha256=m.sha256,
                preprocessing_sha256=digest(d.preprocessing),
            ),
        ),
        exposure_history=b.exposure_history,
    )
    with pytest.raises(ValueError, match="fitted transform readiness"):
        FeatureProvider(bound, (VerifiedSnapshot(m, r.rows, r.verification),))


def test_new_factor_rows_do_not_erase_unknown_research_history():
    from packages.factors.contracts import FeatureSnapshotManifest, SnapshotInput

    r, b, _, _ = setup()
    raw = r.manifest.input.model_dump(mode="json")
    raw["exposure_history"] = {
        "completeness": "unknown",
        "claims": [],
        "unresolved_reasons": ["imported_unknown"],
    }
    inputs = SnapshotInput.model_validate(raw)
    m = FeatureSnapshotManifest(
        snapshot_id=r.manifest.snapshot_id,
        input=inputs,
        input_digest=inputs.sha256,
        output_digest=r.manifest.output_digest,
        row_count=24,
        valid_count=18,
        missing_count=6,
    )
    bound = b.model_copy(
        update={
            "snapshots": (
                SnapshotBinding(
                    version=inputs.version,
                    snapshot_id=m.snapshot_id,
                    input_digest=m.input_digest,
                    output_digest=m.output_digest,
                    manifest_sha256=m.sha256,
                    preprocessing_sha256=digest(inputs.definition.preprocessing),
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="unknown inherited"):
        FeatureProvider(bound, (VerifiedSnapshot(m, r.rows, r.verification),))
