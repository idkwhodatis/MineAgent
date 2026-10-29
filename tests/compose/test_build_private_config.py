"""Build transport only: no Docker process/socket is used by these tests."""

import os
import stat
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from script import build_rd_compose_images as builder


@pytest.mark.parametrize("outcome", ["success", "exit", "timeout"])
def test_private_empty_per_command_config_and_exact_transport(monkeypatch, outcome):
    observed = []
    arguments = ["build", "--pull=false", "--network", "none", "."]
    monkeypatch.setenv("DOCKER_CONFIG", "/unread-operator-config")
    monkeypatch.setenv("DOCKER_HOST", "tcp://untrusted:2375")
    monkeypatch.setenv("DOCKER_AUTH_CONFIG", "must-not-forward")
    result = SimpleNamespace(stdout="built")

    def run(command, **kwargs):
        directory = Path(command[2])
        observed.append(directory)
        info = directory.lstat()
        assert stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700
        assert info.st_uid == os.getuid() and not list(directory.iterdir())
        assert command == [
            "docker",
            "--config",
            str(directory),
            "--host",
            "unix:///var/run/docker.sock",
            *arguments,
        ]
        assert kwargs == {
            "cwd": builder.ROOT,
            "check": True,
            "timeout": 42,
            "capture_output": True,
            "text": True,
            "env": {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": "/nonexistent",
            },
        }
        # Simulate metadata written by Buildx, never credentials from the host.
        (directory / "buildx").mkdir()
        (directory / "buildx" / "state").write_text("synthetic builder state")
        if outcome == "exit":
            raise subprocess.CalledProcessError(1, command)
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(command, 42)
        return result

    monkeypatch.setattr(builder.subprocess, "run", run)
    if outcome == "success":
        assert builder._docker(arguments, capture=True, timeout=42) is result
        assert builder._docker(arguments, capture=True, timeout=42) is result
        assert observed[0] != observed[1]
    else:
        with pytest.raises(
            subprocess.CalledProcessError
            if outcome == "exit"
            else subprocess.TimeoutExpired
        ):
            builder._docker(arguments, capture=True, timeout=42)
    assert observed and all(not directory.exists() for directory in observed)


@pytest.mark.parametrize("kind", ["symlink", "public", "populated", "foreign_owner"])
def test_nonprivate_config_rejected_before_process(tmp_path, monkeypatch, kind):
    directory = tmp_path / "config"
    directory.mkdir(mode=0o700)
    target = directory
    if kind == "symlink":
        target = tmp_path / "link"
        target.symlink_to(directory, target_is_directory=True)
    elif kind == "public":
        directory.chmod(0o755)
    elif kind == "populated":
        (directory / "config.json").write_text("untrusted")
    elif kind == "foreign_owner":
        monkeypatch.setattr(builder.os, "getuid", lambda: directory.stat().st_uid + 1)

    @contextmanager
    def prepared(**kwargs):
        yield str(target)

    monkeypatch.setattr(builder, "TemporaryDirectory", prepared)
    monkeypatch.setattr(
        builder.subprocess, "run", lambda *a, **kw: pytest.fail("must not spawn")
    )
    with pytest.raises(ValueError, match="Empty private owned"):
        builder._docker(["build", "."])
