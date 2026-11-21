"""Local OpenEvolve release qualification only; never imported by a worker."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent
BASELINE = ROOT.parent / "openevolve"
TOP_LEVEL = frozenset({"LICENSE", "MANIFEST.in", "README.md", "pyproject.toml"})
SOURCE_LIMIT = 4 * 1024 * 1024
PATH = re.compile(r"openevolve(?:/[a-zA-Z0-9_.-]+)+")
HEX = re.compile(r"[0-9a-f]{40}")


def manifest():
    return json.loads((ROOT / "release.json").read_text())


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def verify_locks(meta):
    for name, expected in meta["locks"].items():
        if digest((BASELINE / name).read_bytes()) != expected:
            raise ValueError("Candidate dependency lock differs from reviewed input")


def git(source, *args):
    # Read committed objects only. Do not apply archive filters, run hooks or
    # trust untracked/editable working-tree files as release inputs.
    return subprocess.run(
        ["git", "--no-replace-objects", "-C", str(source), *args],
        check=True,
        capture_output=True,
        timeout=30,
    ).stdout


def inventory(source, meta):
    commit, tree = git(source, "rev-parse", "HEAD", "HEAD^{tree}").decode().splitlines()
    if (commit, tree) != (meta["commit"], meta["tree"]):
        raise ValueError("Candidate source commit/tree differs")
    if (
        int(git(source, "show", "-s", "--format=%ct", "HEAD"))
        != meta["source_date_epoch"]
    ):
        raise ValueError("Candidate release timestamp differs")
    listing = git(
        source, "ls-tree", "-r", "-z", "HEAD", "--", *sorted(TOP_LEVEL), "openevolve"
    ).split(b"\0")
    files = {}
    size = 0
    for entry in listing[:-1]:
        header, raw_name = entry.decode("ascii").split("\t", 1)
        mode, kind, blob = header.split(" ")
        name = Path(raw_name)
        if (
            mode != "100644"
            or kind != "blob"
            or HEX.fullmatch(blob) is None
            or raw_name in files
            or ".." in name.parts
            or "." in raw_name.split("/")
            or (raw_name not in TOP_LEVEL and PATH.fullmatch(raw_name) is None)
        ):
            raise ValueError(
                "Candidate contains a non-regular or unexpected build input"
            )
        raw_size = int(git(source, "cat-file", "-s", blob))
        size += raw_size
        if size > SOURCE_LIMIT or len(files) >= meta["files"]:
            raise ValueError("Candidate source exceeds reviewed bounds")
        raw = git(source, "cat-file", "blob", blob)
        if len(raw) != raw_size:
            raise ValueError("Candidate blob size differs")
        files[raw_name] = raw
    encoded_inventory = "".join(
        f"{name}:{len(raw)}:{digest(raw)}\n" for name, raw in sorted(files.items())
    ).encode("ascii")
    if (
        len(files) != meta["files"]
        or not TOP_LEVEL <= files.keys()
        or digest(encoded_inventory) != meta["inventory_sha256"]
    ):
        raise ValueError("Candidate source inventory differs")
    return files


def materialize(source, destination, meta):
    verify_locks(meta)
    files = inventory(source, meta)
    destination.mkdir(mode=0o700)
    epoch = meta["source_date_epoch"]
    for name, raw in files.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(raw)
        target.chmod(0o644)
        os.utime(target, (epoch, epoch))


def verify_wheel(path, meta):
    if (
        path.name != meta["wheel"]["filename"]
        or path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > SOURCE_LIMIT
        or digest(path.read_bytes()) != meta["wheel"]["sha256"]
    ):
        raise ValueError("Candidate wheel differs from reviewed build")


def require(condition, error):
    if not condition:
        raise ValueError(error)


def rejected(action, reason):
    try:
        action()
    except ValueError as exc:
        require(reason in str(exc), "Unexpected configuration rejection")
    else:
        raise ValueError("Unexpected configuration acceptance")


def verify_installed(wheel, meta):
    verify_locks(meta)
    verify_wheel(wheel, meta)
    require(sys.flags.isolated, "Installed candidate proof requires Python -I")
    require(
        sys.version.split()[0] == meta["python"], "Candidate Python version differs"
    )
    expected = {
        **json.loads((BASELINE / "runtime-manifest.json").read_text())["dependencies"],
        "openevolve": meta["version"],
    }
    observed = {
        item.metadata["Name"].lower().replace("_", "-"): item.version
        for item in importlib.metadata.distributions()
        if item.metadata["Name"].lower() != "pip"
    }
    require(observed == expected, "Candidate package inventory differs")
    installed = importlib.metadata.distribution("openevolve")
    with ZipFile(wheel) as archive:
        for name in archive.namelist():
            if name.startswith("openevolve/"):
                path = installed.locate_file(name)
                require(
                    not path.is_symlink() and path.read_bytes() == archive.read(name),
                    "Installed candidate package bytes differ from reviewed wheel",
                )
    attempts = []

    def deny(*_args, **_kwargs):
        attempts.append(True)
        raise ValueError("No network connection permitted in release smoke")

    with (
        patch.object(socket.socket, "connect", deny),
        patch.object(socket.socket, "connect_ex", deny),
        tempfile.TemporaryDirectory(prefix="mineagent-oe-release-") as temporary,
    ):
        from openevolve import OpenEvolve
        from openevolve.config import Config
        from openevolve.database import Program
        from openevolve.process_parallel import _choose_evolution_mode

        # Match the relevant current structured semantics without launching an
        # evaluator or accepting user-configurable execution/provenance inputs.
        structured = Config(diff_based_evolution=False)
        structured.validate_evolution_mode()
        require(
            structured.full_rewrite_probability == 0.0
            and structured.checkpoint_on_improvement is False
            and structured.database.island_initial_programs == []
            and structured.evolution_trace.include_failures is False
            and _choose_evolution_mode(structured, 1) == "full_rewrite",
            "Candidate defaults change the structured boundary",
        )
        invalid = Config(diff_based_evolution=False, full_rewrite_probability=0.5)
        root = Path(temporary)
        output = root / "output"
        rejected(
            lambda: OpenEvolve(
                str(root / "seed"), str(root / "evaluator"), invalid, str(output)
            ),
            "full_rewrite_probability only applies",
        )
        require(
            not output.exists(), "Controller touched output before rejecting config"
        )
        rejected(
            lambda: Config.from_dict({"llm": {"model": "unsupported"}}),
            "llm.model",
        )
        mixed = Config(
            diff_based_evolution=True, full_rewrite_probability=0.5, random_seed=42
        )
        mixed.validate_evolution_mode()
        modes = [_choose_evolution_mode(mixed, k) for k in range(1, 33)]
        require(
            set(modes) == {"full_rewrite", "diff"}
            and modes == [_choose_evolution_mode(mixed, k) for k in range(1, 33)],
            "Upstream mixed mode is not seeded and repeatable",
        )
        # The newer fast serializer must not alias the plain nested state used
        # by MineAgent. This is not a checkpoint or rebase compatibility claim.
        program = Program(id="probe", code="{}", metadata={"nested": [1, {"ok": True}]})
        record = program.to_dict()
        require(record == asdict(program), "Optimized Program serialization differs")
        record["metadata"]["nested"][1]["ok"] = False
        require(
            program.metadata["nested"][1]["ok"], "Program snapshot aliased live state"
        )
    require(not attempts, "Candidate attempted a socket connection")
    return {
        "version": meta["version"],
        "upstream_commit": meta["commit"],
        "closed_default_mode": "full_rewrite",
        "programmatic_invalid_config_rejected_before_output": True,
        "invalid_llm_model_rejected": True,
        "seeded_mixed_modes_observed_only_in_fixture": True,
        "program_serialization_isolated": True,
        "python_socket_attempts": len(attempts),
        "production_activation": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    source = commands.add_parser("materialize")
    source.add_argument("source", type=Path)
    source.add_argument("destination", type=Path)
    wheel = commands.add_parser("verify-wheel")
    wheel.add_argument("path", type=Path)
    installed = commands.add_parser("verify-installed")
    installed.add_argument("wheel", type=Path)
    args = parser.parse_args()
    meta = manifest()
    if args.command == "materialize":
        materialize(args.source, args.destination, meta)
    elif args.command == "verify-wheel":
        verify_wheel(args.path, meta)
    else:
        print(json.dumps(verify_installed(args.wheel, meta), sort_keys=True))


if __name__ == "__main__":
    main()
