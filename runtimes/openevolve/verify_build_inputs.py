"""Check prepared upstream build bytes before any build backend can execute."""

import argparse
import hashlib
import json
from pathlib import Path


def verify(source: Path) -> None:
    root = Path(__file__).resolve().parent
    manifest = json.loads((root / "upstream-source.json").read_text())
    expected = {item["path"]: item for item in manifest["files"]}
    actual = set()
    for path in source.rglob("*"):
        if path.is_symlink():
            raise ValueError("Symlink build input is forbidden")
        if path.is_file():
            name = path.relative_to(source).as_posix()
            actual.add(name)
            if name not in expected:
                raise ValueError("Unexpected prepared build input")
            info = expected[name]
            data = path.read_bytes()
            if (
                len(data) != info["size_bytes"]
                or hashlib.sha256(data).hexdigest() != info["sha256"]
            ):
                raise ValueError("Prepared build input changed")
    if actual != set(expected):
        raise ValueError("Missing prepared build inputs")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    verify(parser.parse_args().source)
