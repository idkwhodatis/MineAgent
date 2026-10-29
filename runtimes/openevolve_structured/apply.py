"""027 explicit import-seam fork of exact phase8 upstream, not a024 overlay.

No upstream sampler/evolution/evaluator algorithm is rewritten. The entire
reviewed phase8 package is required before the four hook-module imports change.
Never call against the shared installed environment; this is a future image
build input operation on its private source tree.
"""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGETS = {
    "openevolve/controller.py": 4,
    "openevolve/evaluator.py": 1,
    "openevolve/process_parallel.py": 6,
    "openevolve/prompt/sampler.py": 1,
}


def expected_inventory():
    from runtimes.openevolve_phase8.apply import expected_inventory as phase7_inventory

    result = phase7_inventory()
    phase8 = json.loads((ROOT.parent / "openevolve_phase8/manifest.json").read_bytes())
    if result[phase8["file"]] != phase8["before_sha256"]:
        raise ValueError("Phase8 predecessor differs")
    result[phase8["file"]] = phase8["after_sha256"]
    return result


def rewrite(path, raw):
    if path not in TARGETS or type(raw) is not bytes:
        raise ValueError("Closed structured hook target required")
    text = raw.decode("utf-8")
    old = "from packages.openevolve_adapter"
    if (
        text.count(old) != TARGETS[path]
        or text.count("packages.openevolve_adapter") != TARGETS[path]
    ):
        raise ValueError("Unexpected upstream hook inventory")
    text = text.replace(old, "from packages.openevolve_structured")
    text = text.replace(
        "packages.openevolve_structured.checkpoint_native",
        "packages.openevolve_structured.checkpoint_runtime",
    )
    return text.encode("utf-8")


def apply(source):
    source = Path(source)
    expected = expected_inventory()
    found = {}
    if source.is_symlink() or (source / "openevolve").is_symlink():
        raise ValueError("Symlink upstream source")
    for path in (source / "openevolve").rglob("*"):
        if path.is_symlink():
            raise ValueError("Symlink upstream package")
        if path.is_file() and "__pycache__" not in path.parts:
            found[path.relative_to(source).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    if found != expected:
        raise ValueError("Exact reviewed phase8 upstream source required")
    manifest = json.loads((ROOT / "hook-manifest.json").read_bytes())
    if (
        manifest.get("schema_version") != "mineagent.oe-structured-import-seams.v1"
        or manifest.get("requires_phase8_manifest_sha256")
        != hashlib.sha256(
            (ROOT.parent / "openevolve_phase8/manifest.json").read_bytes()
        ).hexdigest()
        or set(manifest["files"]) != set(TARGETS)
    ):
        raise ValueError("Closed027 hook manifest required")
    outputs = {}
    for name in TARGETS:
        original = (source / name).read_bytes()
        output = rewrite(name, original)
        if manifest["files"][name] != {
            "before_sha256": hashlib.sha256(original).hexdigest(),
            "after_sha256": hashlib.sha256(output).hexdigest(),
        }:
            raise ValueError("027 hook bytes differ")
        outputs[name] = output
    installed = dict(expected)
    installed.update(
        {name: hashlib.sha256(raw).hexdigest() for name, raw in outputs.items()}
    )
    if manifest.get("installed_source_inventory") != installed:
        raise ValueError("Full027 installed source inventory differs")
    for name, output in outputs.items():
        (source / name).write_bytes(output)
    return {name: hashlib.sha256(raw).hexdigest() for name, raw in outputs.items()}
