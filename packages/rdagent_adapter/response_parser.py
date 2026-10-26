"""Bounded pinned-upstream response parsing, never execution or bearer authority.

JSONParser is copied byte-for-byte from Microsoft RD-Agent under the MIT license.
Copyright (c) Microsoft Corporation. See runtimes/rdagent/LICENSE.rdagent.
The operational policy fixes reasoning_think_rm=False: no think text is removed.
Duplicate JSON keys and trailing first-object extraction retain upstream semantics.
"""

# ruff: noqa: UP006, UP035
from __future__ import annotations

import hashlib
import io
import json
import re
import tokenize
from dataclasses import dataclass
from typing import Callable, List

from pydantic import TypeAdapter, ValidationError

from packages.factors.contracts import source_sha256
from packages.research_runtime.engine_broker_contracts import RDChatResponse

UPSTREAM_COMMIT = "484776c211e4fbbeef03e0ec00d6bbee7362a4f4"
UPSTREAM_FRAGMENT_SHA256 = (
    "a5a6a4a397f86b71caac2b1e056d200411c9d6254dff5e81d84aad8c19d1a583"
)
MAX_RESPONSES = 6
MAX_RAW_CHARS = MAX_RESPONSES * 32768
MAX_RAW_BYTES = MAX_RAW_CHARS * 4
MAX_NORMALIZED_BYTES = MAX_RAW_CHARS * 12


# fmt: off
# BEGIN BYTE-EXACT UPSTREAM JSONParser
class JSONParser:
    """JSON parser supporting multiple strategies"""

    def __init__(self, add_json_in_prompt: bool = False) -> None:
        self.strategies: List[Callable[[str], str]] = [
            self._direct_parse,
            self._extract_from_code_block,
            self._fix_python_syntax,
            self._extract_with_fix_combined,
        ]
        self.add_json_in_prompt = add_json_in_prompt

    def parse(self, content: str) -> str:
        """Parse JSON content, automatically trying multiple strategies"""
        original_content = content

        for strategy in self.strategies:
            try:
                return strategy(original_content)
            except json.JSONDecodeError:
                continue

        # All strategies failed
        if not self.add_json_in_prompt:
            error = json.JSONDecodeError(
                "Failed to parse JSON after all attempts, maybe because 'messages' must contain the word 'json' in some form",
                original_content,
                0,
            )
            error.message = "Failed to parse JSON after all attempts, maybe because 'messages' must contain the word 'json' in some form"  # type: ignore[attr-defined]
            raise error
        else:
            raise json.JSONDecodeError("Failed to parse JSON after all attempts", original_content, 0)

    def _direct_parse(self, content: str) -> str:
        """Strategy 1: Direct parsing (including handling extra data)"""
        try:
            json.loads(content)
            return content
        except json.JSONDecodeError as e:
            if "Extra data" in str(e):
                return self._extract_first_json(content)
            raise

    def _extract_from_code_block(self, content: str) -> str:
        """Strategy 2: Extract JSON from code block"""
        match = re.search(r"```json\s*(.*?)\s*```", content, re.DOTALL)
        if not match:
            raise json.JSONDecodeError("No JSON code block found", content, 0)

        json_content = match.group(1).strip()
        return self._direct_parse(json_content)

    def _fix_python_syntax(self, content: str) -> str:
        """Strategy 3: Fix Python syntax before parsing"""
        fixed = self._fix_python_booleans(content)
        return self._direct_parse(fixed)

    def _extract_with_fix_combined(self, content: str) -> str:
        """Strategy 4: Combined strategy - fix Python syntax first, then extract the first JSON object"""
        fixed = self._fix_python_booleans(content)

        # Try to extract code block from the fixed content
        match = re.search(r"```json\s*(.*?)\s*```", fixed, re.DOTALL)
        if match:
            fixed = match.group(1).strip()

        return self._direct_parse(fixed)

    @staticmethod
    def _fix_python_booleans(json_str: str) -> str:
        """Safely fix Python-style booleans to JSON standard format using tokenize"""
        replacements = {"True": "true", "False": "false", "None": "null"}

        try:
            out = []
            io_string = io.StringIO(json_str)
            tokens = tokenize.generate_tokens(io_string.readline)

            for toknum, tokval, _, _, _ in tokens:
                if toknum == tokenize.NAME and tokval in replacements:
                    out.append(replacements[tokval])
                else:
                    out.append(tokval)

            result = "".join(out)
            return result

        except (tokenize.TokenError, json.JSONDecodeError):
            # If tokenize fails, fallback to regex method
            for python_val, json_val in replacements.items():
                json_str = re.sub(rf"\b{python_val}\b", json_val, json_str)
            return json_str

    @staticmethod
    def _extract_first_json(response: str) -> str:
        """Extract the first complete JSON object, ignoring extra content"""
        decoder = json.JSONDecoder()
        obj, _ = decoder.raw_decode(response)
        return json.dumps(obj)
