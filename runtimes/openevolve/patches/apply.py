"""Apply the closed, hash-guarded upstream patch series to a private build tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent
COMMIT = "8c65f74aa906abc0b0e9cae948ddf4c4c6570bdc"
ALLOWED = frozenset(
    {
        "openevolve/process_parallel.py",
        "openevolve/evaluator.py",
        "openevolve/controller.py",
        "openevolve/prompt/sampler.py",
    }
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def apply_unified(original: str, patch_lines: list[str]) -> str:
    """Strict unified hunks, no fuzzy matching or offset repair."""
    source = original.splitlines(keepends=True)
    output = []
    cursor = 0
    index = 0
    while index < len(patch_lines):
        header = re.fullmatch(
            r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@\n", patch_lines[index]
        )
        if header is None:
            raise ValueError("Unsupported unified hunk header")
        start = int(header[1]) - 1
        old_count = int(header[2] or 1)
        new_start = int(header[3]) - 1
        new_count = int(header[4] or 1)
        if start < cursor or start > len(source):
            raise ValueError("Overlapping or out-of-range hunk")
        output.extend(source[cursor:start])
        cursor = start
        if len(output) != new_start:
            raise ValueError("Patched hunk position mismatch")
        consumed = produced = 0
        index += 1
        while index < len(patch_lines) and not patch_lines[index].startswith("@@ "):
            line = patch_lines[index]
            if not line or line[0] not in " +-":
                raise ValueError("Unsupported unified hunk content")
            if line[0] in " -":
                if cursor >= len(source) or source[cursor] != line[1:]:
                    raise ValueError("Exact patch context mismatch")
                cursor += 1
                consumed += 1
            if line[0] in " +":
                output.append(line[1:])
                produced += 1
            index += 1
        if consumed != old_count or produced != new_count:
            raise ValueError("Hunk line count mismatch")
    output.extend(source[cursor:])
    return "".join(output)


def split_files(patch: str) -> dict[str, list[str]]:
    sections = {}
    lines = patch.splitlines(keepends=True)
    index = 0
    while index < len(lines):
        if not lines[index].startswith("--- a/") or index + 1 >= len(lines):
            raise ValueError("Unsupported patch file header")
        name = lines[index][6:].rstrip("\n")
        if (
            name not in ALLOWED
            or name in sections
            or lines[index + 1] != "+++ b/" + name + "\n"
        ):
            raise ValueError("Unexpected or duplicate patched path")
        index += 2
        start = index
        while index < len(lines) and not lines[index].startswith("--- a/"):
            index += 1
        sections[name] = lines[start:index]
    return sections


def apply(
    source: Path,
    *,
    control: bool = False,
    observer: bool = False,
    prompt: bool = False,
    frontier: bool = False,
    evaluation: bool = False,
) -> dict:
    if sum((control, observer, prompt, frontier, evaluation)) > 1:
        raise ValueError("Choose one patch series at a time")
    filename = (
        "evaluation-manifest.json"
        if evaluation
        else "frontier-manifest.json"
        if frontier
        else "prompt-manifest.json"
        if prompt
        else "observer-manifest.json"
        if observer
        else "control-manifest.json"
        if control
        else "manifest.json"
    )
    manifest = json.loads((ROOT / filename).read_text())
    if (
        manifest["schema"] != "mineagent.openevolve-patch-series.v1"
        or manifest["upstream_commit"] != COMMIT
    ):
        raise ValueError("Unsupported upstream patch series")
    if source.is_symlink():
        raise ValueError("Symlink build root is forbidden")
    updates = {}
    for item in manifest["patches"]:
        relative = PurePosixPath(item["path"])
        if len(relative.parts) != 1 or relative.suffix != ".patch":
            raise ValueError("Unexpected patch path")
        path = ROOT / item["path"]
        if path.is_symlink():
            raise ValueError("Symlink patch is forbidden")
        raw = path.read_bytes()
        if digest(raw) != item["sha256"]:
            raise ValueError("Patch hash mismatch")
        sections = split_files(raw.decode("utf-8"))
        if set(sections) != {f["path"] for f in item["files"]}:
            raise ValueError("Patch inventory mismatch")
        for entry in item["files"]:
            name = entry["path"]
            if name not in ALLOWED:
                raise ValueError("Unsupported patched file")
            target = source
            for part in PurePosixPath(name).parts:
                target = target / part
                if target.is_symlink():
                    raise ValueError("Symlink source is forbidden")
            old = updates.get(name, target.read_bytes())
            if digest(old) != entry["before_sha256"]:
                raise ValueError("Upstream source drift")
            new = apply_unified(old.decode("utf-8"), sections[name]).encode("utf-8")
            if digest(new) != entry["after_sha256"]:
                raise ValueError("Patched source hash mismatch")
            updates[name] = new
    # All hashes and hunks are validated before writing any file. This private
    # build tree must be discarded if filesystem writes subsequently fail.
    for name, data in updates.items():
        (source / name).write_bytes(data)
    return {
        "schema": manifest["schema"],
        "upstream_commit": COMMIT,
        "files": {n: digest(b) for n, b in updates.items()},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument(
        "--control",
        action="store_true",
        help="Apply partial control patch after base series",
    )
    parser.add_argument(
        "--observer",
        action="store_true",
        help="Apply observer seams after control series",
    )
    parser.add_argument(
        "--prompt",
        action="store_true",
        help="Apply prompt rendering seams after observer series",
    )
    parser.add_argument(
        "--frontier",
        action="store_true",
        help="Apply frontier seams after prompt series",
    )
    parser.add_argument(
        "--evaluation",
        action="store_true",
        help="Apply objective receipt seam after frontier series",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            apply(
                args.source,
                control=args.control,
                observer=args.observer,
                prompt=args.prompt,
                frontier=args.frontier,
                evaluation=args.evaluation,
            ),
            indent=2,
            sort_keys=True,
        )
    )
