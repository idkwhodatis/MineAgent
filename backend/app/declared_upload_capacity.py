"""Conservative accounting of every dataset byte, including uncommitted orphans.

At schema030 all factor publishers include this conservative inventory.
Existing uploads count against factor admission; their separate ingestion
quota is unchanged. No manifest or committed row exempts dataset bytes.
"""

import os
import stat
import time
from uuid import UUID


def dataset_physical_usage(root_fd, owner_id, deadline, maximum_files):
    total = owned = count = 0

    def identity(info):
        return (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_nlink,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )

    def scan(fd, parts):
        nonlocal total, owned, count
        if time.monotonic() >= deadline:
            raise ValueError("Dataset inventory deadline")
        before = os.fstat(fd)
        names = os.listdir(fd)
        if len(names) + count > maximum_files or len(parts) > 10:
            raise ValueError("Dataset inventory bound")
        for name in names:
            if time.monotonic() >= deadline:
                raise ValueError("Dataset inventory deadline")
            count += 1
            if count > maximum_files or not name or name in {".", ".."}:
                raise ValueError("Dataset inventory bound")
            if len(parts) < 2 and str(UUID(name)) != name:
                raise ValueError("Canonical dataset namespace required")
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(
                    name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                )
                try:
                    if identity(info) != identity(os.fstat(child)):
                        raise ValueError("Dataset directory changed")
                    scan(child, (*parts, name))
                    if identity(info) != identity(os.fstat(child)):
                        raise ValueError("Dataset directory changed")
                finally:
                    os.close(child)
            elif len(parts) >= 2 and stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                total += info.st_size
                if parts[0] == str(owner_id):
                    owned += info.st_size
            else:
                raise ValueError("Dataset inventory requires singleton regular files")
            if identity(info) != identity(
                os.stat(name, dir_fd=fd, follow_symlinks=False)
            ):
                raise ValueError("Dataset inventory changed")
        if identity(before) != identity(os.fstat(fd)):
            raise ValueError("Dataset inventory changed")

    try:
        fd = os.open(
            "datasets", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd
        )
    except FileNotFoundError:
        return 0, 0, 0
    try:
        original = os.fstat(fd)
        scan(fd, ())
        if identity(original) != identity(
            os.stat("datasets", dir_fd=root_fd, follow_symlinks=False)
        ):
            raise ValueError("Dataset root changed")
        return total, owned, count
    finally:
        os.close(fd)
