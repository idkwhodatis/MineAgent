"""Disabled versioned numeric ABI proposals; never launch/admission capabilities."""

import hashlib
from dataclasses import asdict
from types import MappingProxyType

from packages.factors.contracts import canonical_bytes, source_sha256
from packages.rdagent_adapter.prefix import PrefixManifest
from packages.research_runtime.process_manifests import InputBinding

CANDIDATE_LOCK_SHA256 = (
    "43328af0c36fe5ad2000a64d931888f820f6604375b02fea97a0a4817fc8c4bc"
)
NORMALIZER_LOCK_SHA256 = (
    "cd0c3d4398ab01325a1f9952d2b49eec5f7626891472692c1b206c0b8a719435"
)
NUMERIC_CHILD_ENV = (
    ("PATH", "/usr/local/bin:/usr/bin:/bin"),
    ("HOME", "/tmp"),
    ("LANG", "C.UTF-8"),
    ("OPENBLAS_NUM_THREADS", "1"),
    ("OMP_NUM_THREADS", "1"),
    ("MKL_NUM_THREADS", "1"),
    ("NUMEXPR_NUM_THREADS", "1"),
    ("VECLIB_MAXIMUM_THREADS", "1"),
    ("BLIS_NUM_THREADS", "1"),
    ("HDF5_PLUGIN_PRELOAD", "::"),
    ("HDF5_PLUGIN_PATH", ""),
)
# These values must be passed explicitly by a separately reviewed numeric
# protected-wrapper successor; Docker ENV alone does not reach execve's child.
CANDIDATE_EXECUTABLE = ("python", "-I", "/input/source.py")
NORMALIZER_EXECUTABLE = ("python", "-I", "-m", "runtimes.factor_normalizer.entrypoint")
CANDIDATE_LIMITS = MappingProxyType(
    {
        "/input/source.py": 131072,
        "/input/observations.json": 16777216,
        "/input/listings.json": 1048576,
        "/input/manifest.json": 1048576,
    }
)
NORMALIZER_LIMITS = MappingProxyType(
    {
        "/input/result.h5": 16777216,
        "/input/listings.json": 1048576,
        "/input/manifest.json": 1048576,
    }
)
CANDIDATE_OUTPUT = ("/output/result.h5", 16777216)
NORMALIZER_OUTPUT = ("/output/values.parquet", 16777216)


def candidate_inputs(
    prefix, *, snapshot_input, source, expected_source_sha256, projected_files
):
    """Hash exact host-projected bytes; no filesystem, native parser or authority."""
    prefix = PrefixManifest.model_validate_json(prefix.model_dump_json())
    from packages.factors.contracts import SnapshotInput
    from packages.rdagent_adapter.assembly import validate_projection_binding

    snapshot_input = SnapshotInput.model_validate_json(snapshot_input.model_dump_json())
    validate_projection_binding(snapshot_input, prefix)
    if expected_source_sha256 != snapshot_input.definition.code_sha256:
        raise ValueError("Candidate source differs from frozen snapshot")
    if (
        type(source) is not bytes
        or source_sha256(source.decode("utf-8")) != expected_source_sha256
    ):
        raise ValueError("Candidate source differs from retained version")
    if set(projected_files) != {"observations.json", "listings.json"}:
        raise ValueError("Candidate has extra or missing input")
    for expected in prefix.input_files:
        content = projected_files[expected.name]
        if type(content) is not bytes or (
            len(content),
            hashlib.sha256(content).hexdigest(),
        ) != (expected.size_bytes, expected.sha256):
            raise ValueError("Projection byte identity differs")
    files = {
        "/input/source.py": source,
        "/input/manifest.json": canonical_bytes(prefix),
        **{"/input/" + key: value for key, value in projected_files.items()},
    }
    inventory = tuple(
        InputBinding(path, hashlib.sha256(content).hexdigest(), len(content))
        for path, content in sorted(files.items())
    )
    if any(item.size_bytes > CANDIDATE_LIMITS[item.destination] for item in inventory):
        raise ValueError("Candidate input exceeds fixed role ceiling")
    commitment = canonical_bytes(
        {
            "schema_version": "mineagent.rdagent-numeric-input.v2",
            "ipc_policy": "none",
            "prefix_sha256": prefix.sha256,
            "source_sha256": expected_source_sha256,
            "inputs": [asdict(item) for item in inventory],
        }
    )
    return inventory, MappingProxyType(files), commitment


