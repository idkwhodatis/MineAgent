"""Unregistered typed normal note draft, not write/restore/retention acceptance."""

import json
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError

from backend.app.normal_oe_note_contracts import (
    NormalOENoteEvidence,
    NormalOENoteRestore,
    exact_normal_restore,
    normal_note_href,
)
from backend.app.research_note_schemas import ResearchNoteContent
from packages.research_trace.contracts import canonical_bytes, digest

adapter = TypeAdapter(NormalOENoteEvidence)


def raw(kind="normal_oe_execution"):
    value = {
        key: str(UUID(int=index + 1))
        for index, key in enumerate(
            (
                "owner_id",
                "run_id",
                "execution_attempt_id",
                "execution_id",
                "candidate_id",
                "program_version_id",
                "factor_id",
                "factor_version_id",
                "snapshot_id",
            )
        )
    }
    value.update(
        {
            key: "a" * 64
            for key in (
                "program_sha256",
                "snapshot_input_sha256",
                "result_sha256",
                "market_sha256",
                "config_sha256",
                "measurement_sha256",
            )
        }
    )
    value.update(kind=kind, generation=1)
    if kind == "normal_oe_decision":
        value.update(decision_sequence=0, row_sha256="b" * 64)
    if kind == "normal_oe_fill":
        value.update(
            native_fill_id="real/fill?1",
            listing_id=str(UUID(int=20)),
            session_date="2026-10-28",
            row_sha256="b" * 64,
        )
    return value


@pytest.mark.parametrize(
    "kind", ["normal_oe_execution", "normal_oe_decision", "normal_oe_fill"]
)
def test_exact_typed_identity_and_route(kind):
    ref = adapter.validate_json(json.dumps(raw(kind)))
    path = normal_note_href(ref)
    url = urlsplit(path)
    assert url.path == f"/engine-runs/structured-search/runs/{ref.run_id}"
    values = parse_qs(url.query)
    assert values["kind"] == [kind]
    from backend.app.normal_oe_note_contracts import physical_reference_sha256

    assert values["evidence_sha256"] == [physical_reference_sha256(ref)]
    assert "strategy_version_id" not in values
    assert (
        exact_normal_restore(
            NormalOENoteRestore(
                schema_version="mineagent.note-normal-oe-restore.v1", evidence_index=0
            ),
            [ref],
            [True],
        )
        == path
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("generation", True),
        ("generation", 2),
        ("strategy_version_id", str(UUID(int=1))),
        ("result_sha256", "xyz"),
        ("execution_attempt_id", "not-an-id"),
        ("kind", "decision"),
        ("decision_sequence", 0),
    ],
)
def test_foreign_legacy_extra_or_invalid_identity_rejected(key, value):
    body = raw()
    body[key] = value
    with pytest.raises(ValidationError):
        adapter.validate_json(json.dumps(body))


def test_missing_required_owner_and_cross_kind_selector_fail():
    for key in (
        "owner_id",
        "candidate_id",
        "execution_attempt_id",
        "snapshot_input_sha256",
    ):
        body = raw()
        del body[key]
        with pytest.raises(ValidationError):
            adapter.validate_json(json.dumps(body))
    body = raw("normal_oe_fill")
    body["decision_sequence"] = 0
    with pytest.raises(ValidationError):
        adapter.validate_json(json.dumps(body))
    body = raw("normal_oe_fill")
    body["session_date"] = "2026-02-30"
    with pytest.raises(ValidationError):
        adapter.validate_json(json.dumps(body))


def test_restore_has_no_legacy_id_or_fallback_or_permission_inference():
    ref = adapter.validate_json(json.dumps(raw()))
    restore = NormalOENoteRestore(
        schema_version="mineagent.note-normal-oe-restore.v1", evidence_index=0
    )
    for refs, availability in (
        ([], []),
        ([ref], [False]),
        ([ref], []),
        ([{"kind": "source"}], [True]),
    ):
        with pytest.raises(ValueError):
            exact_normal_restore(restore, refs, availability)
    with pytest.raises(ValidationError):
        NormalOENoteRestore.model_validate(
            {
                "schema_version": "mineagent.note-normal-oe-restore.v1",
                "evidence_index": 0,
                "strategy_version_id": str(UUID(int=1)),
            }
        )


def test_legacy_canonical_is_unchanged_and_normal_grammar_is_runtime_gated():
    old = ResearchNoteContent(
        title="Legacy canonical sentinel",
        body="Human note, not validation",
        evidence=(),
    )
    assert (
        digest(old)
        == "64ccb211eafc96749a441deb09aaaaebb1389af0d4c423314d129cd7d0e4bd12"
    )
    assert (
        canonical_bytes(old)
        == b'{"applicability":"","behavior_evidence":"","body":"Human note, not validation","control_results":"","counterexamples":"","evidence":[],"hypothesis":"","motivation":"","next_steps":"","restore":null,"rules":"","sources":"","status":"pending","tags":[],"title":"Legacy canonical sentinel"}'
    )
    # New grammar is parsed, but only exact032 capability+resolver can write it.
    assert (
        ResearchNoteContent.model_validate_json(
            json.dumps({"title": "Runtime gated", "evidence": [raw()]})
        )
        .evidence[0]
        .kind
        == "normal_oe_execution"
    )


def test_restore_rejects_bare_identity_and_unknown_subclass_before_url_build():
    from backend.app.normal_oe_note_contracts import (
        NormalOEExecutionNoteEvidence,
        NormalOENoteIdentity,
    )

    restore = NormalOENoteRestore(
        schema_version="mineagent.note-normal-oe-restore.v1", evidence_index=0
    )
    body = raw()
    bare = {key: value for key, value in body.items() if key != "kind"}
    identity = NormalOENoteIdentity.model_validate_json(json.dumps(bare))

    class UnknownEvidence(NormalOEExecutionNoteEvidence):
        pass

    unknown = UnknownEvidence.model_validate_json(json.dumps(body))
    for value in (identity, unknown):
        with pytest.raises(ValueError, match="exact admitted evidence"):
            exact_normal_restore(restore, [value], [True])


def test_human_label_changes_note_commitment_not_physical_restore_anchor():
    first = adapter.validate_json(json.dumps(raw()))
    second = first.model_copy(update={"label": "Different human description"})
    assert digest(first) != digest(second)
    assert normal_note_href(first) == normal_note_href(second)
