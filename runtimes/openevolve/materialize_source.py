"""Verify an external pinned checkout and copy only wheel build inputs.

This is a build-time utility, never runtime code or a user upload endpoint. It
neither fetches source nor executes upstream Python. Destination must be new.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path, PurePosixPath

COMMIT = "8c65f74aa906abc0b0e9cae948ddf4c4c6570bdc"
ROOT = Path(__file__).resolve().parent


def materialize(source: Path, destination: Path) -> dict:
    manifest = json.loads((ROOT / "upstream-source.json").read_text())
    if (
        manifest["commit"] != COMMIT
        or manifest["schema"] != "mineagent.openevolve-source.v1"
    ):
        raise ValueError("Unsupported OpenEvolve source manifest")
    source = source.resolve(strict=True)
    observed = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD", "HEAD^{tree}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.splitlines()
    if observed != [COMMIT, manifest["tree"]]:
        raise ValueError("OpenEvolve checkout is not the exact supported commit/tree")
    checked = []
    seen = set()
    for entry in manifest["files"]:
        name = entry["path"]
        relative = PurePosixPath(name)
        if (
            relative.is_absolute()
            or any(p in {"", ".", ".."} for p in relative.parts)
            or name in seen
        ):
            raise ValueError("Invalid or duplicate build-input path")
        seen.add(name)
        if not (
            name.startswith("openevolve/")
            or name in {"pyproject.toml", "README.md", "LICENSE", "MANIFEST.in"}
        ):
            raise ValueError("Unexpected build input")
        current = source
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("Symlink build input is forbidden")
        fd = os.open(current, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size != entry["size_bytes"]:
                raise ValueError("Build input size/type mismatch")
            data = stream.read(entry["size_bytes"] + 1)
        if (
            len(data) != entry["size_bytes"]
            or hashlib.sha256(data).hexdigest() != entry["sha256"]
        ):
            raise ValueError("Build input hash mismatch")
        checked.append((relative, data))
    # Validate the package inventory as well as each file: extras cannot silently
    # enter setuptools' package discovery. Non-package docs/tests are not copied.
    actual = {
        p.relative_to(source).as_posix()
        for p in (source / "openevolve").rglob("*")
        if p.is_file() or p.is_symlink()
    }
    if actual != {name for name in seen if name.startswith("openevolve/")}:
        raise ValueError("Unexpected or missing package file")
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    for relative, data in checked:
        target = destination.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(data)
        os.chmod(target, 0o644)
        os.utime(target, (manifest["source_date_epoch"], manifest["source_date_epoch"]))
    return {
        "commit": COMMIT,
        "files": len(checked),
        "source_date_epoch": manifest["source_date_epoch"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(materialize(args.source, args.destination), sort_keys=True))
