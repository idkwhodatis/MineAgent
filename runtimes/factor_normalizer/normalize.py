"""Strict HDF grammar; native parsing STILL requires isolated admission.

Only known synthetic fixtures may call this directly on a development host.
Never parse candidate artifacts here inside the trusted API/RD orchestrator.
"""

from __future__ import annotations

import ctypes
import hashlib
import math
import os
import stat
from pathlib import Path

MAX_BYTES = 16 * 1024 * 1024
MAX_ROWS = 10_000
DTYPES = {"event_ns": "<i8", "listing_code": "<u4", "value": "<f8", "state": "|u1"}


class InvalidFactorOutput(ValueError):
    pass


def disabled_plugin_backend():
    # HDFGroup: '::' disables all dynamically loaded plugins. Empty PATH does not.
    os.environ["HDF5_PLUGIN_PRELOAD"] = "::"
    os.environ["HDF5_PLUGIN_PATH"] = ""
    import h5py

    library = ctypes.CDLL(h5py.h5.__file__)
    setter = library.H5PLset_loading_state
    setter.argtypes, setter.restype = [ctypes.c_uint], ctypes.c_int
    getter = library.H5PLget_loading_state
    getter.argtypes, getter.restype = [ctypes.POINTER(ctypes.c_uint)], ctypes.c_int
    state = ctypes.c_uint(0xFFFFFFFF)
    if setter(0) < 0 or getter(ctypes.byref(state)) < 0 or state.value != 0:
        raise InvalidFactorOutput("plugin loading not disabled")
    return h5py


def normalize_file(
    path: Path,
    *,
    sha256: str,
    event_ns: int,
    listing_codes: tuple[int, ...],
    max_rows: int,
    max_bytes: int,
):
    """Return primitives only; no provider/PIT/readiness claims."""
    if (
        type(event_ns) is not int
        or not -(2**63) <= event_ns < 2**63
        or type(max_rows) is not int
        or not 1 <= max_rows <= MAX_ROWS
        or type(max_bytes) is not int
        or not 1 <= max_bytes <= MAX_BYTES
        or not listing_codes
        or len(listing_codes) > MAX_ROWS
        or any(type(code) is not int or not 0 <= code < 2**32 for code in listing_codes)
        or len(set(listing_codes)) != len(listing_codes)
    ):
        raise InvalidFactorOutput("invalid host bounds")
    h5py = disabled_plugin_backend()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as raw:
        info = os.fstat(raw.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or not 0 < info.st_size <= max_bytes
        ):
            raise InvalidFactorOutput("invalid file bounds")
        if hashlib.file_digest(raw, "sha256").hexdigest() != sha256:
            raise InvalidFactorOutput("HDF content drift")
        raw.seek(0)
        with h5py.File(raw, "r") as file:
            if file.userblock_size or len(file.attrs) or set(file.keys()) != {"factor"}:
                raise InvalidFactorOutput("unexpected root grammar")
            if not isinstance(file.get("factor", getlink=True), h5py.HardLink):
                raise InvalidFactorOutput("group link")
            group = file["factor"]
            if (
                not isinstance(group, h5py.Group)
                or len(group.attrs)
                or set(group.keys()) != set(DTYPES)
            ):
                raise InvalidFactorOutput("unexpected factor grammar")
            if h5py.h5o.get_info(group.id).rc != 1:
                raise InvalidFactorOutput("group aliases")
            length = None
            addresses = set()
            # Validate ALL metadata before reading any dataset values.
            for name, expected in DTYPES.items():
                if not isinstance(group.get(name, getlink=True), h5py.HardLink):
                    raise InvalidFactorOutput("dataset link")
                dataset = group[name]
                if not isinstance(dataset, h5py.Dataset):
                    raise InvalidFactorOutput("unexpected nested group")
                obj = h5py.h5o.get_info(dataset.id)
                if obj.rc != 1 or obj.addr in addresses:
                    raise InvalidFactorOutput("dataset aliases")
                addresses.add(obj.addr)
                creation = dataset.id.get_create_plist()
                if (
                    dataset.dtype.str != expected
                    or dataset.dtype.metadata is not None
                    or len(dataset.shape) != 1
                    or len(dataset.attrs)
                    or dataset.is_virtual
                    or creation.get_external_count()
                    or creation.get_nfilters()
                    or creation.get_layout() != h5py.h5d.CONTIGUOUS
                ):
                    raise InvalidFactorOutput("unsupported dataset storage/type")
                count = dataset.shape[0]
                if count > max_rows or (length is not None and count != length):
                    raise InvalidFactorOutput("row bounds/length mismatch")
                length = count
            columns = {name: group[name][...] for name in DTYPES}
    allowed, rows, previous = set(listing_codes), [], None
    for event, listing, value, state_code in zip(
        *(columns[name] for name in DTYPES), strict=True
    ):
        key, state_code = (int(event), int(listing)), int(state_code)
        if (
            key[0] != event_ns
            or key[1] not in allowed
            or (previous is not None and key <= previous)
        ):
            raise InvalidFactorOutput("invalid event/listing/order")
        if state_code not in (0, 1) or not math.isfinite(float(value)):
            raise InvalidFactorOutput("invalid state/nonfinite value")
        rows.append(
            (key[0], key[1], float(value) if state_code == 0 else None, state_code)
        )
        previous = key
    return tuple(rows)