def numeric_abi_description(config):
    """Prompt instructions describe an ABI; they prove neither reads nor PIT."""
    fields = ",".join(field.name for field in config.input.input_fields)
    return (
        "Execute self-contained Python3.12 source at /input/source.py with no arguments. "
        "Read-only files: /input/manifest.json (mineagent.factor-prefix-input.v2), "
        "/input/observations.json and /input/listings.json. No other inputs, network, credentials or IPC. "
        f"Observation values follow manifest.input_fields order ({fields}); each observation has listing_code, session, event_time, available_at, values. "
        "Listings are {code,listing_id}. Lookback L means L prior sessions plus current, L1..63, at most64 mounted sessions; missing history remains missing. "
        "Write only /output/result.h5 using h5py/NumPy; close it and set mode0644 for the unprivileged exporter. No links. Root has exactly group factor and no attributes. "
        "factor has exactly four contiguous uncompressed one-dimensional equal-length primitive datasets: "
        "event_ns little-endian int64, listing_code little-endian uint32, value little-endian float64, state uint8. "
        "No links, references, variable-length/string/object values, filters, external datasets, VDS or plugins. "
        "At most one row per allowed listing for manifest.output_event_time, sorted by event_ns then listing_code. "
        "state0 requires finite value; state1 means missing and requires a finite numeric placeholder. "
        "All rows <=manifest.max_output_rows (<=10000); total HDF<=manifest.max_output_bytes (<=16MiB). "
        "Output native bytes are parsed only in a separate isolated normalizer; no pandas pickle/HDF conventions. "
        "Input availability is host-projected, not proof that generated code read it or is PIT-correct."
    )


def normalizer_inputs(prefix, *, hdf, expected_hdf_sha256, listings):
    """Hash-only host staging; native HDF parsing is confined to numeric child."""
    from datetime import UTC, datetime, timedelta

    prefix = PrefixManifest.model_validate_json(prefix.model_dump_json())
    if (
        type(hdf) is not bytes
        or not 1 <= len(hdf) <= prefix.max_output_bytes
        or hashlib.sha256(hdf).hexdigest() != expected_hdf_sha256
    ):
        raise ValueError("Extracted HDF identity differs")
    listing_ref = next(
        item for item in prefix.input_files if item.name == "listings.json"
    )
    if type(listings) is not bytes or (
        len(listings),
        hashlib.sha256(listings).hexdigest(),
    ) != (listing_ref.size_bytes, listing_ref.sha256):
        raise ValueError("Normalizer listing identity differs")
    event_ns = (
        (prefix.output_event_time - datetime(1970, 1, 1, tzinfo=UTC))
        // timedelta(microseconds=1)
    ) * 1000
    manifest = canonical_bytes(
        {
            "schema_version": "mineagent.factor-normalization.v1",
            "hdf_sha256": expected_hdf_sha256,
            "listings_sha256": listing_ref.sha256,
            "output_event_ns": event_ns,
            "max_rows": prefix.max_output_rows,
            "max_bytes": prefix.max_output_bytes,
        }
    )
    files = {
        "/input/result.h5": hdf,
        "/input/listings.json": listings,
        "/input/manifest.json": manifest,
    }
    inventory = tuple(
        InputBinding(path, hashlib.sha256(content).hexdigest(), len(content))
        for path, content in sorted(files.items())
    )
    if any(item.size_bytes > NORMALIZER_LIMITS[item.destination] for item in inventory):
        raise ValueError("Normalizer input exceeds fixed role ceiling")
    commitment = canonical_bytes(
        {
            "schema_version": "mineagent.rdagent-normalizer-input.v1",
            "ipc_policy": "none",
            "prefix_sha256": prefix.sha256,
            "hdf_sha256": expected_hdf_sha256,
            "inputs": [asdict(item) for item in inventory],
        }
    )
    return inventory, MappingProxyType(files), commitment
