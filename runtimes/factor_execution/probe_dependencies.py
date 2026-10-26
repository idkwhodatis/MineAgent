"""Installed fixed-library import probe, never a candidate-source executor."""

import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
from pathlib import Path

from runtimes.factor_execution.numeric_deadline import NUMERIC_ENV


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("candidate", "normalizer"):
        raise ValueError("fixed numeric profile required")
    if os.getuid() != 65533 or os.getgid() != 65533 or not sys.flags.isolated:
        raise ValueError("nonroot isolated dependency probe required")
    for key, value in NUMERIC_ENV:
        os.environ[key] = value
    expected = {"numpy": "2.5.4", "h5py": "3.16.0"}
    if sys.argv[1] == "normalizer":
        expected["pyarrow"] = "26.0.0"
    versions = {name: importlib.metadata.version(name) for name in expected}
    if versions != expected:
        raise ValueError("installed numeric version drift")
    import h5py
    import numpy

    modules = {"h5py": h5py, "numpy": numpy}
    if sys.argv[1] == "normalizer":
        import pyarrow
        import pyarrow.parquet

        modules["pyarrow"] = pyarrow
    paths = {name: module.__file__ for name, module in modules.items()}
    if any("/site-packages/" not in path for path in paths.values()):
        raise ValueError("dependency import is not installed-only")
    check = subprocess.run(
        [sys.executable, "-I", "-m", "pip", "check"],
        capture_output=True,
        timeout=20,
        check=True,
    )
    if len(check.stdout) + len(check.stderr) > 4096:
        raise ValueError("unexpected pip-check output")
    native_paths = set()
    for line in Path("/proc/self/maps").read_text().splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) == 6 and fields[5].startswith("/") and ".so" in fields[5]:
            native_paths.add(fields[5])
    if not 1 <= len(native_paths) <= 128:
        raise ValueError("native dependency inventory bound")
    native = {}
    for name in sorted(native_paths):
        path = Path(name)
        if not path.is_file() or not 0 < path.stat().st_size <= 256 * 1024 * 1024:
            raise ValueError("native dependency file bound")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        native[name] = {"sha256": digest.hexdigest(), "bytes": path.stat().st_size}
    print(
        json.dumps(
            {
                "scope": "installed_numeric_dependencies_only",
                "role": sys.argv[1],
                "python": sys.version.split()[0],
                "uid": os.getuid(),
                "versions": versions,
                "installed_distributions": sorted(
                    (dist.metadata["Name"], dist.version)
                    for dist in importlib.metadata.distributions()
                ),
                "module_paths": paths,
                "hdf5_version": h5py.version.hdf5_version,
                "pip_check": True,
                "loaded_native_libraries": native,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
