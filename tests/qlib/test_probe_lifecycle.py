"""Wrapper lifecycle tests; no Qlib/RD execution, environment reinstall or network."""

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from runtimes.qlib.probe_container import fixture_directory, verify_pair


def test_restores_original_cwd_before_removal_and_subsequent_child(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    with fixture_directory() as directory:
        os.chdir(directory)
        assert Path.cwd() == directory
    assert not directory.exists()
    assert Path.cwd() == tmp_path
    child = subprocess.run(
        [sys.executable, "-I", "-c", "import os; print(os.getcwd())"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert child.stdout.strip() == str(tmp_path)


def test_exception_restores_cwd_before_scratch_cleanup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with (
        pytest.raises(RuntimeError, match="fixture failed"),
        fixture_directory() as directory,
    ):
        os.chdir(directory)
        raise RuntimeError("fixture failed")
    assert Path.cwd() == tmp_path
    assert not directory.exists()


def test_wrapper_failure_retains_network_guard_then_restores_it(tmp_path, monkeypatch):
    from runtimes.qlib import verify_rd_runner

    monkeypatch.chdir(tmp_path)
    original = socket.socket.connect

    def failed_fixture(directory):
        os.chdir(directory)
        with (
            socket.socket() as sock,
            pytest.raises(RuntimeError, match="network attempt"),
        ):
            sock.connect(("127.0.0.1", 9))
        raise RuntimeError("fixture failed")

    monkeypatch.setattr(verify_rd_runner, "verify", failed_fixture)
    with pytest.raises(RuntimeError, match="fixture failed"):
        verify_pair(Path("/unused/probe/python"))
    assert Path.cwd() == tmp_path
    assert socket.socket.connect is original
