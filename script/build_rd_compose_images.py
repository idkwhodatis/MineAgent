"""Explicit operator build-only command for exact RD runtime image IDs.

Requires pre-materialized, hash-verified upstream wheels/source as documented.
Does not start services, enable profiles, inject secrets or contact a provider.
Partial build evidence is retained; no image/tag is silently substituted.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from backend.app.rdagent_provider_profile import RDProviderServerRuntimeProfile
from backend.app.rdagent_server_profile import ROLE_LOCKS
from packages.rdagent_adapter.contracts import DependencyLock, RuntimePins
from script.compose_provider_runtime import verify_rd_images

ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    ("orchestrator", "runtimes/rdagent_operational/Dockerfile"),
    ("candidate", "runtimes/factor_execution/Dockerfile"),
    ("normalizer", "runtimes/factor_normalizer/Dockerfile"),
    ("qlib-installed", "runtimes/qlib/Dockerfile"),
    ("analysis", "runtimes/qlib/Dockerfile.direct"),
)


def read_iid(path):
    value = path.read_text().strip()
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
        raise ValueError("Exact built image ID required")
    return value


def profile_from_ids(ids):
    return RDProviderServerRuntimeProfile(
        runtime=RuntimePins(
            **{
                role + "_image": ids[role]
                for role in ("orchestrator", "candidate", "normalizer", "analysis")
            },
            dependency_locks=tuple(
                DependencyLock(role=role, sha256=sha)
                for role, sha in sorted(ROLE_LOCKS.items())
            ),
        ),
        qlib_installed_parent_image_id=ids["qlib-installed"],
    )


def verify_prepared_inputs():
    from runtimes.factor_execution.verify_inventory import verify
    from runtimes.qlib.verify_build import verify_profile

    verify()
    for directory, report, proof, roles in (
        (
            ".runtime/rdagent-wheels",
            "build-report.json",
            "runtimes/rdagent/wheel-proof.json",
            ("upstream", "adapter"),
        ),
        (
            ".runtime/rdagent-operational-wheels",
            "operational-build-report.json",
            "runtimes/rdagent_operational/wheel-proof.json",
            ("operational",),
        ),
    ):
        actual = json.loads((ROOT / directory / report).read_text())
        expected = json.loads((ROOT / proof).read_text())
        if actual != expected:
            raise ValueError("Prepared RD wheels differ from frozen proof")
        for role in roles:
            item = actual[role]
            path = ROOT / directory / item["filename"]
            if (
                path.name != item["filename"]
                or path.is_symlink()
                or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]
            ):
                raise ValueError("Prepared RD wheel bytes differ")
    verify_profile(
        ROOT / ".runtime/qlib-wheels",
        json.loads((ROOT / "runtimes/qlib/wheel-proof.json").read_text()),
    )


def _docker(args, *, capture=False, timeout=1800):
    return subprocess.run(
        [
            "docker",
            "--config",
            "/nonexistent",
            "--host",
            "unix:///var/run/docker.sock",
            *args,
        ],
        cwd=ROOT,
        check=True,
        timeout=timeout,
        capture_output=capture,
        text=capture,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/nonexistent"},
    )


def _verify_parent_tag(tag, iid):
    observed = _docker(
        ["image", "inspect", "--format", "{{.Id}}", tag], capture=True, timeout=60
    ).stdout.strip()
    if observed != iid:
        raise ValueError("Local parent tag differs from exact built image ID")


def build(output):
    verify_prepared_inputs()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    ids = {}
    for role, dockerfile in TARGETS:
        iid = output / (role + ".iid")
        args = [
            "build",
            "--iidfile",
            str(iid),
            "-f",
            dockerfile,
        ]
        if role == "analysis":
            # Docker FROM sha256:<hex> is parsed as a registry image reference.
            # The local tag is only a build transport alias; retained authority
            # and the published runtime profile continue using actual image IDs.
            parent_id = ids["qlib-installed"]
            parent_tag = "mineagent-local-parent:" + parent_id.removeprefix("sha256:")
            _docker(["tag", parent_id, parent_tag], timeout=60)
            _verify_parent_tag(parent_tag, parent_id)
            args += [
                "--pull=false",
                "--network",
                "none",
                "--build-arg",
                "QLIB_IMAGE=" + parent_tag,
            ]
        _docker(args + ["."])
        if role == "analysis":
            _verify_parent_tag(parent_tag, parent_id)
        ids[role] = read_iid(iid)
    profile = profile_from_ids(ids)
    verify_rd_images(profile)
    path = output / "rd-provider-runtime-profile.json"
    path.write_text(profile.model_dump_json() + "\n")
    (output / "build-targets.json").write_text(
        json.dumps(dict(TARGETS), sort_keys=True) + "\n"
    )
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New private build-evidence directory",
    )
    args = parser.parse_args()
    print(build(args.output))


if __name__ == "__main__":
    main()
