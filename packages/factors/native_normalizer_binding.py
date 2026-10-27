"""Hash-only normalizer input commitment; not byte/publication authority."""

import hashlib
from datetime import UTC, datetime, timedelta

from packages.factors.contracts import canonical_bytes
from packages.factors.v2_storage_contracts import StorageIdentityV2
from packages.rdagent_adapter.prefix import PrefixManifest
from packages.research_runtime.process_manifests import InputBinding


def normalizer_inventory(prefix, identity, *, primitive_report=False):
    if type(primitive_report) is not bool:
        raise ValueError("Exact primitive report selection required")
    prefix = PrefixManifest.model_validate(prefix)
    identity = StorageIdentityV2.model_validate(identity)
    if (
        identity.object.object_kind != "native_hdf"
        or identity.object.size_bytes > prefix.max_output_bytes
    ):
        raise ValueError("Native normalizer identity differs")
    listings = canonical_bytes(
        [item.model_dump(mode="json") for item in prefix.listings]
    )
    reference = next(
        item for item in prefix.input_files if item.name == "listings.json"
    )
    if (len(listings), hashlib.sha256(listings).hexdigest()) != (
        reference.size_bytes,
        reference.sha256,
    ):
        raise ValueError("Native normalizer listings differ")
    event_ns = (
        (prefix.output_event_time - datetime(1970, 1, 1, tzinfo=UTC))
        // timedelta(microseconds=1)
    ) * 1000
    manifest = canonical_bytes(
        {
            "schema_version": "mineagent.factor-normalization.v2"
            if primitive_report
            else "mineagent.factor-normalization.v1",
            "hdf_sha256": identity.object.sha256,
            "listings_sha256": reference.sha256,
            "output_event_ns": event_ns,
            "max_rows": prefix.max_output_rows,
            "max_bytes": prefix.max_output_bytes,
        }
    )
    return (
        InputBinding("/input/listings.json", reference.sha256, reference.size_bytes),
        InputBinding(
            "/input/manifest.json", hashlib.sha256(manifest).hexdigest(), len(manifest)
        ),
        InputBinding(
            "/input/result.h5", identity.object.sha256, identity.object.size_bytes
        ),
    )


def normalizer_report_selection(prefix, identity, files, *, allow_reports=False):
    """Select only by exact immutable inventory; never trust a caller flag."""
    actual = tuple((x.destination, x.sha256, x.size_bytes) for x in files)
    for report in (False, True) if allow_reports else (False,):
        expected = normalizer_inventory(prefix, identity, primitive_report=report)
        if actual == tuple((x.destination, x.sha256, x.size_bytes) for x in expected):
            return report
    raise ValueError("Native normalizer full inventory differs")


def primitive_report_context(prefix, identity):
    """Exact canonical metadata bytes for a separately admitted report decoder."""
    expected = normalizer_inventory(prefix, identity, primitive_report=True)
    listings = canonical_bytes(
        [item.model_dump(mode="json") for item in prefix.listings]
    )
    event_ns = (
        (prefix.output_event_time - datetime(1970, 1, 1, tzinfo=UTC))
        // timedelta(microseconds=1)
    ) * 1000
    manifest = canonical_bytes(
        {
            "schema_version": "mineagent.factor-normalization.v2",
            "hdf_sha256": identity.object.sha256,
            "listings_sha256": expected[0].sha256,
            "output_event_ns": event_ns,
            "max_rows": prefix.max_output_rows,
            "max_bytes": prefix.max_output_bytes,
        }
    )
    if (hashlib.sha256(manifest).hexdigest(), len(manifest)) != (
        expected[1].sha256,
        expected[1].size_bytes,
    ):
        raise ValueError("Primitive report metadata differs from inventory")
    return manifest, listings
