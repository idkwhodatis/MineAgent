"""Fixed installed-only proof entry point, not a production research supervisor."""

import json
import os
import socket
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def fixture_directory():
    """Undo upstream process cwd changes before removing the scratch directory."""
    original = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="qlib-installed-pair-") as directory:
        try:
            yield Path(directory)
        finally:
            # configure_upstream changes cwd. Removing that cwd first makes
            # subsequent Python subprocess startup fail before its module runs.
            os.chdir(original)


def verify_pair(qlib_python):
    attempts = []
    old, old_ex = socket.socket.connect, socket.socket.connect_ex

    def deny(*args, **kwargs):
        attempts.append(1)
        raise RuntimeError("Qlib proof network attempt")

    socket.socket.connect = socket.socket.connect_ex = deny
    try:
        from runtimes.qlib import verify_rd_runner as probe

        probe.QLIB_PYTHON = qlib_python
        with fixture_directory() as directory:
            result = probe.verify(directory)
        direct = subprocess.run(
            [str(qlib_python), "-I", "-m", "runtimes.qlib.verify_direct"],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": "/tmp",
                "MLFLOW_DISABLE_TELEMETRY": "true",
                "DO_NOT_TRACK": "true",
            },
        )
        if len(direct.stdout) > 131072:
            raise ValueError("Direct proof output budget exceeded")
        direct = json.loads(direct.stdout)
        origins = {
            name: str(module.__file__)
            for name, module in sys.modules.items()
            if (name.startswith(("rdagent", "packages.qlib_adapter", "runtimes.qlib")))
            and getattr(module, "__file__", None)
        }
        assert origins and all("site-packages" in path for path in origins.values())
        assert not attempts
        return {
            "schema": "mineagent.qlib-installed-pair-proof.v1",
            "isolated_python": bool(sys.flags.isolated),
            "module_paths_installed_only": True,
            "socket_connect_attempts": len(attempts),
            "actual_upstream_runner": result["actual_upstream_runner"],
            "baseline": result["baseline"],
            "combined": result["combined"],
            "direct": direct,
            "uid": os.getuid(),
            "scope": "synthetic_installed_qlib_and_rd_runner_only",
            "rd_feedback_scope": "validation_only",
            "production_admission": False,
            "hosted_acceptance": False,
        }
    finally:
        socket.socket.connect, socket.socket.connect_ex = old, old_ex


if __name__ == "__main__":
    if sys.argv[1:] or not sys.flags.isolated or os.getuid() == 0:
        raise SystemExit("Proof requires fixed isolated nonroot entry point")
    print(
        json.dumps(verify_pair(Path("/opt/qlib/bin/python")), sort_keys=True, indent=2)
    )
