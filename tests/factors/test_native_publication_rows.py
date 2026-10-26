"""Pure row-decoding tests; not evidence of persisted receipt/source authority."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app.factor_native_services import decode_intent, footprint
from backend.app.services import DomainError
from packages.factors.contracts import digest
from tests.factors.test_native_io import real_intent


def row():
    intent, _ = real_intent()
    obj = intent.output.object
    peak, entries = footprint(intent)
    return SimpleNamespace(
        id=intent.journal_id,
        reservation_id=intent.reservation_id,
        admission_id=intent.admission.id,
        cutoff_id=intent.admission.logical_operation_id,
        source_id=intent.source_id,
        source_object_id=intent.source_object_id,
        source_sha256=intent.source_sha256,
        stop_receipt_id=intent.stop_receipt_id,
        extraction_receipt_id=intent.extraction_receipt_id,
        stop_kind="stop",
        extraction_kind="extraction",
        intent=intent.model_dump(mode="json"),
        intent_sha256=digest(intent),
        object_id=obj.object_id,
        sha256=obj.sha256,
        size_bytes=obj.size_bytes,
        storage_key=intent.output.storage_key,
        journal_key=intent.journal_key,
        peak_bytes=peak,
        peak_entries=entries,
        **{
            key: getattr(intent.admission, key)
            for key in ("owner_id", "run_id", "attempt_id", "generation")
        },
    )


def test_roundtrip_only_decodes_no_stop_or_publication_authority():
    assert decode_intent(row()) == real_intent()[0]


@pytest.mark.parametrize(
    "field",
    [
        "id",
        "reservation_id",
        "admission_id",
        "cutoff_id",
        "source_id",
        "source_object_id",
        "stop_receipt_id",
        "extraction_receipt_id",
        "object_id",
        "owner_id",
        "run_id",
        "attempt_id",
    ],
)
def test_every_row_identity_is_bound_to_intent(field):
    value = row()
    setattr(value, field, uuid4())
    with pytest.raises(DomainError):
        decode_intent(value)


@pytest.mark.parametrize(
    "field", ["generation", "size_bytes", "peak_bytes", "peak_entries"]
)
def test_numeric_row_drift_rejected(field):
    value = row()
    setattr(value, field, getattr(value, field) + 1)
    with pytest.raises(DomainError):
        decode_intent(value)


@pytest.mark.parametrize(
    "field",
    [
        "source_sha256",
        "intent_sha256",
        "sha256",
        "stop_kind",
        "extraction_kind",
        "storage_key",
        "journal_key",
    ],
)
def test_row_hash_kind_or_address_drift_rejected(field):
    value = row()
    setattr(value, field, "different")
    with pytest.raises(DomainError):
        decode_intent(value)
