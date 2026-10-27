"""Apply placement seam only to the full exact phase7 package inventory."""

import hashlib
import json
from pathlib import Path

from runtimes.openevolve.patches.apply import apply_unified, split_files

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parent / "openevolve"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def expected_inventory():
    expected = {
        x["path"]: x["sha256"]
        for x in json.loads((BASE / "upstream-source.json").read_bytes())["files"]
        if x["path"].startswith("openevolve/")
    }
    for name in [
        "manifest.json",
        "control-manifest.json",
        "observer-manifest.json",
        "prompt-manifest.json",
        "frontier-manifest.json",
        "evaluation-manifest.json",
    ]:
        for patch in json.loads((BASE / "patches" / name).read_bytes())["patches"]:
            for item in patch["files"]:
                if expected[item["path"]] != item["before_sha256"]:
                    raise ValueError("Patch source chain drift")
                expected[item["path"]] = item["after_sha256"]
    manifest = json.loads((ROOT / "manifest.json").read_bytes())
    previous_raw = (ROOT.parent / "openevolve_phase7/manifest.json").read_bytes()
    if sha(previous_raw) != manifest["requires_phase7_manifest_sha256"]:
        raise ValueError("Phase7 manifest drift")
    proof = json.loads(
        (ROOT.parent / "openevolve_phase7/build-proof.json").read_bytes()
    )
    if proof["wheel_sha256"] != manifest["requires_phase7_wheel_sha256"]:
        raise ValueError("Phase7 wheel identity differs")
    previous = json.loads(previous_raw)
    if expected[previous["file"]] != previous["before_sha256"]:
        raise ValueError("Phase7 source drift")
    expected[previous["file"]] = previous["after_sha256"]
    return expected


def apply(source):
    source = Path(source)
    manifest = json.loads((ROOT / "manifest.json").read_bytes())
    if (
        manifest["schema"] != "mineagent.openevolve-placement-patch.v1"
        or manifest["upstream_commit"] != "8c65f74aa906abc0b0e9cae948ddf4c4c6570bdc"
        or manifest["file"] != "openevolve/process_parallel.py"
        or manifest["patch"] != "0008-observe-submitted-worker-placement.patch"
    ):
        raise ValueError("Closed placement patch required")
    found = {}
    if source.is_symlink() or (source / "openevolve").is_symlink():
        raise ValueError("Symlink source")
    for item in (source / "openevolve").rglob("*"):
        if item.is_symlink():
            raise ValueError("Symlink source")
        if item.is_file() and "__pycache__" not in item.parts:
            found[item.relative_to(source).as_posix()] = sha(item.read_bytes())
    if found != expected_inventory():
        raise ValueError("Exact phase7 inventory required")
    raw = (ROOT / manifest["patch"]).read_bytes()
    if sha(raw) != manifest["patch_sha256"]:
        raise ValueError("Placement patch drift")
    sections = split_files(raw.decode())
    if set(sections) != {manifest["file"]}:
        raise ValueError("Unexpected patch target")
    target = source / manifest["file"]
    original = target.read_bytes()
    if sha(original) != manifest["before_sha256"]:
        raise ValueError("Placement input drift")
    result = apply_unified(original.decode(), sections[manifest["file"]]).encode()
    if sha(result) != manifest["after_sha256"]:
        raise ValueError("Placement output drift")
    target.write_bytes(result)
    return {manifest["file"]: sha(result)}


if __name__ == "__main__":
    import sys

    print(json.dumps(apply(sys.argv[1]), sort_keys=True))
