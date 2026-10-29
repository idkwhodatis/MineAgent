from datetime import UTC, datetime
from uuid import UUID

import pytest

from packages.research_inputs.derivation import DerivationEvent, validate_event_prefix


def event(ordinal, state, **kwargs):
    return DerivationEvent(
        id=UUID(int=ordinal),
        derivation_id=UUID(int=100),
        ordinal=ordinal,
        state=state,
        recorded_at=datetime(2026, 10, 26, tzinfo=UTC),
        **kwargs,
    )


def test_started_is_not_a_running_claim():
    assert (
        validate_event_prefix([event(1, "prepared"), event(2, "started")])
        == "started_no_terminal_receipt"
    )


def test_early_cancel_is_terminal():
    assert (
        validate_event_prefix(
            [event(1, "prepared"), event(2, "cancelled", error_code="owner_cancelled")]
        )
        == "cancelled"
    )


def test_failed_receipt_does_not_need_a_fake_version():
    assert (
        validate_event_prefix(
            [
                event(1, "prepared"),
                event(2, "started"),
                event(3, "rejected", error_code="invalid_json_or_mapping"),
            ]
        )
        == "rejected"
    )


@pytest.mark.parametrize(
    "events",
    [
        [],
        [event(1, "started")],
        [event(1, "prepared"), event(2, "rejected", error_code="invalid_json")],
        [
            event(1, "prepared"),
            event(2, "cancelled", error_code="owner_cancelled"),
            event(3, "failed", error_code="error"),
        ],
        [event(1, "prepared"), event(3, "started")],
    ],
)
def test_nonactual_prefix_rejected(events):
    with pytest.raises(ValueError):
        validate_event_prefix(events)


def test_success_requires_actual_outputs():
    with pytest.raises(ValueError):
        event(3, "succeeded")


def test_prestart_cancellation_is_not_arbitrary_failure_receipt():
    with pytest.raises(ValueError, match="owner cancellation"):
        validate_event_prefix(
            [
                event(1, "prepared"),
                event(2, "cancelled", error_code="derivation_failed"),
            ]
        )
