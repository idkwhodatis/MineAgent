"""Immutable steer contracts and inert transaction doubles, not PostgreSQL proof."""

from hashlib import sha256
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import rdagent_steer_services as services
from backend.app.rdagent_steer_models import RDSteerVersion
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes
from packages.rdagent_adapter.steer import SteerSnapshot, SteerVersion


def version(
    text="Prefer a testable short-horizon hypothesis",
    *,
    kind="direction",
    steer_id=None,
    ordinal=1,
):
    return SteerVersion(
        steer_id=steer_id or uuid4(),
        version_id=uuid4(),
        ordinal=ordinal,
        kind=kind,
        text=text,
        text_sha256=sha256(text.encode()).hexdigest(),
    )


def row(owner, value):
    return NS(
        id=value.version_id,
        owner_id=owner,
        steer_id=value.steer_id,
        ordinal=value.ordinal,
        kind=value.kind,
        text=value.text,
        text_sha256=value.text_sha256,
        content_canonical=canonical_bytes(value).decode(),
        content_sha256=value.sha256,
    )


def test_snapshot_preserves_explicit_order_and_commits_exact_text():
    owner = uuid4()
    a, b = (
        version('方向\nunchanged 😀 " \\'),
        version("No unverified causal claims", kind="constraints"),
    )
    first = SteerSnapshot(owner_id=owner, versions=(a, b))
    reverse = SteerSnapshot(owner_id=owner, versions=(b, a))
    assert first.sha256 != reverse.sha256
    assert SteerSnapshot.model_validate_json(canonical_bytes(first)) == first
    assert first.versions[0].text == a.text


@pytest.mark.parametrize(
    "case",
    [
        "duplicate_version",
        "duplicate_root",
        "too_many",
        "too_large",
        "wrong_hash",
        "blank",
    ],
)
def test_invalid_steer_contracts_fail_closed(case):
    a = version()
    owner = uuid4()
    with pytest.raises(ValueError):
        if case == "duplicate_version":
            SteerSnapshot(owner_id=owner, versions=(a, a))
        elif case == "duplicate_root":
            SteerSnapshot(
                owner_id=owner, versions=(a, version(steer_id=a.steer_id, ordinal=2))
            )
        elif case == "too_many":
            SteerSnapshot(owner_id=owner, versions=tuple(version() for _ in range(9)))
        elif case == "too_large":
            SteerSnapshot(
                owner_id=owner, versions=tuple(version("界" * 2048) for _ in range(8))
            )
        elif case == "wrong_hash":
            SteerVersion.model_validate(a.model_dump() | {"text_sha256": "0" * 64})
        else:
            version(" \n ")


@pytest.fixture
def storage(monkeypatch):
    owner = uuid4()
    value = version()
    saved = row(owner, value)
    root = NS(
        id=value.steer_id,
        owner_id=owner,
        current_version_id=value.version_id,
        disabled=False,
        revision=1,
    )
    rows = {value.version_id: saved}
    reads = []
    events = []

    def get(model, key):
        reads.append(model)
        return rows.get(key) if model is RDSteerVersion else root

    session = NS(
        get=get,
        scalar=lambda _: root,
        add=lambda value: rows.update({value.id: value}),
        flush=lambda: events.append("flush"),
    )
    monkeypatch.setattr(services, "require_schema", lambda _: None)
    monkeypatch.setattr(
        services, "_try_neutral_lock", lambda *args: events.append("owner_lock")
    )
    return NS(
        owner=owner,
        value=value,
        saved=saved,
        root=root,
        rows=rows,
        reads=reads,
        events=events,
        session=session,
    )


def test_accepted_snapshot_survives_later_edit_and_disable(storage):
    s = storage
    snapshot = services.resolve_steer_snapshot(
        s.session, s.owner, (s.value.version_id,)
    )
    original = canonical_bytes(snapshot)
    root, new = services.edit_steer(
        s.session,
        s.owner,
        s.root.id,
        NS(
            expected_revision=1,
            kind="hypothesis",
            text="Only future runs use this text",
        ),
    )
    services.set_disabled(
        s.session, s.owner, root.id, NS(expected_revision=2, disabled=True)
    )
    assert root.revision == 3 and root.disabled and new.id != s.value.version_id
    with pytest.raises(DomainError) as caught:
        services.resolve_steer_snapshot(s.session, s.owner, (s.value.version_id,))
    assert caught.value.code == "steer_selection_changed"
    with pytest.raises(DomainError):
        services.resolve_steer_snapshot(s.session, s.owner, (new.id,))
    s.reads.clear()
    assert services.verify_steer_snapshot(s.session, s.owner, snapshot) == snapshot
    assert canonical_bytes(snapshot) == original and s.reads == [RDSteerVersion]
    assert s.events[0] == "owner_lock"


@pytest.mark.parametrize("mutation", ["owner", "text", "canonical", "hash"])
def test_historical_snapshot_rechecks_owner_and_immutable_commitment(storage, mutation):
    s = storage
    snapshot = SteerSnapshot(owner_id=s.owner, versions=(s.value,))
    if mutation == "owner":
        owner = uuid4()
    else:
        owner = s.owner
        if mutation == "text":
            s.saved.text = "tampered"
        elif mutation == "canonical":
            s.saved.content_canonical += " "
        else:
            s.saved.content_sha256 = "0" * 64
    with pytest.raises(DomainError):
        services.verify_steer_snapshot(s.session, owner, snapshot)


def test_stale_edit_cannot_replace_head_or_create_version(storage):
    s = storage
    with pytest.raises(DomainError) as caught:
        services.edit_steer(
            s.session,
            s.owner,
            s.root.id,
            NS(expected_revision=0, kind="direction", text="new"),
        )
    assert caught.value.code == "steer_edit_conflict"
    assert s.root.current_version_id == s.value.version_id and len(s.rows) == 1


def test_owner_cannot_select_another_owners_version(storage):
    s = storage
    with pytest.raises(DomainError) as caught:
        services.resolve_steer_snapshot(s.session, uuid4(), (s.value.version_id,))
    assert caught.value.status_code == 404
