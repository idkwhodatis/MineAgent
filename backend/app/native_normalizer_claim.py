"""Atomic native input consumption in the explicit factor_numeric transaction.

No commits, detached grants, host HDF parser or default launch registration.
"""

from sqlalchemy import select

from backend.app.factor_native_models import NativeNormalizerInput
from backend.app.factor_native_services import (
    _publication,
    decode_intent,
    error,
    read_completed_native,
)
from backend.app.research_lifecycle import require_neutral_transaction
from packages.factors.contracts import canonical_bytes, digest
from packages.rdagent_adapter.numeric_profile import normalizer_inputs
from packages.rdagent_adapter.prefix import PrefixManifest
from packages.research_runtime.numeric_process_journal import NumericIntent


def _verify(session, fence, publication_id, admission_id, settings, *, initial):
    require_neutral_transaction(session, fence, resource_scope="factor_numeric")
    from backend.app.numeric_process_services import _persisted, _scope
    from backend.app.rdagent_models import FactorCutoffExecution

    identity, raw = read_completed_native(session, fence, publication_id, settings)
    publication = _publication(session, fence, publication_id)
    candidate = decode_intent(publication)
    cutoff = session.get(FactorCutoffExecution, publication.cutoff_id)
    prefix = PrefixManifest.model_validate(cutoff.projection)
    admission, state, receipts = _persisted(session, admission_id)
    _scope(admission, fence)
    from backend.app.research_broker_services import require_reservation
    from packages.research_runtime.budget_contracts import BudgetVector

    minimum = BudgetVector(
        process_slots=1, wall_milliseconds=60000, storage_bytes=67_174_400
    )
    if initial is None:
        _require_completed_reservation(
            session, fence, admission.reservation_id, minimum
        )
    else:
        require_reservation(
            session,
            fence,
            admission.reservation_id,
            purpose="process_unit",
            minimum=minimum,
        )
    expected_state = (
        "stopped" if initial is None else "launch_intended" if initial else "uncertain"
    )
    expected_receipts = (
        {"launch_intent", "launch_identity", "stop", "extraction"}
        if initial is None
        else {"launch_intent"}
    )
    if state.status != expected_state or set(receipts) != expected_receipts:
        raise error("native_normalizer_already_claimed")
    intent = NumericIntent.model_validate_json(
        canonical_bytes(receipts["launch_intent"].payload)
    )
    listings = canonical_bytes(
        [item.model_dump(mode="json") for item in prefix.listings]
    )
    from packages.factors.native_normalizer_binding import normalizer_report_selection

    report = normalizer_report_selection(
        prefix, identity, intent.inputs.files, allow_reports=True
    )
    if report:
        from backend.app.normalizer_report_ddl import require_report_capability

        require_report_capability(session)
    files, _, _ = normalizer_inputs(
        prefix,
        hdf=raw,
        expected_hdf_sha256=identity.object.sha256,
        listings=listings,
        primitive_report=report,
    )
    if (
        intent.admission,
        admission.role,
        admission.profile,
        admission.image_id,
        admission.logical_operation_id,
        intent.inputs.source_object_id,
        intent.inputs.source_sha256,
        intent.inputs.prefix_sha256,
        intent.inputs.cutoff,
        tuple((x.destination, x.sha256, x.size_bytes) for x in intent.inputs.files),
    ) != (
        admission,
        "factor_normalizer",
        prefix.normalizer_profile,
        prefix.normalizer_image,
        candidate.admission.logical_operation_id,
        candidate.source_object_id,
        candidate.source_sha256,
        candidate.prefix_sha256,
        candidate.cutoff,
        tuple((x.destination, x.sha256, x.size_bytes) for x in files),
    ):
        raise error("native_normalizer_input_drift")
    return publication, admission, intent, identity, raw


