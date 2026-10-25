#!/usr/bin/env python3
"""Materialize verified external legacy files, without executing dependency code.

The original TradeGrad source is not relicensed or redistributed as MineAgent MIT
code. This explicit bootstrap obtains the frozen compatibility dependency from
the user's existing public fork. Never stage these ignored files with ``git -f``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

REPOSITORY = "https://github.com/idkwhodatis/TradeGrad.git"
COMMIT = "6828f3bd429fc754648021bae703a14fa300b62e"
TREE = "add06334d6cbcd9e5f359f2cb4a91f6902fed589"
ALLOWED = frozenset(
    {
        "requirements.txt",
        "script/eval.sh",
        "script/train.sh",
        "seed_strategies/cn_cross.py",
        "seed_strategies/cn_time.py",
        "seed_strategies/us_cross.py",
        "seed_strategies/us_time.py",
        "src/__init__.py",
        "src/eval_runs.py",
        "src/evaluate.py",
        "src/evaluate_time.py",
        "src/job.py",
        "src/llm_client.py",
        "src/loss.py",
        "src/market.py",
        "src/optimizer.py",
        "src/prompt.py",
        "src/run_one.py",
        "src/strategy.py",
        "src/tasks.py",
        "src/util.py",
    }
)


class BootstrapError(RuntimeError):
    """Dependency could not be validated or safely materialized."""


def git(root: Path, *args: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            timeout=180,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        ).stdout
    except (subprocess.SubprocessError, OSError) as exc:
        raise BootstrapError(
            "Git dependency operation failed; no credentials requested"
        ) from exc


def load_manifest(path: Path) -> dict:
    if path.is_symlink():
        raise BootstrapError("Manifest must not be a symlink")
    data = json.loads(path.read_text())
    if (
        data.get("schema"),
        data.get("repository"),
        data.get("commit"),
        data.get("tree"),
    ) != (
        "mineagent.tradegrad-compat.v1",
        REPOSITORY,
        COMMIT,
        TREE,
    ):
        raise BootstrapError("Unrecognized compatibility source identity")
    files = data.get("files", [])
    if len(files) != len(ALLOWED) or {f.get("path") for f in files} != ALLOWED:
        raise BootstrapError("Compatibility paths must match the explicit allowlist")
    for entry in files:
        if (
            not isinstance(entry.get("bytes"), int)
            or not 0 <= entry["bytes"] <= 2_000_000
        ):
            raise BootstrapError("Invalid bounded file size")
        for key, length in (("sha256", 64), ("git_blob_sha1", 40)):
            value = entry.get(key)
            if (
                not isinstance(value, str)
                or len(value) != length
                or any(c not in "0123456789abcdef" for c in value)
            ):
                raise BootstrapError("Invalid content digest")
    return data


def read_payloads(source: Path, ref: str, manifest: dict) -> dict[str, bytes]:
    if git(source, "rev-parse", f"{ref}^{{tree}}").decode().strip() != TREE:
        raise BootstrapError("Source tree does not match the accepted baseline")
    payloads = {}
    for entry in manifest["files"]:
        path = entry["path"]
        # Read the immutable Git blob, never a dirty source working-tree file.
        mode = git(source, "ls-tree", ref, "--", path).decode().split()[0]
        if mode not in {"100644", "100755"}:
            raise BootstrapError("Dependency is not a regular Git file")
        body = git(source, "show", f"{ref}:{path}")
        blob = hashlib.sha1(
            b"blob " + str(len(body)).encode() + b"\0" + body
        ).hexdigest()
        if (len(body), hashlib.sha256(body).hexdigest(), blob) != (
            entry["bytes"],
            entry["sha256"],
            entry["git_blob_sha1"],
        ):
            raise BootstrapError(f"Dependency digest mismatch: {path}")
        payloads[path] = body
    return payloads


def safe_target(root: Path, relative: str) -> Path:
    if relative not in ALLOWED:
        raise BootstrapError("Path is outside the compatibility allowlist")
    target = root / relative
    cursor = target
    while cursor != root:
        if cursor.is_symlink():
            raise BootstrapError(f"Symlink destination refused: {relative}")
        cursor = cursor.parent
    if target.exists() and not target.is_file():
        raise BootstrapError(f"Non-file destination refused: {relative}")
    return target


def materialize(root: Path, payloads: dict[str, bytes]) -> int:
    root = root.resolve(strict=True)
    if (
        Path(git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve()
        != root
    ):
        raise BootstrapError("Destination must be the repository root")
    if set(payloads) != ALLOWED:
        raise BootstrapError("Incomplete compatibility payload")
    for relative, body in payloads.items():
        path = safe_target(root, relative)
        if git(root, "ls-files", "--", relative).strip():
            raise BootstrapError(f"Refusing tracked dependency destination: {relative}")
        if not git(root, "check-ignore", "--", relative).strip():
            raise BootstrapError(
                f"Dependency destination must be Git-ignored: {relative}"
            )
        if path.exists() and path.read_bytes() != body:
            raise BootstrapError(
                f"Refusing to overwrite differing local file: {relative}"
            )
    created = []
    try:
        for relative, body in payloads.items():
            path = safe_target(root, relative)
            if path.exists():
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            # Recheck parents immediately before creation and never follow leaf symlinks.
            safe_target(root, relative)
            fd = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o644,
            )
            created.append(path)
            with os.fdopen(fd, "wb") as stream:
                stream.write(body)
        return len(created)
    except BaseException:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise


def bootstrap(root: Path, source_dir: Path | None = None) -> int:
    manifest = load_manifest(root / "compatibility/tradegrad.json")
    if source_dir is not None:
        payloads = read_payloads(source_dir, "HEAD", manifest)
    else:
        with tempfile.TemporaryDirectory(prefix="mineagent-compat-") as temporary:
            source = Path(temporary)
            git(source, "init", "--bare", "--quiet")
            git(
                source,
                "-c",
                "credential.helper=",
                "fetch",
                "--quiet",
                "--depth=1",
                REPOSITORY,
                COMMIT,
            )
            if git(source, "rev-parse", "FETCH_HEAD").decode().strip() != COMMIT:
                raise BootstrapError(
                    "Fetched commit does not match the pinned revision"
                )
            payloads = read_payloads(source, "FETCH_HEAD", manifest)
    return materialize(root, payloads)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        help="Use a local Git tree with the exact pinned tree; dirty files are ignored",
    )
    args = parser.parse_args()
    try:
        count = bootstrap(args.root, args.source_dir)
    except (BootstrapError, ValueError, OSError) as exc:
        parser.exit(1, f"Compatibility bootstrap refused: {exc}\n")
    print(
        f"Verified {len(ALLOWED)} pinned compatibility files; materialized {count}. No dependency code executed."
    )


if __name__ == "__main__":
    main()
