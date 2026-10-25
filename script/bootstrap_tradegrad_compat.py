#!/usr/bin/env python3
"""Materialize verified external legacy files, without executing dependency code.

The original TradeGrad source is not relicensed or redistributed as MineAgent MIT
code. This explicit bootstrap obtains the frozen compatibility dependency from
the user's existing public fork. Never stage these ignored files with ``git -f``.

Descriptor-relative no-follow primitives are mandatory. Complete verified files
are published without replacing existing leaves. No destination is deleted on
failure. Private staging hardlinks remain beneath .runtime/compatibility-bootstrap
because pathname cleanup cannot atomically establish inode ownership during a
concurrent replacement. An identical repeat creates no further staging files.
Final inode/content checks detect concurrent replacement; they do not make an
arbitrarily mutable checkout permanently immutable after validation returns.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import tempfile
import uuid
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


# Capture primitive identities before tests/instrumentation wrap their calls.
_DIRFD_REQUIRED = (os.open, os.mkdir, os.stat, os.link)
_LINK_PRIMITIVE = os.link


def require_safe_primitives() -> None:
    if (
        not all(
            hasattr(os, flag) for flag in ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")
        )
        or not all(fn in os.supports_dir_fd for fn in _DIRFD_REQUIRED)
        or _LINK_PRIMITIVE not in os.supports_follow_symlinks
    ):
        raise BootstrapError(
            "Safe descriptor-relative no-follow primitives are required"
        )


def _directory(
    parent: int, name: str, *, create: bool = False, mode: int = 0o755
) -> int:
    if create:
        try:
            os.mkdir(name, mode=mode, dir_fd=parent)
        except FileExistsError:
            pass
    try:
        return os.open(
            name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
        )
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise BootstrapError("Symlink or non-directory destination refused") from exc


def _root_directory(root: Path) -> int:
    # Anchor every component, not just the leaf of an absolute path.
    fd = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in root.parts[1:]:
            child = _directory(fd, component)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _parent_directory(root_fd: int, relative: str, *, create: bool = False) -> int:
    if relative not in ALLOWED:
        raise BootstrapError("Path is outside the compatibility allowlist")
    fd = os.dup(root_fd)
    try:
        for component in Path(relative).parts[:-1]:
            child = _directory(fd, component, create=create)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _identity(fd: int) -> tuple[int, int]:
    info = os.fstat(fd)
    return info.st_dev, info.st_ino


def _read_exact(parent: int, name: str, body: bytes) -> tuple[int, int] | None:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise BootstrapError(
            "Symlink or unreadable dependency destination refused"
        ) from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise BootstrapError("Non-file dependency destination refused")
        chunks = []
        remaining = len(body) + 1
        while remaining:
            chunk = os.read(fd, min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        if b"".join(chunks) != body:
            raise BootstrapError(f"Refusing differing local file content: {name}")
        return _identity(fd)
    finally:
        os.close(fd)


def _staging_directory(root_fd: int) -> int:
    runtime = _directory(root_fd, ".runtime", create=True, mode=0o700)
    try:
        cache = _directory(runtime, "compatibility-bootstrap", create=True, mode=0o700)
        try:
            name = uuid.uuid4().hex
            # Unique ownership established by exclusive mkdir; never adopt a collision.
            os.mkdir(name, mode=0o700, dir_fd=cache)
            return _directory(cache, name)
        finally:
            os.close(cache)
    finally:
        os.close(runtime)


def _publish(staging: int, parent: int, name: str, body: bytes) -> tuple[int, int]:
    temporary_name = uuid.uuid4().hex
    fd = os.open(
        temporary_name,
        os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=staging,
    )
    try:
        with os.fdopen(os.dup(fd), "wb") as stream:
            stream.write(body)
            stream.flush()
            # Public source files must remain readable by non-root Docker users.
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        identity = _identity(fd)
        if _read_exact(staging, temporary_name, body) != identity:
            raise BootstrapError("Staged dependency identity changed")
        # A complete verified file becomes visible without ever clobbering a leaf.
        os.link(
            temporary_name,
            name,
            src_dir_fd=staging,
            dst_dir_fd=parent,
            follow_symlinks=False,
        )
        if _read_exact(parent, name, body) != identity:
            raise BootstrapError("Published dependency identity changed")
        os.fsync(parent)
        return identity
    finally:
        os.close(fd)
        # Deliberately no unlink by pathname: even stat/inode-check + unlink races
        # with a concurrent replacement. Private staging hardlinks are retained.


def _verify_final(
    root: Path, root_fd: int, payloads: dict[str, bytes], identities: dict
) -> None:
    current_root = _root_directory(root)
    try:
        if _identity(current_root) != _identity(root_fd):
            raise BootstrapError("Repository directory identity changed")
        for relative, body in payloads.items():
            parent = _parent_directory(current_root, relative)
            try:
                if (
                    _read_exact(parent, Path(relative).name, body)
                    != identities[relative]
                ):
                    raise BootstrapError(
                        f"Final dependency identity changed: {relative}"
                    )
                reopened = _parent_directory(current_root, relative)
                try:
                    if _identity(reopened) != _identity(parent):
                        raise BootstrapError("Final parent directory identity changed")
                finally:
                    os.close(reopened)
            finally:
                os.close(parent)
        reopened_root = _root_directory(root)
        try:
            if _identity(reopened_root) != _identity(root_fd):
                raise BootstrapError("Repository directory identity changed")
        finally:
            os.close(reopened_root)
    finally:
        os.close(current_root)


def materialize(root: Path, payloads: dict[str, bytes]) -> int:
    require_safe_primitives()
    root = root.resolve(strict=True)
    if (
        Path(git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve()
        != root
    ):
        raise BootstrapError("Destination must be the repository root")
    if set(payloads) != ALLOWED:
        raise BootstrapError("Incomplete compatibility payload")
    root_fd = _root_directory(root)
    staging = None
    try:
        # Validate every pre-existing path before creating any dependency file.
        for relative, body in payloads.items():
            safe_target(root, relative)  # Useful error text, not the safety boundary.
            if git(root, "ls-files", "--", relative).strip():
                raise BootstrapError(
                    f"Refusing tracked dependency destination: {relative}"
                )
            if not git(root, "check-ignore", "--", relative).strip():
                raise BootstrapError(
                    f"Dependency destination must be Git-ignored: {relative}"
                )
            try:
                parent = _parent_directory(root_fd, relative)
            except FileNotFoundError:
                continue
            try:
                _read_exact(parent, Path(relative).name, body)
            finally:
                os.close(parent)
        identities = {}
        created = 0
        for relative, body in payloads.items():
            parent = _parent_directory(root_fd, relative, create=True)
            try:
                name = Path(relative).name
                identity = _read_exact(parent, name, body)
                if identity is None:
                    if staging is None:
                        staging = _staging_directory(root_fd)
                    identity = _publish(staging, parent, name, body)
                    created += 1
                identities[relative] = identity
            finally:
                os.close(parent)
        _verify_final(root, root_fd, payloads, identities)
        return created
    finally:
        # Never delete final names, including replacements owned by another actor.
        # Complete published files remain available for an idempotent retry.
        if staging is not None:
            os.close(staging)
        os.close(root_fd)


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
