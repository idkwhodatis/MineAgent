"""Offline tests for the explicit external compatibility boundary."""

import copy
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "bootstrap", ROOT / "script/bootstrap_tradegrad_compat.py"
)
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


@pytest.fixture
def destination(tmp_path):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text(
        "".join("/" + path + "\n" for path in bootstrap.ALLOWED)
    )
    return tmp_path


@pytest.fixture
def payloads():
    return {path: ("external " + path).encode() for path in bootstrap.ALLOWED}


def test_manifest_is_pinned_and_exact():
    manifest = bootstrap.load_manifest(ROOT / "compatibility/tradegrad.json")
    assert len(manifest["files"]) == 21
    assert manifest["commit"] == "6828f3bd429fc754648021bae703a14fa300b62e"
    assert "README.md" not in bootstrap.ALLOWED
    assert "LICENSE" not in bootstrap.ALLOWED
    assert "src/data_cache.py" not in bootstrap.ALLOWED
    assert "src/resources.py" not in bootstrap.ALLOWED


@pytest.mark.parametrize(
    "change", ["source", "traversal", "duplicate", "digest", "size"]
)
def test_reject_manifest_changes(tmp_path, change):
    manifest = json.loads((ROOT / "compatibility/tradegrad.json").read_text())
    if change == "source":
        manifest["repository"] = "https://example.com/untrusted.git"
    elif change == "traversal":
        manifest["files"][0]["path"] = "../outside"
    elif change == "duplicate":
        manifest["files"].append(copy.deepcopy(manifest["files"][0]))
    elif change == "digest":
        manifest["files"][0]["sha256"] = "not-a-digest"
    else:
        manifest["files"][0]["bytes"] = 10_000_000
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.load_manifest(path)


def test_materialize_idempotent_and_preserves_user_files(destination, payloads):
    (destination / "src").mkdir()
    authored = destination / "src/resources.py"
    authored.write_text("user-authored")
    license_file = destination / "LICENSE"
    license_file.write_text("mineagent license")
    assert bootstrap.materialize(destination, payloads) == 21
    assert bootstrap.materialize(destination, payloads) == 0
    assert authored.read_text() == "user-authored"
    assert license_file.read_text() == "mineagent license"
    assert subprocess.check_output(["git", "-C", str(destination), "ls-files"]) == b""


def test_refuse_different_file_before_any_writes(destination, payloads):
    existing = destination / "requirements.txt"
    existing.write_text("do not replace")
    with pytest.raises(bootstrap.BootstrapError, match="differing local"):
        bootstrap.materialize(destination, payloads)
    assert existing.read_text() == "do not replace"
    assert not (destination / "src").exists()


@pytest.mark.parametrize("parent", [False, True])
def test_refuse_symlinks(destination, payloads, parent):
    outside = destination / "outside"
    outside.mkdir()
    if parent:
        (destination / "src").symlink_to(outside, target_is_directory=True)
    else:
        (destination / "requirements.txt").symlink_to(outside / "missing")
    with pytest.raises(bootstrap.BootstrapError, match="Symlink"):
        bootstrap.materialize(destination, payloads)
    assert list(outside.iterdir()) == []


def test_refuse_tracked_dependency(destination, payloads):
    file = destination / "requirements.txt"
    file.write_bytes(payloads["requirements.txt"])
    subprocess.run(
        ["git", "-C", str(destination), "add", "-f", "requirements.txt"], check=True
    )
    with pytest.raises(bootstrap.BootstrapError, match="tracked dependency"):
        bootstrap.materialize(destination, payloads)
    assert file.read_bytes() == payloads["requirements.txt"]


def test_refuse_incomplete_payload(destination, payloads):
    payloads.pop("requirements.txt")
    with pytest.raises(bootstrap.BootstrapError, match="Incomplete"):
        bootstrap.materialize(destination, payloads)


def test_refuse_source_tree_change(tmp_path):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--allow-empty",
            "-m",
            "fixture",
        ],
        check=True,
        capture_output=True,
    )
    manifest = bootstrap.load_manifest(ROOT / "compatibility/tradegrad.json")
    with pytest.raises(bootstrap.BootstrapError, match="tree does not match"):
        bootstrap.read_payloads(tmp_path, "HEAD", manifest)


def test_all_external_paths_ignored():
    manifest = bootstrap.load_manifest(ROOT / "compatibility/tradegrad.json")
    for entry in manifest["files"]:
        assert bootstrap.git(ROOT, "check-ignore", "--", entry["path"]).strip()
        assert not bootstrap.git(ROOT, "ls-files", "--", entry["path"]).strip()


def test_every_checkout_bootstraps_before_validation():
    workflow = (ROOT / ".github/workflows/v1-validation.yml").read_text()
    sections = workflow.split("- uses: actions/checkout@")
    assert len(sections) > 1
    for section in sections[1:]:
        assert "python script/bootstrap_tradegrad_compat.py" in section
