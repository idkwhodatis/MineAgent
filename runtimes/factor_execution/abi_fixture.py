"""Maintained fixed-path numeric ABI fixture, not an arbitrary-source service.

Production-style __main__ accepts no arguments and uses /input and /output.
The calculate helper is exposed solely for whitelisted local logic tests; those
are NOT acceptance of absolute mounts, containment or the retained-output channel.
"""

import json
import math
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

os.environ["HDF5_PLUGIN_PRELOAD"] = "::"


def calculate(source: Path, target: Path):
    """Known fixture calculation only; source/target are host test bindings."""
    manifest = json.loads((source / "manifest.json").read_text())
    if (
        manifest["schema_version"] != "mineagent.factor-prefix-input.v2"
        or manifest["history_semantics"] != "prior_sessions_plus_current"
        or manifest["candidate_profile"] != "mineagent.factor-prefix-candidate.v2"
    ):
        raise ValueError("unsupported prior-history profile")
    lookback = manifest["lookback_prior_sessions"]
    if type(lookback) is not int or not 1 <= lookback <= 63:
        raise ValueError("invalid prior history bound")
    observations = json.loads((source / "observations.json").read_text())
    listings = json.loads((source / "listings.json").read_text())
    field_index = [field["name"] for field in manifest["input_fields"]].index("close")
    event = datetime.fromisoformat(manifest["output_event_time"])
    event_ns = (
        (event - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1)
    ) * 1000
    output = []
    for listing in listings:
        rows = sorted(
            (row for row in observations if row["listing_code"] == listing["code"]),
            key=lambda row: row["session"],
        )
        valid = (
            len(rows) >= lookback + 1
            and rows[-1]["session"] == manifest["output_session"]
        )
        prices = [row["values"][field_index] for row in rows[-(lookback + 1) :]]
        valid = (
            valid
            and all(
                type(price) in (float, int) and math.isfinite(price) for price in prices
            )
            and prices[0] != 0
        )
        value = (prices[-1] / prices[0] - 1) if valid else 0.0
        output.append((event_ns, listing["code"], value, 0 if valid else 1))
    import h5py
    import numpy as np

    with h5py.File(target, "x") as file:
        group = file.create_group("factor")
        for index, (name, dtype) in enumerate(
            (
                ("event_ns", "<i8"),
                ("listing_code", "<u4"),
                ("value", "<f8"),
                ("state", "u1"),
            )
        ):
            group.create_dataset(
                name, data=np.array([row[index] for row in output], dtype=dtype)
            )

    # Protected exporter has no DAC override; make this exact regular output readable.
    target.chmod(0o644)


if __name__ == "__main__":
    if len(sys.argv) != 1:
        raise ValueError("fixed numeric ABI accepts no arguments")
    calculate(Path("/input"), Path("/output/result.h5"))
