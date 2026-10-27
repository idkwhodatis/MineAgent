"""Host retained staging inventory, no launch grant and no arbitrary recursive mount."""

import hashlib
import os
import stat
from pathlib import Path

from runtimes.openevolve_evaluator.contracts import (
    MAX_FILES,
    MAX_INPUT_BYTES,
    NativeInputFile,
    NativeInputManifest,
    path_kind,
)
from runtimes.research_supervisor.supervisor import _directory


def inventory_staged_input(root, *, evaluation_id, plan, source_sha256, window):
    if window not in plan.windows:
        raise ValueError("window outside immutable execution plan")
    root = Path(root).absolute()
    parent = _directory(root)
    result = []
    total = 0

    def walk(fd, prefix=""):
        nonlocal total
        before = os.fstat(fd)
        for leaf in sorted(os.listdir(fd)):
            path = prefix + leaf
            info = os.stat(leaf, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                if prefix or leaf not in {"data", "universe"}:
                    raise ValueError("undeclared native input directory")
                child = os.open(
                    leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                )
                try:
                    walk(child, leaf + "/")
                finally:
                    os.close(child)
                continue
            path_kind(path)
            file = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            try:
                first = os.fstat(file)
                if (
                    not stat.S_ISREG(first.st_mode)
                    or first.st_nlink != 1
                    or stat.S_IMODE(first.st_mode) != 0o444
                    or not 0 < first.st_size <= 64 * 1024**2
                ):
                    raise ValueError("native staging file identity")
                digest = hashlib.sha256()
                count = 0
                while raw := os.read(file, 65536):
                    count += len(raw)
                    if count > first.st_size:
                        raise ValueError("native staging growth")
                    digest.update(raw)

                def signature(value):
                    return (
                        value.st_dev,
                        value.st_ino,
                        value.st_mode,
                        value.st_size,
                        value.st_nlink,
                        value.st_mtime_ns,
                        value.st_ctime_ns,
                    )

                if (
                    count != first.st_size
                    or signature(first) != signature(os.fstat(file))
                    or signature(first)
                    != signature(os.stat(leaf, dir_fd=fd, follow_symlinks=False))
                ):
                    raise ValueError("native staging replaced")
                total += count
                result.append(
                    NativeInputFile(
                        path=path, sha256=digest.hexdigest(), size_bytes=count
                    )
                )
                if len(result) > MAX_FILES or total > MAX_INPUT_BYTES:
                    raise ValueError("native staged inventory bound")
            finally:
                os.close(file)
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError("native staged directory changed")

    try:
        walk(parent)
    finally:
        os.close(parent)
    return NativeInputManifest(
        evaluation_id=evaluation_id,
        execution_plan_sha256=plan.sha256
        if hasattr(plan, "sha256")
        else _plan_sha(plan),
        source_sha256=source_sha256,
        window_start=window.start,
        window_end=window.end,
        files=tuple(sorted(result, key=lambda f: f.path)),
    )


def _plan_sha(plan):
    from packages.research_runtime.broker_contracts import digest

    return digest(plan)
