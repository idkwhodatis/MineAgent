"""Build/hash/confinement contracts; does not pretend to execute Docker."""

import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from runtimes.qlib.verify_build import COMMIT, source_files, verify_profile, wheelhouse

ROOT = Path(__file__).resolve().parents[2]


def test_source_inventory_rejects_extra_drift_and_symlink(tmp_path):
    (tmp_path / "qlib").mkdir()
    p = tmp_path / "qlib/example.py"
    p.write_text("x=1\n")
    inventory = {
        "commit": COMMIT,
        "schema": "mineagent.qlib-build-source.v1",
        "files": {"qlib/example.py": hashlib.sha256(p.read_bytes()).hexdigest()},
    }
    assert source_files(tmp_path, inventory) == 1
    p.write_text("x=2\n")
    with pytest.raises(ValueError, match="drift"):
        source_files(tmp_path, inventory)
    p.write_text("x=1\n")
    extra = tmp_path / "extra.py"
    extra.write_text("ignored=False")
    with pytest.raises(ValueError, match="Unexpected"):
        source_files(tmp_path, inventory)
    extra.unlink()
    p.unlink()
    p.symlink_to("/etc/hosts")
    with pytest.raises(ValueError, match="symlink"):
        source_files(tmp_path, inventory)


def test_wheelhouse_hashes_exact_bytes_and_rejects_duplicate_distribution(tmp_path):
    def wheel(path, name):
        with ZipFile(path, "w") as archive:
            archive.writestr(
                "demo-1.0.dist-info/METADATA",
                f"Metadata-Version: 2.3\nName: {name}\nVersion: 1.0\n",
            )

    path = tmp_path / "demo-1.0-py3-none-any.whl"
    wheel(path, "demo")
    hashes = wheelhouse(tmp_path)
    assert hashes[path.name] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert (
        "--hash=sha256:" + hashes[path.name] in (tmp_path / "install.lock").read_text()
    )
    wheel(tmp_path / "demo-1.0-1-py3-none-any.whl", "demo")
    with pytest.raises(ValueError, match="Duplicate"):
        wheelhouse(tmp_path)


def test_wheelhouse_ignores_vendored_metadata_but_hashes_it(tmp_path):
    path = tmp_path / "setuptools-84.2.1-py3-none-any.whl"
    with ZipFile(path, "w") as archive:
        archive.writestr(
            "setuptools-84.2.1.dist-info/METADATA",
            "Metadata-Version: 2.4\nName: setuptools\nVersion: 84.2.1\n",
        )
        archive.writestr(
            "setuptools/_vendor/packaging-26.3.dist-info/METADATA",
            "Metadata-Version: 2.4\nName: packaging\nVersion: 26.3\n",
        )
    hashes = wheelhouse(tmp_path)
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    assert hashes == {path.name: expected}
    assert (tmp_path / "install.lock").read_text() == (
        f"setuptools==84.2.1 --hash=sha256:{expected}\n"
    )


@pytest.mark.parametrize(
    "member,metadata",
    [
        ("vendor/demo-1.0.dist-info/METADATA", "Name: demo\nVersion: 1.0\n"),
        ("other-1.0.dist-info/METADATA", "Name: demo\nVersion: 1.0\n"),
        ("demo-2.0.dist-info/METADATA", "Name: demo\nVersion: 1.0\n"),
        ("demo-1.0.dist-info/METADATA", "Name: other\nVersion: 1.0\n"),
        ("demo-1.0.dist-info/METADATA", "Name: demo\nVersion: 2.0\n"),
        ("demo-1.0.dist-info/METADATA", "Name: demo\nName: other\nVersion: 1.0\n"),
        ("demo-1.0.dist-info/METADATA", "Name: demo\nVersion: 1.0\nVersion: 2.0\n"),
    ],
)
def test_wheelhouse_rejects_missing_root_or_conflicting_identity(
    tmp_path, member, metadata
):
    with ZipFile(tmp_path / "demo-1.0-py3-none-any.whl", "w") as archive:
        archive.writestr(member, metadata)
    with pytest.raises(ValueError):
        wheelhouse(tmp_path)
    assert not (tmp_path / "install.lock").exists()


