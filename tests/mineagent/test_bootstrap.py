"""Offline tests for the explicit external compatibility boundary."""

import copy
import importlib.util
import json
import os
import stat
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
    assert stat.S_IMODE((destination / "requirements.txt").stat().st_mode) == 0o644
    staging = destination / ".runtime/compatibility-bootstrap"
    first_staging = sorted(path.relative_to(staging) for path in staging.rglob("*"))
    assert bootstrap.materialize(destination, payloads) == 0
    assert (
        sorted(path.relative_to(staging) for path in staging.rglob("*"))
        == first_staging
    )
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


def _ordered(payloads, first):
    return {first: payloads[first]} | {p: b for p, b in payloads.items() if p != first}


def test_parent_swap_immediately_before_publication_cannot_follow_symlink(
    destination,
    payloads,
    monkeypatch,
):
    outside = destination / "outside"
    outside.mkdir()
    actual_link = os.link

    def swap_then_link(source, target, **kwargs):
        if target == "evaluate.py":
            (destination / "src").rename(destination / "original-src")
            (destination / "src").symlink_to(outside, target_is_directory=True)
        return actual_link(source, target, **kwargs)

    monkeypatch.setattr(bootstrap.os, "link", swap_then_link)
    with pytest.raises(bootstrap.BootstrapError, match="Symlink"):
        bootstrap.materialize(destination, _ordered(payloads, "src/evaluate.py"))
    assert list(outside.iterdir()) == []
    assert (destination / "original-src/evaluate.py").read_bytes() == payloads[
        "src/evaluate.py"
    ]


def test_failure_never_unlinks_replacement_at_final_name(
    destination, payloads, monkeypatch
):
    actual_read = bootstrap._read_exact
    replaced = False

    def replace_after_publication(parent, name, body):
        nonlocal replaced
        result = actual_read(parent, name, body)
        if name == "requirements.txt" and result is not None and not replaced:
            replaced = True
            target = destination / name
            target.rename(destination / "original-created")
            target.write_bytes(b"unrelated replacement")
            raise bootstrap.BootstrapError("injected failure before cleanup")
        return result

    def forbidden_unlink(*args, **kwargs):
        raise AssertionError("No pathname cleanup is safe after concurrent replacement")

    monkeypatch.setattr(bootstrap, "_read_exact", replace_after_publication)
    monkeypatch.setattr(bootstrap.os, "unlink", forbidden_unlink)
    with pytest.raises(bootstrap.BootstrapError, match="injected failure"):
        bootstrap.materialize(destination, _ordered(payloads, "requirements.txt"))
    assert (destination / "requirements.txt").read_bytes() == b"unrelated replacement"
    assert (destination / "original-created").read_bytes() == payloads[
        "requirements.txt"
    ]


def test_existing_file_is_rechecked_after_preflight(destination, payloads, monkeypatch):
    target = destination / "requirements.txt"
    target.write_bytes(payloads["requirements.txt"])
    actual_read = bootstrap._read_exact
    changed = False

    def change_after_first_read(parent, name, body):
        nonlocal changed
        result = actual_read(parent, name, body)
        if name == "requirements.txt" and not changed:
            changed = True
            target.write_bytes(b"changed after preflight")
        return result

    monkeypatch.setattr(bootstrap, "_read_exact", change_after_first_read)
    with pytest.raises(bootstrap.BootstrapError, match="differing local"):
        bootstrap.materialize(destination, _ordered(payloads, "requirements.txt"))
    assert target.read_bytes() == b"changed after preflight"
    assert not (destination / ".runtime").exists()


@pytest.mark.parametrize("same_bytes", [True, False])
def test_final_verification_rejects_replaced_leaf(
    destination, payloads, monkeypatch, same_bytes
):
    actual_verify = bootstrap._verify_final

    def replace_before_final(root, root_fd, content, identities):
        target = root / "requirements.txt"
        target.rename(root / "original-final")
        target.write_bytes(
            content["requirements.txt"]
            if same_bytes
            else b"changed at final verification"
        )
        return actual_verify(root, root_fd, content, identities)

    monkeypatch.setattr(bootstrap, "_verify_final", replace_before_final)
    with pytest.raises(
        bootstrap.BootstrapError, match="identity changed|differing local"
    ):
        bootstrap.materialize(destination, payloads)
    assert (destination / "requirements.txt").exists()
    assert (destination / "original-final").read_bytes() == payloads["requirements.txt"]


