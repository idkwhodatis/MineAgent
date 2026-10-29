"""Bounded, closed projection of private OE/workflow browser failures; never raw output."""

import errno
import os
import re
import stat
from pathlib import Path
from xml.etree import ElementTree as ET

MAX_BYTES = 1_048_576
SOURCES = (
    "oe-normal-acceptance/normal.spec.ts",
    "e2e/helpers.ts",
    "workflow-history-acceptance/history.spec.ts",
    "workflow-history-acceptance/create.spec.ts",
    "workflow-history-acceptance/observation-checkpoint.spec.ts",
)


def browser_failure(path: Path, root: Path) -> dict:
    result = {"report": "unavailable", "failures": []}
    try:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError as error:
            if error.errno in {errno.ENOENT, errno.ELOOP}:
                return result
            raise
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_BYTES:
                return {**result, "report": "invalid"}
            chunks = []
            remaining = MAX_BYTES + 1
            while remaining:
                chunk = os.read(fd, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
        finally:
            os.close(fd)
        if (
            len(raw) > MAX_BYTES
            or b"\x00" in raw
            or b"<!DOCTYPE" in raw
            or b"<!ENTITY" in raw
        ):
            return {**result, "report": "invalid"}
        # Playwright emits UTF-8: do not let XML encoding autodetection admit
        # UTF-16/32 DTDs past the byte-level declaration rejection above.
        tree = ET.fromstring(raw.decode("utf-8", errors="strict"))
    except (OSError, ET.ParseError, ValueError):
        return {**result, "report": "invalid"}
    result["report"] = "parsed"
    for case in list(tree.iter("testcase"))[:8]:
        for failure in list(case)[:8]:
            if failure.tag not in {"failure", "error"}:
                continue
            text = failure.text or ""
            kind = "unknown"
            if failure.get("type") == "TimeoutError" or "Test timeout of " in text:
                kind = "timeout"
            elif re.search(r"(?:^|\n)\s*(?:Error: )?expect\(", text):
                kind = "assertion"
            frames = []
            for source in SOURCES:
                # Match only this checkout's exact known source, not arbitrary
                # external paths with the same basename or caller-provided names.
                absolute = str(root / "frontend/tests" / source)
                pattern = re.compile(
                    r"(?:^|\n)\s+at (?:[^\r\n()]{0,120} \()?"
                    + re.escape(absolute)
                    + r":([1-9][0-9]{0,3}):([1-9][0-9]{0,2})(?:\)|\s|$)"
                )
                for match in list(pattern.finditer(text))[:4]:
                    frames.append(
                        {
                            "file": Path(source).name,
                            "line": int(match[1]),
                            "column": int(match[2]),
                        }
                    )
            result["failures"].append({"kind": kind, "source_frames": frames[:4]})
            if len(result["failures"]) == 4:
                return result
    return result
