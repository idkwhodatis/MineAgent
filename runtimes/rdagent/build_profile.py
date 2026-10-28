"""Deterministic, explicitly reduced RD profile wheels (not pristine rdagent).

The upstream package declaration builds an empty wheel at the pinned commit.
This packaging-only recipe copies byte-verified original modules/resources;
no upstream algorithm is patched. It has no build dependencies beyond Python.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

PREFIXES = (
    "rdagent/components/coder/CoSTEER/",
    "rdagent/components/coder/factor_coder/",
    "rdagent/components/knowledge_management/",
    "rdagent/core/",
    "rdagent/log/",
    "rdagent/oai/",
    "rdagent/utils/",
)
SINGLE = {"rdagent/app/kaggle/conf.py"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def wheel(output, name, files, dependencies):
    info = f"{name}-0.1.0.dist-info"
    files = dict(files)
    files[f"{info}/METADATA"] = (
        f"Metadata-Version: 2.3\nName: {name.replace('_', '-')}\nVersion: 0.1.0\n"
        "Summary: MineAgent reduced pinned RD CoSTEER profile; not full upstream CLI\n"
        "Requires-Python: >=3.12,<3.13\n"
        + "".join(f"Requires-Dist: {d}\n" for d in dependencies)
        + "\n"
    ).encode()
    files[f"{info}/WHEEL"] = (
        b"Wheel-Version: 1.0\nGenerator: mineagent-profile-v1\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    )
    records = io.StringIO(newline="")
    writer = csv.writer(records, lineterminator="\n")
    for path, data in sorted(files.items()):
        digest = (
            base64.urlsafe_b64encode(hashlib.sha256(data).digest())
            .rstrip(b"=")
            .decode()
        )
        writer.writerow((path, f"sha256={digest}", len(data)))
    writer.writerow((f"{info}/RECORD", "", ""))
    files[f"{info}/RECORD"] = records.getvalue().encode()
    target = output / f"{name}-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
        for path, data in sorted(files.items()):
            entry = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            entry.external_attr = 0o644 << 16
            archive.writestr(entry, data)
    return {
        "filename": target.name,
        "sha256": sha(target.read_bytes()),
        "files": {p: sha(d) for p, d in sorted(files.items())},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    profile = root / "runtimes/rdagent"
    inventory = json.loads((profile / "source-inventory.json").read_text())
    if inventory["commit"] != "484776c211e4fbbeef03e0ec00d6bbee7362a4f4":
        raise ValueError("inventory commit drift")
    source_root = args.source.absolute()
    if source_root.is_symlink() or any(
        parent.is_symlink() for parent in source_root.parents
    ):
        raise ValueError("source root symlink")
    actual_commit = subprocess.check_output(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"], text=True
    ).strip()
    dirty = subprocess.check_output(
        [
            "git",
            "-C",
            str(source_root),
            "status",
            "--porcelain",
            "--untracked-files=no",
        ],
        text=True,
    )
    if actual_commit != inventory["commit"] or dirty:
        raise ValueError("source commit/worktree drift")
    files = {}
    for path, expected in inventory["files"].items():
        relative = PurePosixPath(path)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not path.startswith("rdagent/")
        ):
            raise ValueError("invalid inventory path")
        source = source_root.joinpath(*relative.parts)
        if source.is_symlink() or any(parent.is_symlink() for parent in source.parents):
            raise ValueError("source ancestor symlink")
        if not source.resolve().is_relative_to(source_root.resolve()):
            raise ValueError("source escapes checkout")
        data = source.read_bytes()
        if sha(data) != expected:
            raise ValueError(f"source drift: {path}")
        if path in SINGLE or path.startswith(PREFIXES) or path.endswith(".yaml"):
            files[path] = data
    files[
        "mineagent_rdagent_upstream_profile-0.1.0.dist-info/licenses/LICENSE.rdagent"
    ] = (profile / "LICENSE.rdagent").read_bytes()
    dependencies = [
        line.split()[0]
        for line in (profile / "requirements.lock").read_text().splitlines()
        if line and not line.startswith(("#", " "))
    ]
    args.output.mkdir(parents=True, exist_ok=True)
    upstream = wheel(
        args.output, "mineagent_rdagent_upstream_profile", files, dependencies
    )
    adapter_files = (
        "packages/rdagent_adapter/__init__.py",
        "packages/rdagent_adapter/contracts.py",
        "packages/rdagent_adapter/controller_profile.py",
        "packages/rdagent_adapter/controller_limits.py",
        "packages/rdagent_adapter/prefix.py",
        "packages/rdagent_adapter/broker_bridge.py",
        "packages/rdagent_adapter/upstream.py",
        "runtimes/rdagent/bootstrap.py",
        "runtimes/rdagent/probe_costeer.py",
        "runtimes/rdagent/probe_runtime.py",
        "runtimes/rdagent/probe_fixture.py",
        "packages/factors/__init__.py",
        "packages/factors/contracts.py",
        "packages/research_runtime/__init__.py",
        "packages/research_runtime/control.py",
        "packages/research_runtime/budget_contracts.py",
    )
    adapter = {path: (root / path).read_bytes() for path in adapter_files}
    adapter_wheel = wheel(
        args.output,
        "mineagent_rdagent_adapter_profile",
        adapter,
        ["mineagent-rdagent-upstream-profile==0.1.0"],
    )
    report = {
        "schema": "mineagent.rdagent-wheel-build.v1",
        "commit": inventory["commit"],
        "recipe_sha256": sha(Path(__file__).read_bytes()),
        "dependency_lock_sha256": sha((profile / "requirements.lock").read_bytes()),
        "upstream": upstream,
        "adapter": adapter_wheel,
    }
    (args.output / "build-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    print(
        json.dumps(
            {
                k: v["sha256"]
                for k, v in (("upstream", upstream), ("adapter", adapter_wheel))
            }
        )
    )


if __name__ == "__main__":
    main()
