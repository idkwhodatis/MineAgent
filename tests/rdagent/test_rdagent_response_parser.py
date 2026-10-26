"""Pinned parser logic only: no source compilation, execution or provenance grant."""

import ast
import hashlib
import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from packages.rdagent_adapter import response_parser as parser
from packages.research_runtime.engine_broker_contracts import RDChatResponse


def response(text, finish="stop"):
    return RDChatResponse(content=text, finish_reason=finish)


@pytest.mark.parametrize(
    "text,source",
    [
        ('{"code":"x = 1\\n"}', "x = 1\n"),
        ('```json\n{"code":"x=1"}\n```', "x=1"),
        ('{"code":"first","code":"last"}', "last"),
        ('{"code":"first"}{"code":"second"}', "first"),
        ('{"code":"x=1"} trailing words', "x=1"),
        ('{"code":"True False None"}', "True False None"),
        ('{"code":"é = 1"}', "é = 1"),
    ],
)
def test_exact_upstream_success_semantics(text, source):
    value = parser.parse_selected_responses((response(text),))
    assert value.source == source
    assert value.source_sha256 == hashlib.sha256(source.encode()).hexdigest()
    assert value.raw_text_sha256 == hashlib.sha256(text.encode()).hexdigest()
    assert (
        value.normalized_json_sha256
        == hashlib.sha256(value.normalized_json.encode()).hexdigest()
    )
    assert value.source_size_bytes == len(source.encode())
    with pytest.raises(FrozenInstanceError):
        value.source = "changed"


@pytest.mark.parametrize(
    "text,stage",
    [
        ('{"code":True}', "response_schema"),
        ('```json\n{"code":None}\n```', "response_schema"),
        ('{"code":1}', "response_schema"),
        ('{"other":"x"}', "missing_code"),
        ('{"code":""}', "source_contract"),
        ('{"code":"   "}', "source_contract"),
        ('{"code":"x\\u0000y"}', "source_contract"),
        ('{"code":"\\ud800"}', "response_schema"),
        ('<think>reason</think>{"code":"x=1"}', "json_parse"),
        ("not-json", "json_parse"),
        ('{"code":', "json_parse"),
        ('["x"]', "response_schema"),
    ],
)
def test_exact_error_stage_is_bounded(text, stage):
    with pytest.raises(parser.SourceResponseError) as captured:
        parser.parse_selected_responses((response(text),))
    assert captured.value.stage == stage and str(captured.value) == stage


def test_selected_continuations_join_without_newline_or_think_stripping():
    value = parser.parse_selected_responses(
        (response('{"co', "length"), response('de":"x"}'))
    )
    assert value.raw_text == '{"code":"x"}' and value.source == "x"


@pytest.mark.parametrize(
    "values",
    [
        (),
        [],
        (response("x", "length"),),
        (response("x"), response("y")),
        tuple(response("x", "length") for _ in range(7)),
        ({"content": "x", "finish_reason": "stop"},),
    ],
)
def test_chain_cardinality_exact_types_and_finish_states(values):
    with pytest.raises(parser.SourceResponseError, match="response_chain"):
        parser.parse_selected_responses(values)


def test_source_utf8_size_not_character_count():
    text = json.dumps({"code": "é" * 65537}, ensure_ascii=False)
    chunks = [text[index : index + 32768] for index in range(0, len(text), 32768)]
    values = tuple(
        response(chunk, "length" if index < len(chunks) - 1 else "stop")
        for index, chunk in enumerate(chunks)
    )
    with pytest.raises(parser.SourceResponseError, match="source_contract"):
        parser.parse_selected_responses(values)


def test_model_construct_cannot_bypass_revalidation():
    malformed = RDChatResponse.model_construct(
        content="x" * 32769, finish_reason="stop", kind="chat"
    )
    with pytest.raises(parser.SourceResponseError, match="response_chain"):
        parser.parse_selected_responses((malformed,))


def test_byte_exact_upstream_fragment_and_license_provenance():
    path = Path(parser.__file__)
    text = path.read_text()
    node = next(
        node
        for node in ast.parse(text).body
        if isinstance(node, ast.ClassDef) and node.name == "JSONParser"
    )
    fragment = ast.get_source_segment(text, node) + "\n"
    assert (
        hashlib.sha256(fragment.encode()).hexdigest() == parser.UPSTREAM_FRAGMENT_SHA256
    )
    provenance = json.loads(
        path.with_name("response_parser_provenance.json").read_text()
    )
    assert provenance["fragment_sha256"] == parser.UPSTREAM_FRAGMENT_SHA256
    assert provenance["upstream_commit"] == parser.UPSTREAM_COMMIT
    assert provenance["reasoning_think_rm"] is False
    root = path.resolve().parents[2]
    assert (
        "Copyright (c) Microsoft Corporation"
        in (root / provenance["license"]).read_text()
    )


@pytest.mark.parametrize("size", [131072, 131073])
def test_exact_source_byte_boundary(size):
    text = json.dumps({"code": "x" * size})
    chunks = [text[index : index + 32768] for index in range(0, len(text), 32768)]
    values = tuple(
        response(chunk, "length" if index < len(chunks) - 1 else "stop")
        for index, chunk in enumerate(chunks)
    )
    if size == 131072:
        result = parser.parse_selected_responses(values)
        assert result.source_size_bytes == size
    else:
        with pytest.raises(parser.SourceResponseError, match="source_contract"):
            parser.parse_selected_responses(values)
