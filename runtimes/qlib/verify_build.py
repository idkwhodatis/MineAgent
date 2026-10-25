"""Build-only source/wheel verification; never accepts runtime user artifacts."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

COMMIT = "54355232463878d2eebb91fe0ee5fa7fa1f5976c"
ROOT_FILES = {"README.md", "LICENSE", "MANIFEST.in", "pyproject.toml", "setup.py"}


def source_files(root, inventory):
    if (
        inventory["commit"] != COMMIT
        or inventory["schema"] != "mineagent.qlib-build-source.v1"
    ):
        raise ValueError("Unsupported Qlib source inventory")
    expected = inventory["files"]
    actual = {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() or p.is_symlink()
    }
    if actual != set(expected):
        raise ValueError("Unexpected or missing build source file")
    for name, sha in expected.items():
        rel = PurePosixPath(name)
        if (
            rel.is_absolute()
            or ".." in rel.parts
            or not (name.startswith("qlib/") or name in ROOT_FILES)
        ):
            raise ValueError("Unexpected source inventory path")
        p = root
        for part in rel.parts:
            p = p / part
            if p.is_symlink():
                raise ValueError("Source symlink forbidden")
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != sha:
            raise ValueError("Source content drift")
    return len(expected)


def materialize(source, destination, inventory):
    head = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    if head != COMMIT:
        raise ValueError("Wrong Qlib commit")
    # Only allow tracked, audited inputs, without exporting .git or generated files.
    destination.mkdir(parents=False, exist_ok=False)
    for name in inventory["files"]:
        rel = PurePosixPath(name)
        if (
            rel.is_absolute()
            or ".." in rel.parts
            or not (name.startswith("qlib/") or name in ROOT_FILES)
        ):
            raise ValueError("Unexpected source inventory path")
        p = source
        for part in rel.parts:
            p = p / part
            if p.is_symlink():
                raise ValueError("Source symlink forbidden")
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, target)
    source_files(destination, inventory)


def wheelhouse(directory):
    """Seal freshly built/downloaded wheels into an offline installation hashlock."""
    files = sorted(directory.glob("*.whl"))
    if not files:
        raise ValueError("Empty wheelhouse")
    seen = set()
    lines = []
    inventory = {}
    for path in files:
        if path.is_symlink():
            raise ValueError("Wheel symlink")
        filename = re.fullmatch(
            r"([A-Za-z0-9_.]+)-([0-9][A-Za-z0-9._+!]*)"
            r"(?:-[0-9][A-Za-z0-9_]*)?"
            r"-[A-Za-z0-9_.]+-[A-Za-z0-9_.]+-[A-Za-z0-9_.]+\.whl",
            path.name,
        )
        if filename is None:
            raise ValueError("Invalid wheel filename")
        with ZipFile(path) as z:
            # Vendored dependencies (notably setuptools/_vendor) legitimately
            # contain their own dist-info. Only the wheel's root metadata owns
            # its distribution identity; never select by a suffix alone.
            members = z.namelist()
            if len(members) != len(set(members)):
                raise ValueError("Duplicate wheel archive member")
            names = [
                n for n in members if re.fullmatch(r"[^/]+\.dist-info/METADATA", n)
            ]
            if len(names) != 1:
                raise ValueError("Ambiguous wheel metadata")
            meta = BytesParser().parsebytes(z.read(names[0]))
        if len(meta.get_all("Name", [])) != 1 or len(meta.get_all("Version", [])) != 1:
            raise ValueError("Ambiguous wheel identity headers")
        name, version = meta["Name"], meta["Version"]
        if (
            not name
            or re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", name) is None
            or not version
            or re.fullmatch(r"[0-9][A-Za-z0-9._+!]*", version) is None
        ):
            raise ValueError("Invalid wheel identity")
        key = re.sub(r"[-_.]+", "-", name).lower()
        directory_name, separator, directory_version = (
            names[0].split("/")[0][: -len(".dist-info")].rpartition("-")
        )
        if (
            not separator
            or key != re.sub(r"[-_.]+", "-", filename[1]).lower()
            or key != re.sub(r"[-_.]+", "-", directory_name).lower()
            or version != filename[2]
            or version != directory_version
        ):
            raise ValueError("Wheel filename/metadata identity mismatch")
        if key in seen:
            raise ValueError("Duplicate wheel distribution")
        seen.add(key)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{name}=={version} --hash=sha256:{sha}")
        inventory[path.name] = sha
    (directory / "install.lock").write_text("\n".join(lines) + "\n")
    (directory / "wheel-sha256.json").write_text(
        json.dumps(inventory, sort_keys=True, indent=2) + "\n"
    )
    return inventory


def verify_profile(directory, expected):
    observed = json.loads((directory / "build-report.json").read_text())
    if observed != expected:
        raise ValueError("Profile differs from frozen wheel proof")
    for key in ("upstream", "adapter", "native_adapter"):
        item = expected[key]
        path = directory / item["filename"]
        if (
            path.name != item["filename"]
            or path.is_symlink()
            or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]
        ):
            raise ValueError("Profile wheel hash drift")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "operation", choices=("source", "materialize", "wheelhouse", "profile")
    )
    parser.add_argument("path", type=Path)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    if args.operation == "wheelhouse":
        wheelhouse(args.path)
    else:
        inventory = json.loads(args.inventory.read_text())
        if args.operation == "source":
            source_files(args.path, inventory)
        elif args.operation == "profile":
            verify_profile(args.path, inventory)
        else:
            materialize(args.path, args.destination, inventory)
