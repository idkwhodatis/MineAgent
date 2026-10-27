"""Apply phase7 only to the full exact phase6 installed-package source inventory."""

import argparse
import hashlib
import json
from pathlib import Path

from runtimes.openevolve.patches.apply import apply_unified, split_files

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parent / "openevolve"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def apply(source):
    manifest = json.loads((ROOT / "manifest.json").read_bytes())
    if (
        manifest.get("schema") != "mineagent.openevolve-sampling-patch.v1"
        or manifest.get("upstream_commit") != "8c65f74aa906abc0b0e9cae948ddf4c4c6570bdc"
        or manifest.get("patch") != "0007-observe-native-sampling-state.patch"
    ):
        raise ValueError("Unsupported sampling patch manifest")
    proof_bytes = (BASE / "patches/evaluation-build-proof.json").read_bytes()
    if sha(proof_bytes) != manifest["requires_proof_sha256"]:
        raise ValueError("Phase6 proof drift")
    proof = json.loads(proof_bytes)
    if proof["wheel"]["sha256"] != manifest["requires_wheel_sha256"]:
        raise ValueError("Phase6 wheel identity differs")
    expected = {
        entry["path"]: entry["sha256"]
        for entry in json.loads((BASE / "upstream-source.json").read_bytes())["files"]
        if entry["path"].startswith("openevolve/")
    }
    for filename in (
        "manifest.json",
        "control-manifest.json",
        "observer-manifest.json",
        "prompt-manifest.json",
        "frontier-manifest.json",
        "evaluation-manifest.json",
    ):
        raw = (BASE / "patches" / filename).read_bytes()
        if sha(raw) != proof["series"][filename]:
            raise ValueError("Phase6 manifest chain drift")
        for patch in json.loads(raw)["patches"]:
            for entry in patch["files"]:
                if expected[entry["path"]] != entry["before_sha256"]:
                    raise ValueError("Phase6 source chain drift")
                expected[entry["path"]] = entry["after_sha256"]
    source = Path(source)
    if source.is_symlink() or (source / "openevolve").is_symlink():
        raise ValueError("Symlink source forbidden")
    found = {}
    for file in (source / "openevolve").rglob("*"):
        if file.is_symlink():
            raise ValueError("Symlink source forbidden")
        if file.is_file() and "__pycache__" not in file.parts:
            found[file.relative_to(source).as_posix()] = sha(file.read_bytes())
    if found != expected:
        raise ValueError("Full phase6 package inventory differs")
    raw = (ROOT / manifest["patch"]).read_bytes()
    if sha(raw) != manifest["patch_sha256"]:
        raise ValueError("Sampling patch drift")
    sections = split_files(raw.decode())
    name = manifest["file"]
    if set(sections) != {name} or name != "openevolve/process_parallel.py":
        raise ValueError("Unexpected sampling patch target")
    original = (source / name).read_bytes()
    if sha(original) != manifest["before_sha256"]:
        raise ValueError("Sampling patch input differs")
    updated = apply_unified(original.decode(), sections[name]).encode()
    if sha(updated) != manifest["after_sha256"]:
        raise ValueError("Sampling patch output differs")
    (source / name).write_bytes(updated)
    return {name: sha(updated)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    print(json.dumps(apply(parser.parse_args().source), sort_keys=True))
