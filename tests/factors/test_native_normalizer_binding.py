"""Metadata parity only; no claim of executed native parsing or DB authority."""

import hashlib
from uuid import uuid4

import pytest

from packages.factors.native_normalizer_binding import normalizer_inventory
from packages.factors.v2_evidence import EvidenceObject
from packages.factors.v2_storage_contracts import storage_identity
from packages.rdagent_adapter.numeric_profile import normalizer_inputs
from tests.rdagent.test_rdagent_numeric_profile import projected


def test_independent_backup_inventory_matches_actual_host_builder_without_native_parse():
    _, _, prefix, files, _, _ = projected()
    opaque = b"opaque bytes are never parsed here"
    identity = storage_identity(
        uuid4(),
        EvidenceObject(
            role="native_hdf",
            object_id=uuid4(),
            object_kind="native_hdf",
            media_type="application/x-hdf5",
            sha256=hashlib.sha256(opaque).hexdigest(),
            size_bytes=len(opaque),
        ),
    )
    actual, _, _ = normalizer_inputs(
        prefix,
        hdf=opaque,
        expected_hdf_sha256=identity.object.sha256,
        listings=files["listings.json"],
    )
    assert normalizer_inventory(prefix, identity) == actual


def test_normalizer_inventory_cannot_replace_listing_hash():
    _, _, prefix, _, _, _ = projected()
    value = prefix.model_dump()
    value["input_files"][0]["sha256"] = "a" * 64
    prefix = type(prefix).model_validate(value)
    identity = storage_identity(
        uuid4(),
        EvidenceObject(
            role="native_hdf",
            object_id=uuid4(),
            object_kind="native_hdf",
            media_type="application/x-hdf5",
            sha256="b" * 64,
            size_bytes=1,
        ),
    )
    with pytest.raises(ValueError, match="listings"):
        normalizer_inventory(prefix, identity)
