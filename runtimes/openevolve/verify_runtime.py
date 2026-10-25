"""Verify installed, separately locked upstream baseline without provider access."""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import pkgutil
import socket
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent


@contextmanager
def blocked_connections():
    """Count both Python socket connection APIs, including swallowed attempts."""
    originals = (socket.socket.connect, socket.socket.connect_ex)
    attempts = [0]

    def denied(*_args, **_kwargs):
        attempts[0] += 1
        raise RuntimeError("Network unavailable during import verification")

    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    try:
        yield attempts
    finally:
        socket.socket.connect, socket.socket.connect_ex = originals
    if attempts[0]:
        raise ValueError("Upstream imports attempted socket connections")


def verify() -> dict:
    manifest = json.loads((ROOT / "runtime-manifest.json").read_text())
    if sys.version_info[:3] != tuple(map(int, manifest["python"].split("."))):
        raise ValueError("OpenEvolve runtime Python differs from pin")
    for filename, key in (
        ("requirements.lock", "requirements_sha256"),
        ("build-requirements.lock", "build_requirements_sha256"),
    ):
        if hashlib.sha256((ROOT / filename).read_bytes()).hexdigest() != manifest[key]:
            raise ValueError("OpenEvolve lock manifest mismatch")
    expected = {**manifest["dependencies"], "openevolve": "0.4.0"}
    installed = {
        d.metadata["Name"].lower().replace("_", "-"): d.version
        for d in importlib.metadata.distributions()
    }
    # pip belongs to the venv installer, not the application dependency closure.
    if {k: v for k, v in installed.items() if k != "pip"} != expected:
        raise ValueError("OpenEvolve runtime dependency set differs from exact lock")
    with blocked_connections() as attempts:
        import openevolve

        modules = []
        for item in pkgutil.walk_packages(
            openevolve.__path__, openevolve.__name__ + "."
        ):
            importlib.import_module(item.name)
            modules.append(item.name)
        source = json.loads((ROOT / "upstream-source.json").read_text())
        package_parent = Path(openevolve.__file__).resolve().parent.parent
        for entry in source["files"]:
            if entry["path"].startswith("openevolve/") and (
                hashlib.sha256(
                    (package_parent / entry["path"]).read_bytes()
                ).hexdigest()
                != entry["sha256"]
            ):
                raise ValueError(
                    "Installed upstream source differs from verified baseline"
                )
    return {
        "schema": "mineagent.openevolve-import-proof.v1",
        "upstream_commit": manifest["upstream_commit"],
        "version": openevolve.__version__,
        "python": manifest["python"],
        "dependencies": expected,
        "imported_modules": modules,
        "socket_connect_attempts": attempts[0],
        "claim": "upstream baseline imports only; not search or evaluator acceptance",
    }


if __name__ == "__main__":
    print(json.dumps(verify(), sort_keys=True, indent=2))
