"""Strict source patching contracts; runtime_smoke separately exercises installation."""

import difflib
import hashlib
import json
from pathlib import Path

import pytest

from runtimes.openevolve.patches import apply as patcher

ROOT = Path(__file__).resolve().parents[2] / "runtimes/openevolve"


def test_patch_pins_match_baseline_source_manifest():
    baseline = {
        f["path"]: f["sha256"]
        for f in json.loads((ROOT / "upstream-source.json").read_text())["files"]
    }
    manifest = json.loads((ROOT / "patches/manifest.json").read_text())
    assert manifest["upstream_commit"] == patcher.COMMIT
    for patch in manifest["patches"]:
        raw = (ROOT / "patches" / patch["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == patch["sha256"]
        assert set(patcher.split_files(raw.decode())) == {
            f["path"] for f in patch["files"]
        }
        for file in patch["files"]:
            assert file["before_sha256"] == baseline[file["path"]]
            assert file["before_sha256"] != file["after_sha256"]


@pytest.fixture
def build_tree(tmp_path, monkeypatch):
    old = "first\nsecond\nthird\n"
    new = "first\nreplacement\nthird\n"
    name = "openevolve/evaluator.py"
    source = tmp_path / "source"
    (source / "openevolve").mkdir(parents=True)
    (source / name).write_text(old)
    control = tmp_path / "patches"
    control.mkdir()
    diff = "".join(
        difflib.unified_diff(
            old.splitlines(True),
            new.splitlines(True),
            fromfile="a/" + name,
            tofile="b/" + name,
        )
    )
    (control / "change.patch").write_text(diff)
    manifest = {
        "schema": "mineagent.openevolve-patch-series.v1",
        "upstream_commit": patcher.COMMIT,
        "patches": [
            {
                "path": "change.patch",
                "sha256": patcher.digest(diff.encode()),
                "files": [
                    {
                        "path": name,
                        "before_sha256": patcher.digest(old.encode()),
                        "after_sha256": patcher.digest(new.encode()),
                    }
                ],
            }
        ],
    }
    (control / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(patcher, "ROOT", control)
    return source, control


def test_exact_patch_and_repeat_rejection(build_tree):
    source, _ = build_tree
    result = patcher.apply(source)
    assert (
        source / "openevolve/evaluator.py"
    ).read_text() == "first\nreplacement\nthird\n"
    assert result["upstream_commit"] == patcher.COMMIT
    with pytest.raises(ValueError, match="source drift"):
        patcher.apply(source)


@pytest.mark.parametrize(
    "mutation",
    ["source", "patch", "source_symlink", "patch_symlink", "commit", "target_hash"],
)
def test_drift_and_substitution_reject_before_write(build_tree, mutation):
    source, control = build_tree
    file = source / "openevolve/evaluator.py"
    if mutation == "source":
        file.write_text("unreviewed\n")
    elif mutation == "patch":
        (control / "change.patch").write_text("unreviewed\n")
    elif mutation.endswith("symlink"):
        path = file if mutation == "source_symlink" else control / "change.patch"
        data = path.read_bytes()
        target = control / "other"
        target.write_bytes(data)
        path.unlink()
        path.symlink_to(target)
    else:
        manifest = json.loads((control / "manifest.json").read_text())
        if mutation == "commit":
            manifest["upstream_commit"] = "f" * 40
        else:
            manifest["patches"][0]["files"][0]["after_sha256"] = "f" * 64
        (control / "manifest.json").write_text(json.dumps(manifest))
    before = file.read_bytes()
    with pytest.raises(ValueError):
        patcher.apply(source)
    assert file.read_bytes() == before


def test_hunks_never_fuzz_match_or_apply_other_paths():
    with pytest.raises(ValueError, match="context mismatch"):
        patcher.apply_unified("changed\n", ["@@ -1 +1 @@\n", "-original\n", "+new\n"])
    with pytest.raises(ValueError, match="Unexpected"):
        patcher.split_files(
            "--- a/../../outside\n+++ b/../../outside\n@@ -1 +1 @@\n-old\n+new\n"
        )


def test_control_patch_requires_exact_first_series_output():
    base = json.loads((ROOT / "patches/manifest.json").read_text())
    prior = {f["path"]: f["after_sha256"] for p in base["patches"] for f in p["files"]}
    control = json.loads((ROOT / "patches/control-manifest.json").read_text())
    assert control["requires"] == "manifest.json"
    for patch in control["patches"]:
        raw = (ROOT / "patches" / patch["path"]).read_bytes()
        assert patcher.digest(raw) == patch["sha256"]
        assert set(patcher.split_files(raw.decode())) == {
            f["path"] for f in patch["files"]
        }
        for entry in patch["files"]:
            assert entry["before_sha256"] == prior[entry["path"]]
            assert entry["after_sha256"] != entry["before_sha256"]


def test_observer_patch_hash_chain_and_closed_selector():
    previous = {
        entry["path"]: entry["sha256"]
        for entry in json.loads((ROOT / "upstream-source.json").read_text())["files"]
    }
    for filename in (
        "manifest.json",
        "control-manifest.json",
        "observer-manifest.json",
        "prompt-manifest.json",
        "frontier-manifest.json",
        "evaluation-manifest.json",
    ):
        manifest = json.loads((ROOT / "patches" / filename).read_text())
        for patch in manifest["patches"]:
            raw = (ROOT / "patches" / patch["path"]).read_bytes()
            assert patcher.digest(raw) == patch["sha256"]
            assert set(patcher.split_files(raw.decode())) == {
                f["path"] for f in patch["files"]
            }
            for file in patch["files"]:
                assert file["before_sha256"] == previous[file["path"]]
                previous[file["path"]] = file["after_sha256"]
    with pytest.raises(ValueError, match="one patch series"):
        patcher.apply(Path("unused"), control=True, observer=True)
