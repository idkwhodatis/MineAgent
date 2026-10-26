"""Closed, maintained adversarial sources; executed only inside numeric child.

Host may read/compile this source but never calls its native fixture constructor.
The driver prepends a literal CASE assignment from its closed case set.
"""

import errno
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

CASES = (
    "malformed",
    "external_link",
    "soft_link",
    "vds",
    "external_storage",
    "gzip",
    "shuffle",
    "vlen",
    "reference",
    "compound",
    "hardlink_alias",
    "fifo",
    "symlink",
    "unreadable",
    "hardlink",
    "file_limit",
)


def create_case(case, target, input_root=Path("/input")):
    if case not in CASES:
        raise ValueError("unknown maintained case")
    import h5py
    import numpy as np

    if case == "fifo":
        os.mkfifo(target, 0o644)
        return
    if case == "symlink":
        target.symlink_to("/input/manifest.json")
        return
    if case == "file_limit":
        with target.open("xb") as output:
            try:
                os.ftruncate(output.fileno(), 16 * 1024 * 1024 + 1)
            except OSError as error:
                if error.errno == errno.EFBIG:
                    raise SystemExit(42) from None
                raise
        raise SystemExit(43)  # Explicit unexpected success, not a passing fixture.
    if case == "malformed":
        target.write_bytes(b"maintained invalid HDF signature\n")
        target.chmod(0o644)
        return
    manifest = json.loads((input_root / "manifest.json").read_text())
    listings = json.loads((input_root / "listings.json").read_text())
    event = datetime.fromisoformat(manifest["output_event_time"])
    event_ns = (
        (event - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1)
    ) * 1000
    primitives = {"event_ns": event_ns, "listing_code": listings[0]["code"], "state": 0}
    with h5py.File(target, "x") as file:
        group = file.create_group("factor")
        for name, dtype in (
            ("event_ns", "<i8"),
            ("listing_code", "<u4"),
            ("state", "u1"),
        ):
            group.create_dataset(name, data=np.array([primitives[name]], dtype=dtype))
        if case == "external_link":
            group["value"] = h5py.ExternalLink("/input/absent.h5", "/value")
        elif case == "soft_link":
            group["value"] = h5py.SoftLink("/factor/event_ns")
        elif case == "vds":
            layout = h5py.VirtualLayout(shape=(1,), dtype="<f8")
            layout[:] = h5py.VirtualSource("/input/absent.h5", "value", shape=(1,))
            group.create_virtual_dataset("value", layout)
        elif case == "external_storage":
            group.create_dataset(
                "value", shape=(1,), dtype="<f8", external=[("/input/absent.bin", 0, 8)]
            )
        elif case == "vlen":
            group.create_dataset(
                "value", data=np.array(["x"], dtype=object), dtype=h5py.string_dtype()
            )
        elif case == "reference":
            group.create_dataset(
                "value", data=np.array([file.ref], dtype=h5py.ref_dtype)
            )
        elif case == "compound":
            group.create_dataset(
                "value", data=np.array([(1,)], dtype=[("field", "<i8")])
            )
        else:
            options = (
                {"compression": "gzip"}
                if case == "gzip"
                else {"shuffle": True}
                if case == "shuffle"
                else {}
            )
            group.create_dataset("value", data=np.array([1.0], dtype="<f8"), **options)
            if case == "hardlink_alias":
                group["alias"] = group["value"]
    target.chmod(0o600 if case == "unreadable" else 0o644)
    if case == "hardlink":
        os.link(target, target.with_name("second.h5"))


if __name__ == "__main__":
    # CASE is supplied only by the closed driver as literal source, never env/argv.
    create_case(CASE, Path("/output/result.h5"))  # noqa: F821
