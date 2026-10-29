"""Image-build-only exact source fork; never patches a running/shared environment."""

import hashlib

# This file is executed with -I beside the reviewed helper, not caller import paths.
# Import helper via explicit fixed __file__ directory during the image build only.
import importlib.util
import json
import sys
from pathlib import Path


def install(root):
    root = Path(root)
    here = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location(
        "structured_build_apply", here / "apply.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = json.loads((here / "hook-manifest.json").read_bytes())
    before = dict(manifest["installed_source_inventory"])
    before.update(
        {name: item["before_sha256"] for name, item in manifest["files"].items()}
    )
    found = {}
    if root.is_symlink() or (root / "openevolve").is_symlink():
        raise ValueError("Symlink installed upstream")
    for path in (root / "openevolve").rglob("*"):
        if path.is_symlink():
            raise ValueError("Symlink installed source")
        if path.is_file() and "__pycache__" not in path.parts:
            found[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    if found != before:
        raise ValueError("Exact measured024 parent source required")
    outputs = {}
    for name in module.TARGETS:
        raw = module.rewrite(name, (root / name).read_bytes())
        if hashlib.sha256(raw).hexdigest() != manifest["files"][name]["after_sha256"]:
            raise ValueError("Structured hook result differs")
        outputs[name] = raw
    for name, raw in outputs.items():
        (root / name).write_bytes(raw)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("one fixed installed source root required")
    install(sys.argv[1])
