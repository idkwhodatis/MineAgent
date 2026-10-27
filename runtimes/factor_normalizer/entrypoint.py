"""Fixed no-IPC normalizer entrypoint. Requires separately accepted containment."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

# Must precede every native HDF import, including transitive imports.
os.environ["HDF5_PLUGIN_PRELOAD"] = "::"
os.environ["HDF5_PLUGIN_PATH"] = ""

from runtimes.factor_normalizer.normalize import InvalidFactorOutput, normalize_file


def read_json(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise InvalidFactorOutput("invalid fixed input")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise InvalidFactorOutput("duplicate JSON key")
            result[key] = value
        return result

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)


def write_parquet(rows, target: Path):
    """Fixed numeric schema; caller already validated every primitive row."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = pa.schema(
        [
            pa.field("event_ns", pa.int64(), nullable=False),
            pa.field("listing_code", pa.uint32(), nullable=False),
            pa.field("value", pa.float64()),
            pa.field("state", pa.uint8(), nullable=False),
        ]
    )
    table = pa.Table.from_arrays(
        [
            pa.array([row[index] for row in rows], type=field.type)
            for index, field in enumerate(schema)
        ],
        schema=schema,
    )
    # Exclusive output prevents overwriting any pre-existing process artifact.
    with target.open("xb") as output:
        os.fchmod(output.fileno(), 0o644)
        pq.write_table(
            table,
            output,
            compression="NONE",
            use_dictionary=False,
            write_statistics=False,
        )
    if target.stat().st_size > 16 * 1024 * 1024:
        raise InvalidFactorOutput("output size cap")


def main():
    if len(sys.argv) != 1 or "h5py" in sys.modules:
        raise InvalidFactorOutput("invalid invocation")
    manifest = read_json(Path("/input/manifest.json"))
    if (
        not isinstance(manifest, dict)
        or set(manifest)
        != {
            "schema_version",
            "hdf_sha256",
            "listings_sha256",
            "output_event_ns",
            "max_rows",
            "max_bytes",
        }
        or manifest["schema_version"]
        not in (
            "mineagent.factor-normalization.v1",
            "mineagent.factor-normalization.v2",
        )
    ):
        raise InvalidFactorOutput("invalid manifest")
    listing_path = Path("/input/listings.json")
    listing = read_json(listing_path)
    if (
        hashlib.sha256(listing_path.read_bytes()).hexdigest()
        != manifest["listings_sha256"]
    ):
        raise InvalidFactorOutput("listing hash drift")
    if not isinstance(listing, list) or any(
        not isinstance(row, dict)
        or set(row) != {"code", "listing_id"}
        or not isinstance(row["listing_id"], str)
        or not row["listing_id"]
        for row in listing
    ):
        raise InvalidFactorOutput("invalid listings")
    rows = normalize_file(
        Path("/input/result.h5"),
        sha256=manifest["hdf_sha256"],
        event_ns=manifest["output_event_ns"],
        listing_codes=tuple(row["code"] for row in listing),
        max_rows=manifest["max_rows"],
        max_bytes=manifest["max_bytes"],
    )
    write_parquet(rows, Path("/output/values.parquet"))
    if manifest["schema_version"] == "mineagent.factor-normalization.v2":
        from runtimes.factor_normalizer.primitive_report import build_report

        report = build_report(
            rows,
            parquet=Path("/output/values.parquet").read_bytes(),
            manifest=Path("/input/manifest.json").read_bytes(),
            listings=listing_path.read_bytes(),
        )
        with Path("/output/rows.json").open("xb") as output:
            os.fchmod(output.fileno(), 0o644)
            output.write(report)
    print(
        json.dumps(
            {
                "schema_version": "mineagent.factor-normalization-result.v1",
                "row_count": len(rows),
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - fixed process boundary sanitizes all parser failures
        # No input-derived exception detail or native object repr crosses IPC.
        print('{"error":"invalid_factor_output"}')
        raise SystemExit(2) from None
