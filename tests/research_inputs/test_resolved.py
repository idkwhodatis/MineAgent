from hashlib import sha256
from uuid import UUID

import pytest

from packages.research_inputs.contracts import InputVersionRef
from packages.research_inputs.resolved import ResolvedInputVersion


def value(**changes):
    payload = {
        "ref": InputVersionRef(
            origin="manual_user_submission",
            record_id=UUID(int=1),
            version_id=UUID(int=2),
            content_sha256="a" * 64,
        ),
        "kind": "direction",
        "title": "Manual direction",
        "text": "Use volume",
        "text_sha256": sha256(b"Use volume").hexdigest(),
    }
    payload.update(changes)
    return ResolvedInputVersion(**payload)


def test_content_commitment_is_not_text_hash():
    assert value().ref.content_sha256 != value().text_sha256


@pytest.mark.parametrize(
    "changes",
    [
        {"text_sha256": "b" * 64},
        {"text": "\x00"},
        {"title": "a\nb"},
        {"approved": True},
        {"text": " "},
    ],
)
def test_invalid_resolved_projection(changes):
    with pytest.raises(ValueError):
        value(**changes)


def test_unknown_is_not_model_input():
    with pytest.raises(ValueError):
        value(
            ref=InputVersionRef(
                origin="unknown",
                record_id=UUID(int=1),
                version_id=UUID(int=2),
                content_sha256="a" * 64,
            )
        )


def test_manual_crlf_bytes_and_hash_are_preserved_not_renormalized():
    text = "Keep this\r\nexact direction"
    actual = value(text=text, text_sha256=sha256(text.encode()).hexdigest())
    assert actual.text == text


def test_external_cannot_claim_manual_control_character_contract():
    text = "external\rtext"
    with pytest.raises(ValueError):
        value(
            ref=InputVersionRef(
                origin="normalized_external_proposal",
                record_id=UUID(int=1),
                version_id=UUID(int=2),
                content_sha256="a" * 64,
            ),
            text=text,
            text_sha256=sha256(text.encode()).hexdigest(),
        )
