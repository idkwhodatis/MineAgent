"""Require the distinct installed027 hook inventory before invoking upstream."""

import hashlib
import importlib.util
import json
from pathlib import Path


def verify_installed_hooks(expected_manifest_sha256):
    raw = Path(__file__).with_name("hook-manifest.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_manifest_sha256:
        raise ValueError("Structured upstream hook commitment differs")
    manifest = json.loads(raw)
    package = importlib.util.find_spec("openevolve")
    if package is None or package.origin is None:
        raise ValueError("Installed upstream package required")
    root = Path(package.origin).parent.parent
    found = {}
    for target in (root / "openevolve").rglob("*"):
        if target.is_symlink():
            raise ValueError("Symlink installed upstream source")
        if target.is_file() and "__pycache__" not in target.parts:
            found[target.relative_to(root).as_posix()] = hashlib.sha256(
                target.read_bytes()
            ).hexdigest()
    if found != manifest["installed_source_inventory"]:
        raise ValueError(
            "Distinct027 full installed source required; legacy024 cannot run JSON"
        )
    return True
