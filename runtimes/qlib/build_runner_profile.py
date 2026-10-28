"""Deterministic, explicitly reduced RD Qlib runner profile wheels (not pristine rdagent).

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
    "rdagent/components/runner/",
    "rdagent/components/workflow/",
    "rdagent/components/coder/model_coder/",
    "rdagent/scenarios/qlib/",
    "rdagent/scenarios/shared/",
    "rdagent/core/",
    "rdagent/log/",
    "rdagent/oai/",
    "rdagent/utils/",
)
SINGLE = {"rdagent/app/kaggle/conf.py", "rdagent/app/qlib_rd_loop/conf.py"}


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


def build_adapters(root, output):
    """Build the exact runner/native adapters; shared by real builds and regressions."""
    profile = root / "runtimes/qlib"
    # Explicit frozen runtime imports only; never package active domain drafts.
    adapter_files = (
        "packages/rdagent_adapter/__init__.py",
        "packages/rdagent_adapter/contracts.py",
        "packages/rdagent_adapter/controller_profile.py",
        "packages/rdagent_adapter/controller_limits.py",
        "packages/rdagent_adapter/prefix.py",
        "packages/rdagent_adapter/broker_bridge.py",
        "packages/rdagent_adapter/upstream.py",
        "runtimes/rdagent/bootstrap.py",
        "packages/qlib_adapter/__init__.py",
        "packages/qlib_adapter/contracts.py",
        "packages/qlib_adapter/direct.py",
        "packages/qlib_adapter/selection.py",
        "runtimes/qlib/verify_native.py",
        "runtimes/qlib/verify_direct.py",
        "runtimes/qlib/verify_rd_runner.py",
        "runtimes/qlib/probe_container.py",
        "packages/factors/__init__.py",
        "packages/factors/contracts.py",
        "packages/research_runtime/__init__.py",
        "packages/research_runtime/control.py",
        "packages/research_runtime/budget_contracts.py",
    )
    adapter = {path: (root / path).read_bytes() for path in adapter_files}
    adapter_wheel = wheel(
        output,
        "mineagent_qlib_runner_adapter_profile",
        adapter,
        ["mineagent-qlib-runner-upstream-profile==0.1.0"],
    )
    native_files = {
        path: data
        for path, data in adapter.items()
        if path.startswith("packages/qlib_adapter/")
        or path
        in (
            "packages/factors/__init__.py",
            "packages/factors/contracts.py",
            "runtimes/qlib/verify_native.py",
            "runtimes/qlib/verify_direct.py",
        )
    }
    native_dependencies = ["pyqlib==0.9.8"] + [
        line.split()[0]
        for line in (profile / "requirements.lock").read_text().splitlines()
        if line and not line.startswith("#")
    ]
    native_wheel = wheel(
        output,
        "mineagent_qlib_native_adapter_profile",
        native_files,
        native_dependencies,
    )
    return adapter_wheel, native_wheel


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    profile = root / "runtimes/qlib"
    inventory = json.loads((profile / "rd-source-inventory.json").read_text())
    source_root = args.source.resolve(strict=True)
    head = subprocess.check_output(
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
    if (
        head != inventory["commit"]
        or head != "484776c211e4fbbeef03e0ec00d6bbee7362a4f4"
        or dirty
    ):
        raise ValueError("RD source commit/worktree drift")
    files = {}
    for path, expected in inventory["files"].items():
        relative = PurePosixPath(path)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not path.startswith("rdagent/")
        ):
            raise ValueError("Invalid RD inventory path")
        if path in SINGLE or path.startswith(PREFIXES) or path.endswith(".yaml"):
            source = args.source / path
            if source.is_symlink() or any(
                parent.is_symlink() for parent in source.parents
            ):
                raise ValueError("source symlink")
            data = source.read_bytes()
            if sha(data) != expected:
                raise ValueError(f"source drift: {path}")
            files[path] = data
    files[
        "mineagent_qlib_runner_upstream_profile-0.1.0.dist-info/licenses/LICENSE.rdagent"
    ] = (profile / "LICENSE.rdagent").read_bytes()
    dependencies = [
        line.split()[0]
        for line in (profile / "runner-requirements.lock").read_text().splitlines()
        if line and not line.startswith(("#", " "))
    ]
    args.output.mkdir(parents=True, exist_ok=True)
    upstream = wheel(
        args.output, "mineagent_qlib_runner_upstream_profile", files, dependencies
    )
    adapter_wheel, native_wheel = build_adapters(root, args.output)
    report = {
        "schema": "mineagent.qlib-runner-wheel-build.v1",
        "commit": inventory["commit"],
        "recipe_sha256": sha(Path(__file__).read_bytes()),
        "dependency_lock_sha256": sha(
            (profile / "runner-requirements.lock").read_bytes()
        ),
        "upstream": upstream,
        "adapter": adapter_wheel,
        "native_adapter": native_wheel,
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
