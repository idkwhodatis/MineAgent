"""Private staging modes survive umask; these are not Docker acceptance tests."""

import importlib
import os
import stat
import subprocess
import sys

import pytest

MODULES = (
    "runtimes.research_supervisor.numeric_supervisor",
    "runtimes.research_supervisor.openevolve_native_supervisor",
)


@pytest.mark.parametrize("module", MODULES)
@pytest.mark.parametrize("mask", [0o022, 0o077])
def test_created_artifact_modes_ignore_umask_only_for_new_inode(tmp_path, module, mask):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib, os, stat, sys
from pathlib import Path
writer = importlib.import_module(sys.argv[1])._write_bytes
root = Path(sys.argv[2])
os.umask(int(sys.argv[3]))
for mode in (0o444, 0o600):
    path = root / str(mode)
    writer(path, b'complete artifact', mode)
    assert path.read_bytes() == b'complete artifact'
    assert stat.S_IMODE(path.stat().st_mode) == mode
    assert path.stat().st_uid == os.getuid()
    try:
        writer(path, b'replacement', 0o666)
    except FileExistsError:
        pass
    else:
        raise AssertionError('overwrote existing artifact')
    assert path.read_bytes() == b'complete artifact'
    assert stat.S_IMODE(path.stat().st_mode) == mode
    link = root / ('link-' + str(mode))
    link.symlink_to(path)
    try:
        writer(link, b'replacement', 0o666)
    except FileExistsError:
        pass
    else:
        raise AssertionError('followed symlink')
    assert path.read_bytes() == b'complete artifact'
    assert stat.S_IMODE(path.stat().st_mode) == mode
""",
            module,
            str(tmp_path),
            str(mask),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("module", MODULES)
@pytest.mark.parametrize("failure", [None, "write", "chmod"])
def test_created_artifact_write_mode_sync_order_and_failure_closes_fds(
    tmp_path, monkeypatch, module, failure
):
    writer = importlib.import_module(module)._write_bytes
    events, closed = [], []
    write, chmod, sync, close = os.write, os.fchmod, os.fsync, os.close

    def write_part(fd, data):
        events.append("write")
        if failure == "write":
            raise OSError("injected write failure")
        return write(fd, data[:2])

    def set_mode(fd, mode):
        events.append("chmod")
        if failure == "chmod":
            raise OSError("injected chmod failure")
        return chmod(fd, mode)

    def flush(fd):
        events.append(
            "directory_sync" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file_sync"
        )
        return sync(fd)

    def close_fd(fd):
        closed.append(fd)
        return close(fd)

    monkeypatch.setattr(os, "write", write_part)
    monkeypatch.setattr(os, "fchmod", set_mode)
    monkeypatch.setattr(os, "fsync", flush)
    monkeypatch.setattr(os, "close", close_fd)
    if failure:
        with pytest.raises(OSError, match=f"injected {failure} failure"):
            writer(tmp_path / "artifact", b"12345", 0o444)
        assert "file_sync" not in events and "directory_sync" not in events
        if failure == "write":
            assert "chmod" not in events
    else:
        writer(tmp_path / "artifact", b"12345", 0o444)
        assert events == [
            "write",
            "write",
            "write",
            "chmod",
            "file_sync",
            "directory_sync",
        ]
    # The final two closes are the exclusively created file and pinned parent.
    for fd in closed[-2:]:
        with pytest.raises(OSError):
            os.fstat(fd)
