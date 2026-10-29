"""Immutable normal027 replay with row doubles; no database/worker acceptance claim."""

import pytest

from backend.app.models import ResearchRun
from backend.app.oe_structured_models import OEStructuredSubmission
from backend.app.oe_structured_provider_submission import ProviderStructuredSearchSubmit
from backend.app.oe_structured_submission_receipts import existing_receipt, validate_key
from backend.app.services import DomainError
from tests.openevolve.test_structured_persisted_identity import graph


def replay():
    session, run, spec, _, _, rows = graph()
    run.idempotency_key = "test-key"
    row = OEStructuredSubmission(
        id=spec.submission_id,
        owner_id=run.owner_id,
        run_id=run.id,
        idempotency_key=run.idempotency_key,
        request_canonical=spec.request_canonical,
        request_sha256=spec.request_sha256,
        preflight_sha256=spec.preflight_sha256,
        config_sha256=spec.config_sha256,
        origin="authenticated_user_submission",
    )
    rows[(ResearchRun, run.id)] = run
    session.scalar = lambda _: row
    submitted = ProviderStructuredSearchSubmit.model_validate_json(
        spec.request_canonical[:-1]
        + ',"preflight_sha256":"'
        + spec.preflight_sha256
        + '"}'
    )
    return session, run, row, submitted


def test_saved_request_replay_needs_no_current_provider_or_new_attempt():
    session, run, row, submitted = replay()
    run.status = "succeeded"
    receipt = existing_receipt(session, run.owner_id, run.idempotency_key, submitted)
    assert receipt.id == row.id
    assert receipt.required_process_units == 4
    assert receipt.request_sha256 == row.request_sha256
    assert receipt.origin_user_id == run.owner_id


def test_same_key_changed_body_conflicts():
    session, run, _, submitted = replay()
    submitted = submitted.model_copy(update={"maximum_mutation_slots": 2})
    with pytest.raises(DomainError) as error:
        existing_receipt(session, run.owner_id, run.idempotency_key, submitted)
    assert error.value.code == "idempotency_conflict"


def test_raw_saved_commitment_not_recanonicalized():
    session, run, row, submitted = replay()
    row.request_canonical += " "
    with pytest.raises(DomainError):
        existing_receipt(session, run.owner_id, run.idempotency_key, submitted)


def test_key_shared_with_other_run_is_not_silently_reused():
    session, run, _, submitted = replay()
    responses = iter((None, run))
    session.scalar = lambda _: next(responses)
    with pytest.raises(DomainError) as error:
        existing_receipt(session, run.owner_id, run.idempotency_key, submitted)
    assert error.value.code == "idempotency_conflict"


def test_unknown_key_is_not_a_receipt_or_launch():
    session, run, _, submitted = replay()
    session.scalar = lambda _: None
    assert (
        existing_receipt(session, run.owner_id, run.idempotency_key, submitted) is None
    )


@pytest.mark.parametrize("key", [None, "", "space key", "\n", "x" * 129])
def test_invalid_key_rejected(key):
    with pytest.raises(DomainError):
        validate_key(key)