def claim_native_normalizer(
    session, fence, publication_id, normalizer_admission_id, settings
):
    """Call after persist_numeric_intent and before its one-shot launch claim."""
    p, admission, intent, _, _ = _verify(
        session, fence, publication_id, normalizer_admission_id, settings, initial=True
    )
    expected = dict(
        admission_id=admission.id,
        **{
            key: getattr(fence, key)
            for key in ("owner_id", "run_id", "attempt_id", "generation")
        },
        publication_id=p.id,
        reservation_id=admission.reservation_id,
        object_id=p.object_id,
        sha256=p.sha256,
        size_bytes=p.size_bytes,
        storage_key=p.storage_key,
        input_sha256=admission.input_sha256,
        intent_sha256=digest(intent),
    )
    current = session.get(NativeNormalizerInput, admission.id)
    if current is not None:
        if any(getattr(current, key) != value for key, value in expected.items()):
            raise error("native_normalizer_claim_drift")
        return current.admission_id
    # Immediate UNIQUE(publication_id) arbitrates competing consumers before
    # commit and before the issuer can release its retained input/FD admission.
    session.add(NativeNormalizerInput(**expected))
    session.flush()
    return admission.id


def _require_claim(
    session, fence, normalizer_admission_id, settings, *, completed=False
):
    """Fresh post-commit pre-launch check, never a previous detached read grant."""
    require_neutral_transaction(session, fence, resource_scope="factor_numeric")
    row = session.scalar(
        select(NativeNormalizerInput)
        .where(NativeNormalizerInput.admission_id == normalizer_admission_id)
        .with_for_update()
    )
    if row is None or any(
        getattr(row, k) != getattr(fence, k)
        for k in ("owner_id", "run_id", "attempt_id", "generation")
    ):
        raise error("native_normalizer_claim_missing")
    p, admission, intent, identity, raw = _verify(
        session,
        fence,
        row.publication_id,
        normalizer_admission_id,
        settings,
        initial=None if completed else False,
    )
    if (
        row.reservation_id,
        row.object_id,
        row.sha256,
        row.size_bytes,
        row.storage_key,
        row.input_sha256,
        row.intent_sha256,
    ) != (
        admission.reservation_id,
        p.object_id,
        p.sha256,
        p.size_bytes,
        p.storage_key,
        admission.input_sha256,
        digest(intent),
    ):
        raise error("native_normalizer_claim_drift")
    return p, admission, intent, identity, raw


def require_native_normalizer_claim(session, fence, normalizer_admission_id, settings):
    *_, identity, raw = _require_claim(
        session, fence, normalizer_admission_id, settings
    )
    return identity, raw


def resolve_completed_native_normalizer(
    session, fence, normalizer_admission_id, settings
):
    """Retained fresh completed graph only; caller must resolve actual stop/output."""
    return _require_claim(
        session, fence, normalizer_admission_id, settings, completed=True
    )


def _require_completed_reservation(session, fence, reservation_id, minimum):
    from backend.app.research_broker_models import (
        ResearchBudgetReservation,
        ResearchBudgetSettlement,
    )
    from packages.research_runtime.budget_contracts import BudgetVector

    reservation = session.get(
        ResearchBudgetReservation, reservation_id, with_for_update=True
    )
    settlement = session.scalar(
        select(ResearchBudgetSettlement).where(
            ResearchBudgetSettlement.reservation_id == reservation_id
        )
    )
    scope = ("owner_id", "run_id", "attempt_id", "generation")
    if (
        reservation is None
        or any(getattr(reservation, k) != getattr(fence, k) for k in scope)
        or reservation.purpose != "process_unit"
        or not BudgetVector.model_validate(reservation.amounts).covers(minimum)
    ):
        raise error("native_normalizer_reservation_drift")
    if reservation.state in {"reserved", "uncertain"} and settlement is None:
        return
    if (
        reservation.state == "settled"
        and settlement is not None
        and all(getattr(settlement, k) == getattr(fence, k) for k in scope)
        and settlement.charge == reservation.amounts
        and settlement.outcome == "conservative_unknown"
    ):
        return
    raise error("native_normalizer_reservation_drift")
