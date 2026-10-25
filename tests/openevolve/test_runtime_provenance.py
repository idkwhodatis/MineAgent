"""Build/input contracts; installed-import proof is a separate runtime command."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtimes.openevolve import materialize_source, verify_build_inputs

ROOT = Path(__file__).resolve().parents[2] / "runtimes/openevolve"


def test_full_declared_upstream_dependency_closure_has_exact_manifest():
    names = {
        line.split(">=")[0]
        for line in (ROOT / "requirements.in").read_text().splitlines()
        if line and not line.startswith("#")
    }
    assert names == {
        "openai",
        "pyyaml",
        "numpy",
        "tqdm",
        "flask",
        "dacite",
        "cloudpickle",
    }
    manifest = json.loads((ROOT / "runtime-manifest.json").read_text())
    assert names <= set(manifest["dependencies"])
    for file, key in (
        ("requirements.lock", "requirements_sha256"),
        ("build-requirements.lock", "build_requirements_sha256"),
    ):
        assert hashlib.sha256((ROOT / file).read_bytes()).hexdigest() == manifest[key]
    assert manifest["upstream_commit"] == materialize_source.COMMIT
    assert "@sha256:" in manifest["base_image"]
    assert manifest["upstream_wheel"]["sha256"] in (ROOT / "Dockerfile").read_text()


def test_license_and_package_inventory_are_explicit():
    manifest = json.loads((ROOT / "upstream-source.json").read_text())
    files = {item["path"]: item for item in manifest["files"]}
    assert len(files) == len(manifest["files"]) == 66
    assert (
        hashlib.sha256((ROOT / "LICENSE.upstream").read_bytes()).hexdigest()
        == files["LICENSE"]["sha256"]
    )
    assert {
        "openevolve/api.py",
        "openevolve/process_parallel.py",
        "openevolve/prompt/sampler.py",
        "openevolve/population.py",
        "pyproject.toml",
    } <= set(files)
    docker = (ROOT / "Dockerfile").read_text()
    assert docker.index("verify_build_inputs.py /tmp/source") < docker.index(
        "python -m build"
    )


@pytest.fixture
def source(tmp_path, monkeypatch):
    control = tmp_path / "control"
    control.mkdir()
    checkout = tmp_path / "checkout"
    (checkout / "openevolve").mkdir(parents=True)
    data = b"# upstream fixture\n"
    (checkout / "openevolve/api.py").write_bytes(data)
    manifest = {
        "schema": "mineagent.openevolve-source.v1",
        "commit": materialize_source.COMMIT,
        "tree": "a" * 40,
        "source_date_epoch": 1700000000,
        "files": [
            {
                "path": "openevolve/api.py",
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        ],
    }
    (control / "upstream-source.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(materialize_source, "ROOT", control)
    monkeypatch.setattr(
        verify_build_inputs, "__file__", str(control / "verify_build_inputs.py")
    )
    monkeypatch.setattr(
        materialize_source.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            stdout=materialize_source.COMMIT + "\n" + "a" * 40 + "\n"
        ),
    )
    return checkout


def test_materialization_is_verified_and_non_overwriting(source, tmp_path):
    output = tmp_path / "prepared"
    materialize_source.materialize(source, output)
    verify_build_inputs.verify(output)
    with pytest.raises(FileExistsError):
        materialize_source.materialize(source, output)


@pytest.mark.parametrize("mutation", ["extra", "changed", "missing", "symlink"])
def test_build_bytes_must_match_before_backend_execution(source, tmp_path, mutation):
    output = tmp_path / "prepared"
    materialize_source.materialize(source, output)
    path = output / "openevolve/api.py"
    if mutation == "extra":
        (output / "unexpected.py").write_text("raise Exception()")
    elif mutation == "changed":
        path.write_text("raise Exception()")
    elif mutation == "missing":
        path.unlink()
    else:
        path.unlink()
        path.symlink_to(source / "openevolve/api.py")
    with pytest.raises(ValueError):
        verify_build_inputs.verify(output)


def test_checkout_revision_mismatch_rejects_before_destination(
    source, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        materialize_source.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stdout="b" * 40 + "\n" + "a" * 40 + "\n"),
    )
    output = tmp_path / "prepared"
    with pytest.raises(ValueError, match="exact supported commit"):
        materialize_source.materialize(source, output)
    assert not output.exists()


@pytest.mark.parametrize("method", ["connect", "connect_ex"])
def test_import_proof_rejects_even_swallowed_connection_attempts(method):
    import socket

    from runtimes.openevolve.verify_runtime import blocked_connections

    original = getattr(socket.socket, method)
    with (
        pytest.raises(ValueError, match="attempted socket connections"),
        blocked_connections() as attempts,
    ):
        with socket.socket() as probe:
            try:
                getattr(probe, method)(("127.0.0.1", 9))
            except RuntimeError:
                pass
        assert attempts == [1]
    assert getattr(socket.socket, method) is original
