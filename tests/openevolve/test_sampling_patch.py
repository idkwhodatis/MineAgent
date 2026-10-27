"""Separate phase7 hash contract; does not modify frozen phase6 runtime files."""

import difflib
import json
from pathlib import Path

import pytest

from runtimes.openevolve_phase7 import apply as patcher


def test_phase7_chains_exact_frozen_phase6():
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads(
        (root / "runtimes/openevolve_phase7/manifest.json").read_bytes()
    )
    proof_raw = (
        root / "runtimes/openevolve/patches/evaluation-build-proof.json"
    ).read_bytes()
    assert patcher.sha(proof_raw) == manifest["requires_proof_sha256"]
    assert json.loads(proof_raw)["wheel"]["sha256"] == manifest["requires_wheel_sha256"]
    phase5 = json.loads(
        (root / "runtimes/openevolve/patches/frontier-manifest.json").read_bytes()
    )
    prior = next(
        f
        for p in phase5["patches"]
        for f in p["files"]
        if f["path"] == manifest["file"]
    )
    assert manifest["before_sha256"] == prior["after_sha256"]
    assert (
        patcher.sha(
            (root / "runtimes/openevolve_phase7" / manifest["patch"]).read_bytes()
        )
        == manifest["patch_sha256"]
    )


@pytest.fixture
def tree(tmp_path, monkeypatch):
    base, control, source = (tmp_path / n for n in ("base", "control", "source"))
    (base / "patches").mkdir(parents=True)
    control.mkdir()
    (source / "openevolve").mkdir(parents=True)
    old, new = "before\n", "after\n"
    name = "openevolve/process_parallel.py"
    (source / name).write_text(old)
    (base / "upstream-source.json").write_text(
        json.dumps({"files": [{"path": name, "sha256": patcher.sha(old.encode())}]})
    )
    series = {}
    for file in (
        "manifest.json",
        "control-manifest.json",
        "observer-manifest.json",
        "prompt-manifest.json",
        "frontier-manifest.json",
        "evaluation-manifest.json",
    ):
        raw = json.dumps({"patches": []}).encode()
        (base / "patches" / file).write_bytes(raw)
        series[file] = patcher.sha(raw)
    proof = json.dumps({"series": series, "wheel": {"sha256": "a" * 64}}).encode()
    (base / "patches/evaluation-build-proof.json").write_bytes(proof)
    patch = "".join(
        difflib.unified_diff(
            old.splitlines(True),
            new.splitlines(True),
            fromfile="a/" + name,
            tofile="b/" + name,
        )
    ).encode()
    pn = "0007-observe-native-sampling-state.patch"
    (control / pn).write_bytes(patch)
    manifest = {
        "schema": "mineagent.openevolve-sampling-patch.v1",
        "upstream_commit": "8c65f74aa906abc0b0e9cae948ddf4c4c6570bdc",
        "requires_proof_sha256": patcher.sha(proof),
        "requires_wheel_sha256": "a" * 64,
        "patch": pn,
        "patch_sha256": patcher.sha(patch),
        "file": name,
        "before_sha256": patcher.sha(old.encode()),
        "after_sha256": patcher.sha(new.encode()),
    }
    (control / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(patcher, "BASE", base)
    monkeypatch.setattr(patcher, "ROOT", control)
    return source, control


def test_apply_exact_once(tree):
    source, _ = tree
    patcher.apply(source)
    assert (source / "openevolve/process_parallel.py").read_text() == "after\n"
    with pytest.raises(ValueError, match="inventory"):
        patcher.apply(source)


@pytest.mark.parametrize("change", ["extra", "changed", "symlink", "patch"])
def test_rejects_drift_before_write(tree, change):
    source, control = tree
    target = source / "openevolve/process_parallel.py"
    if change == "extra":
        (source / "openevolve/extra.py").write_text("extra")
    elif change == "changed":
        target.write_text("changed\n")
    elif change == "symlink":
        (source / "openevolve/extra.py").symlink_to(target)
    else:
        (control / "0007-observe-native-sampling-state.patch").write_text("changed")
    before = target.read_bytes()
    with pytest.raises(ValueError):
        patcher.apply(source)
    assert target.read_bytes() == before
