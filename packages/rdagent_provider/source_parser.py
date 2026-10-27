"""Explicit provider response parsing; never coerce away usage into legacy DTOs.

Uses the unchanged pinned JSONParser and its exact source/bounds/result contract.
Only response envelope validation differs; differential tests cover content parity.
"""

import hashlib
import tokenize

from pydantic import TypeAdapter, ValidationError

from packages.factors.contracts import source_sha256
from packages.rdagent_adapter.response_parser import (
    MAX_NORMALIZED_BYTES,
    MAX_RAW_BYTES,
    MAX_RAW_CHARS,
    MAX_RESPONSES,
    JSONParser,
    ParsedSelectedResponses,
    SourceResponseError,
)
from packages.rdagent_provider.contracts import RDProviderChatResponse


def parse_selected_provider_responses(
    responses: tuple[RDProviderChatResponse, ...],
) -> ParsedSelectedResponses:
    """Reassemble one selected length...stop chain and extract actual code text.

    Callers must independently authenticate ordered response membership and retain
    original bytes. This function neither chooses a chain nor verifies provenance.
    """
    if type(responses) is not tuple or not 1 <= len(responses) <= MAX_RESPONSES:
        raise SourceResponseError("response_chain")
    try:
        if any(type(response) is not RDProviderChatResponse for response in responses):
            raise ValueError("typed response required")
        checked = tuple(
            RDProviderChatResponse.model_validate(response.model_dump())
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