# END BYTE-EXACT UPSTREAM JSONParser
# fmt: on


class SourceResponseError(ValueError):
    """Bounded stage code only; never expose response/native exception content."""

    def __init__(self, stage: str):
        self.stage = stage
        super().__init__(stage)


@dataclass(frozen=True)
class ParsedSelectedResponses:
    source: str
    source_sha256: str
    raw_text_sha256: str
    normalized_json_sha256: str
    source_size_bytes: int
    raw_text: str
    normalized_json: str


def parse_selected_responses(
    responses: tuple[RDChatResponse, ...],
) -> ParsedSelectedResponses:
    """Reassemble one selected length...stop chain and extract actual code text.

    Callers must independently authenticate ordered response membership and retain
    original bytes. This function neither chooses a chain nor verifies provenance.
    """
    if type(responses) is not tuple or not 1 <= len(responses) <= MAX_RESPONSES:
        raise SourceResponseError("response_chain")
    try:
        if any(type(response) is not RDChatResponse for response in responses):
            raise ValueError("typed response required")
        checked = tuple(
            RDChatResponse.model_validate(response.model_dump())
            for response in responses
        )
        if [response.finish_reason for response in checked] != ["length"] * (
            len(checked) - 1
        ) + ["stop"]:
            raise ValueError("closed continuation chain required")
        raw_text = "".join(response.content for response in checked)
        raw = raw_text.encode("utf-8", errors="strict")
        if len(raw_text) > MAX_RAW_CHARS or len(raw) > MAX_RAW_BYTES:
            raise ValueError("response bytes exceeded")
    except (ValueError, UnicodeError) as error:
        raise SourceResponseError("response_chain") from error
    try:
        normalized = JSONParser(add_json_in_prompt=False).parse(raw_text)
        normalized_bytes = normalized.encode("utf-8", errors="strict")
        if len(normalized_bytes) > MAX_NORMALIZED_BYTES:
            raise ValueError("normalization exceeded fixed byte bound")
    except (
        ValueError,
        UnicodeError,
        tokenize.TokenError,
        IndentationError,
        RecursionError,
    ) as error:
        raise SourceResponseError("json_parse") from error
    try:
        document = TypeAdapter(dict[str, str]).validate_json(normalized)
    except ValidationError as error:
        raise SourceResponseError("response_schema") from error
    try:
        source = document["code"]
    except KeyError as error:
        raise SourceResponseError("missing_code") from error
    try:
        sha = source_sha256(source)
    except ValueError as error:
        raise SourceResponseError("source_contract") from error
    return ParsedSelectedResponses(
        source=source,
        source_sha256=sha,
        raw_text_sha256=hashlib.sha256(raw).hexdigest(),
        normalized_json_sha256=hashlib.sha256(normalized_bytes).hexdigest(),
        source_size_bytes=len(source.encode("utf-8")),
        raw_text=raw_text,
        normalized_json=normalized,
    )