def test_final_verification_rejects_replaced_root(destination, payloads, monkeypatch):
    actual_verify = bootstrap._verify_final
    moved = destination.with_name(destination.name + "-original")

    def replace_before_final(root, root_fd, content, identities):
        root.rename(moved)
        root.mkdir()
        (root / "unrelated").write_text("preserve")
        return actual_verify(root, root_fd, content, identities)

    monkeypatch.setattr(bootstrap, "_verify_final", replace_before_final)
    with pytest.raises(
        bootstrap.BootstrapError, match="Repository directory identity changed"
    ):
        bootstrap.materialize(destination, payloads)
    assert (destination / "unrelated").read_text() == "preserve"
    assert (moved / "requirements.txt").read_bytes() == payloads["requirements.txt"]


def test_staging_replacement_before_link_is_detected_without_cleanup(
    destination, payloads, monkeypatch
):
    actual_link = os.link
    changed = False

    def replace_staging_then_link(source, target, **kwargs):
        nonlocal changed
        if not changed:
            changed = True
            source_fd = kwargs["src_dir_fd"]
            os.rename(
                source, source + "-original", src_dir_fd=source_fd, dst_dir_fd=source_fd
            )
            fd = os.open(
                source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=source_fd
            )
            with os.fdopen(fd, "wb") as stream:
                stream.write(b"unrelated staged replacement")
        return actual_link(source, target, **kwargs)

    monkeypatch.setattr(bootstrap.os, "link", replace_staging_then_link)
    with pytest.raises(bootstrap.BootstrapError, match="differing local"):
        bootstrap.materialize(destination, _ordered(payloads, "requirements.txt"))
    assert (
        destination / "requirements.txt"
    ).read_bytes() == b"unrelated staged replacement"
    assert any(
        p.read_bytes() == b"unrelated staged replacement"
        for p in (destination / ".runtime").rglob("*")
        if p.is_file()
    )


def test_fail_closed_without_descriptor_primitives(destination, payloads, monkeypatch):
    monkeypatch.setattr(bootstrap.os, "supports_dir_fd", set())
    with pytest.raises(bootstrap.BootstrapError, match="primitives are required"):
        bootstrap.materialize(destination, payloads)
    assert not (destination / ".runtime").exists()
    assert not (destination / "requirements.txt").exists()


@pytest.mark.parametrize("replace_root", [False, True])
def test_swap_during_final_read_rechecks_directory_identity(
    destination,
    payloads,
    monkeypatch,
    replace_root,
):
    actual_verify = bootstrap._verify_final
    actual_read = bootstrap._read_exact
    in_final = False
    changed = False
    outside = destination.with_name(destination.name + "-outside")
    outside.mkdir()

    def mark_final(*args):
        nonlocal in_final
        in_final = True
        return actual_verify(*args)

    def swap_after_final_read(parent, name, body):
        nonlocal changed
        result = actual_read(parent, name, body)
        if in_final and name == "evaluate.py" and not changed:
            changed = True
            if replace_root:
                destination.rename(
                    destination.with_name(destination.name + "-original")
                )
                destination.mkdir()
                (destination / "preserve").write_text("unrelated")
            else:
                (destination / "src").rename(destination / "old-src")
                (destination / "src").symlink_to(outside, target_is_directory=True)
        return result

    monkeypatch.setattr(bootstrap, "_verify_final", mark_final)
    monkeypatch.setattr(bootstrap, "_read_exact", swap_after_final_read)
    with pytest.raises(bootstrap.BootstrapError, match="identity changed|Symlink"):
        bootstrap.materialize(destination, _ordered(payloads, "src/evaluate.py"))
    assert list(outside.iterdir()) == []
    if replace_root:
        assert (destination / "preserve").read_text() == "unrelated"
