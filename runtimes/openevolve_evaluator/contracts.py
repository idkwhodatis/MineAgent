"""Exact no-IPC native evaluator ABI; declarations never mint launch authority.

Preserves the unchanged TradeGrad native engine's observation qualification.
Process isolation and protected bytes do not make a strategy-shared interpreter
an independent or producer-proof financial ledger.
"""

import hashlib
import re
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.research_runtime.process_contracts import canonical
from packages.research_runtime.process_declarations_v2 import SHA, Closed

PROFILE = "mineagent.openevolve-native-evaluator.v1"
MAX_FILES = 256
MAX_MARKET_FILES = 32
MAX_INPUT_BYTES = 66 * 1024**2
MAX_MANIFEST_BYTES = 65536
MAX_TRANSCRIPT_BYTES = 8 * 1024**2
MAX_METRICS_BYTES = 1024**2
MAX_EXPORT_BYTES = 16 * 1024**2
MAX_WALL_SECONDS = 60
FIXED_FILES = {
    "strategy.py": 32768,
    "request.json": 1048576,
    "stage-report.json": 1048576,
}


def path_kind(path):
    if type(path) is not str:
        raise ValueError("Exact native staged path required")
    if path in FIXED_FILES:
        return "fixed"
    match = re.fullmatch(r"data/([A-Za-z0-9_][A-Za-z0-9_.-]{0,119})\.csv", path)
    if match is not None:
        return "market"
    match = re.fullmatch(
        r"universe/members_list_([0-9]{4}-[0-9]{2}-[0-9]{2})\.csv", path
    )
    if match is not None and date.fromisoformat(match[1]).isoformat() == match[1]:
        return "membership"
    raise ValueError("Unsupported native staged path")


class NativeInputFile(Closed):
    path: str = Field(min_length=1, max_length=160)
    sha256: SHA
    size_bytes: int = Field(gt=0, le=64 * 1024**2)

    @model_validator(mode="after")
    def bounded_file(self):
        path_kind(self.path)
        if self.path in FIXED_FILES and self.size_bytes > FIXED_FILES[self.path]:
            raise ValueError("Fixed native file exceeds bound")
        return self

    @property
    def destination(self):
        return "/input/" + self.path


class NativeInputManifest(Closed):
    schema_version: Literal["mineagent.openevolve-evaluator-input.v1"] = (
        "mineagent.openevolve-evaluator-input.v1"
    )
    evaluation_id: UUID
    execution_plan_sha256: SHA
    source_sha256: SHA
    window_start: date
    window_end: date
    files: tuple[NativeInputFile, ...] = Field(min_length=4, max_length=MAX_FILES)

    @model_validator(mode="after")
    def complete_inventory(self):
        names = tuple(f.path for f in self.files)
        if names != tuple(sorted(set(names))) or not set(FIXED_FILES) <= set(names):
            raise ValueError("Canonical complete native inventory required")
        market = [f for f in self.files if path_kind(f.path) == "market"]
        if (
            not 1 <= len(market) <= MAX_MARKET_FILES
            or sum(f.size_bytes for f in market) > 64 * 1024**2
        ):
            raise ValueError("Native market inventory exceeds bound")
        if (
            sum(f.size_bytes for f in self.files) > MAX_INPUT_BYTES
            or self.window_end <= self.window_start
        ):
            raise ValueError("Native total input/window exceeds bound")
        if (
            next(f.sha256 for f in self.files if f.path == "strategy.py")
            != self.source_sha256
        ):
            raise ValueError("Native strategy bytes differ from source binding")
        if len(canonical(self.model_dump(mode="json"))) > MAX_MANIFEST_BYTES:
            raise ValueError("Native manifest exceeds fixed control bound")
        return self

    @property
    def native_artifacts_sha256(self):
        # Exact unchanged evaluator artifact_inventory grammar, deliberately
        # distinct from the scoped manifest envelope commitment below.
        return hashlib.sha256(
            canonical(
                [
                    {"path": item.path, "sha256": item.sha256, "bytes": item.size_bytes}
                    for item in self.files
                ]
            )
        ).hexdigest()

    @property
    def sha256(self):
        return hashlib.sha256(canonical(self.model_dump(mode="json"))).hexdigest()


def parse_manifest(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_MANIFEST_BYTES:
        raise ValueError("Native manifest exceeds byte bound")
    value = NativeInputManifest.model_validate_json(raw)
    if canonical(value.model_dump(mode="json")) != raw:
        raise ValueError("Native manifest is not canonical")
    return value


def check_input_bytes(manifest, contents):
    manifest = NativeInputManifest.model_validate(manifest)
    expected = {f.path: f for f in manifest.files}
    if type(contents) is not dict or set(contents) != set(expected):
        raise ValueError("Exact native byte inventory required")
    for path, raw in contents.items():
        entry = expected[path]
        if (
            type(raw) is not bytes
            or len(raw) != entry.size_bytes
            or hashlib.sha256(raw).hexdigest() != entry.sha256
        ):
            raise ValueError("Native staged file identity differs")
    return manifest