@pytest.mark.parametrize("duplicate_path", [True, False])
def test_wheelhouse_rejects_duplicate_or_multiple_root_metadata(
    tmp_path, duplicate_path
):
    with ZipFile(tmp_path / "demo-1.0-py3-none-any.whl", "w") as archive:
        archive.writestr("demo-1.0.dist-info/METADATA", "Name: demo\nVersion: 1.0\n")
        if duplicate_path:
            with pytest.warns(UserWarning, match="Duplicate"):
                archive.writestr(
                    "demo-1.0.dist-info/METADATA", "Name: demo\nVersion: 1.0\n"
                )
        else:
            archive.writestr(
                "other-1.0.dist-info/METADATA", "Name: other\nVersion: 1.0\n"
            )
    with pytest.raises(ValueError):
        wheelhouse(tmp_path)


def test_wheelhouse_normalizes_distribution_name_only(tmp_path):
    with ZipFile(tmp_path / "my_demo-1.0-py3-none-any.whl", "w") as archive:
        archive.writestr(
            "my_demo-1.0.dist-info/METADATA", "Name: My-Demo\nVersion: 1.0\n"
        )
    wheelhouse(tmp_path)
    assert (tmp_path / "install.lock").read_text().startswith("My-Demo==1.0 ")


def test_profile_verifier_rejects_same_name_modified_wheel(tmp_path):
    expected = {}
    for key in ("upstream", "adapter", "native_adapter"):
        path = tmp_path / f"{key}.whl"
        path.write_bytes(key.encode())
        expected[key] = {
            "filename": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    (tmp_path / "build-report.json").write_text(json.dumps(expected))
    verify_profile(tmp_path, expected)
    (tmp_path / "adapter.whl").write_bytes(b"modified")
    with pytest.raises(ValueError, match="hash drift"):
        verify_profile(tmp_path, expected)


def test_docker_build_and_workflow_keep_disabled_fresh_installed_only_scope():
    docker = (ROOT / "runtimes/qlib/Dockerfile").read_text()
    workflow = (ROOT / ".github/workflows/mineagent-qlib-runtime.yml").read_text()
    assert "permissions:\n  contents: read\n" in workflow
    assert "contents: write" not in workflow
    commands = workflow
    for required in (
        "--network none",
        "--read-only",
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
        "--pids-limit",
        "--memory",
        "--cpus",
        "validate_proof.py",
        "--iidfile",
    ):
        assert required in commands
    assert "docker push" not in commands and "pull_request_target" not in str(workflow)
    assert docker.count("FROM python:3.12.14-slim-bookworm@sha256:") == 2
    assert "python -m venv /opt/qlib && python -m venv /opt/runner" in docker
    assert docker.count("--no-index --find-links=") == 2
    assert "--no-build-isolation --require-hashes" in docker
    assert "USER 65532:65532" in docker
    assert '"-I", "-m", "runtimes.qlib.probe_container"' in docker
    assert "COPY packages/" not in docker
    assert "pip check && /opt/runner/bin/pip check" in docker


def test_locks_are_exact_hashed_and_runner_pandas_is_compatible():
    for filename in (
        "requirements.lock",
        "runner-requirements.lock",
        "build-requirements.lock",
    ):
        lines = [
            line
            for line in (ROOT / "runtimes/qlib" / filename).read_text().splitlines()
            if line and not line.startswith("#")
        ]
        assert lines and all(
            "==" in line and " --hash=sha256:" in line for line in lines
        )
    runner = (ROOT / "runtimes/qlib/runner-requirements.lock").read_text()
    assert "pandas==2.3.3 " in runner and "pandarallel==1.6.5 " in runner


def test_profile_adapter_uses_explicit_frozen_files_not_domain_drafts():
    import ast

    tree = ast.parse((ROOT / "runtimes/qlib/build_runner_profile.py").read_text())
    assignment = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "adapter_files"
            for target in node.targets
        )
    )
    files = ast.literal_eval(assignment.value)
    assert "runtimes/rdagent/bootstrap.py" in files
    assert "runtimes/qlib/probe_container.py" in files
    assert all(
        not name.endswith(("assembly.py", "host_evidence.py", "checkpoint.py"))
        for name in files
    )
    assert not any(name.startswith("backend/") for name in files)
    assert len(files) == len(set(files))
