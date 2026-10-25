"""Byte-exact source grammar; parsing is not execution or parity verification."""

import ast
import hashlib
import io
import tokenize
from dataclasses import dataclass

BEGIN = "# EVOLVE-BLOCK-START"
END = "# EVOLVE-BLOCK-END"
MAX_SOURCE_BYTES = 131072


@dataclass(frozen=True)
class SourceRegion:
    source_sha256: str
    begin_line: int
    end_line: int
    prefix: bytes
    suffix: bytes


def canonical_source(source: bytes) -> str:
    if type(source) is not bytes or not source or len(source) > MAX_SOURCE_BYTES:
        raise ValueError("Source byte bound")
    if source.startswith(b"\xef\xbb\xbf") or b"\r" in source or b"\0" in source:
        raise ValueError("Source requires explicit UTF-8 LF normalization")
    try:
        decoded = source.decode("utf-8", errors="strict")
        encoding, _ = tokenize.detect_encoding(io.BytesIO(source).readline)
    except (UnicodeError, SyntaxError):
        raise ValueError("Source encoding rejected") from None
    if encoding != "utf-8":
        raise ValueError("Source encoding declaration is not UTF-8")
    return decoded


def region(source: bytes) -> SourceRegion:
    decoded = canonical_source(source)
    lines = decoded.splitlines(keepends=True)
    starts, ends = [], []
    try:
        for token in tokenize.generate_tokens(io.StringIO(decoded).readline):
            if token.type == tokenize.COMMENT and token.string in (BEGIN, END):
                line = token.start[0]
                if lines[line - 1].strip() != token.string:
                    raise ValueError("Mutation markers must be standalone comments")
                (starts if token.string == BEGIN else ends).append(line)
    except (tokenize.TokenError, IndentationError):
        raise ValueError("Source tokenization failed") from None
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise ValueError("Exactly one ordered mutation region required")
    first, last = starts[0], ends[0]
    return SourceRegion(
        hashlib.sha256(source).hexdigest(),
        first,
        last,
        "".join(lines[:first]).encode(),
        "".join(lines[last - 1 :]).encode(),
    )


def validate_descendant(source: bytes, prepared_seed: bytes) -> SourceRegion:
    baseline, child = region(prepared_seed), region(source)
    if baseline.prefix != child.prefix or baseline.suffix != child.suffix:
        raise ValueError("Protected source changed")
    try:
        ast.parse(source)
    except SyntaxError:
        raise ValueError("Effective candidate syntax invalid") from None
    return child


def prepare_marker_region(source: bytes, *, first_line: int, last_line: int) -> bytes:
    """Insert comments at explicit whole-line boundaries; actual parity still required."""
    decoded = canonical_source(source)
    lines = decoded.splitlines(keepends=True)
    if (
        type(first_line) is not int
        or type(last_line) is not int
        or not 1 <= first_line <= last_line <= len(lines)
    ):
        raise ValueError("Invalid explicit preparation span")
    if BEGIN in decoded or END in decoded:
        raise ValueError("Existing or ambiguous marker text")
    # Do not silently append a newline to the original final line.
    if not lines[last_line - 1].endswith("\n"):
        raise ValueError("Preparation requires a line-terminated selected region")
    original_ast = ast.dump(ast.parse(decoded), include_attributes=False)
    marked = (
        "".join(lines[: first_line - 1])
        + BEGIN
        + "\n"
        + "".join(lines[first_line - 1 : last_line])
        + END
        + "\n"
        + "".join(lines[last_line:])
    ).encode()
    region(marked)
    if ast.dump(ast.parse(marked), include_attributes=False) != original_ast:
        raise ValueError("Marker preparation changed parsed program")
    return marked


def verify_controller_source(actual: str, admitted: bytes) -> None:
    canonical_source(admitted)
    if type(actual) is not str or actual.encode("utf-8") != admitted:
        raise ValueError("Upstream loaded source differs from admitted bytes")
