"""Portable normal note admission/row/fence tests, not a real PG/cold proof."""

from contextlib import contextmanager
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from pydantic import BaseModel

from backend.app import normal_oe_note_evidence as service
from backend.app.services import DomainError
from packages.factors.contracts import digest
from packages.structured_factor_strategy.views import ObservedFill


class Decision(BaseModel):
    event_seq: int
    actual_value: int


def scope():
    identity = {
        key: uuid4()
        for key in (
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
    }
    identity.update(
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
    identity["generation"] = 1
    fill = ObservedFill(
        native_fill_id="normal/actual-fill",
        native_order_id="order",
        logical_order_id="b" * 64,
        listing_id=uuid4(),
        side="buy",
        quantity=2,
        price=3,
        commission=0.1,
        session_date="2026-10-28",
    )

    class View(BaseModel):
        decisions: list[Decision]
        fills: list[ObservedFill]

    return (
        identity,
        View(decisions=[Decision(event_seq=0, actual_value=2)], fills=[fill]),
        uuid4(),
        {"factor": uuid4(), "market": uuid4()},
    )


def test_capture_recomputes_actual_row_bytes_and_missing_duplicate_are_rejected():
    value = scope()
    decision = service.capture_from_scope(
        value, "normal_oe_decision", decision_sequence=0
    )
    assert decision.row_sha256 == digest(value[1].decisions[0])
    fill = service.capture_from_scope(
        value, "normal_oe_fill", native_fill_id="normal/actual-fill"
    )
    assert fill.row_sha256 == digest(value[1].fills[0])
    assert fill.listing_id == value[1].fills[0].listing_id
    for kwargs in (
        {"decision_sequence": 1},
        {"decision_sequence": True},
        {"decision_sequence": 0, "native_fill_id": "unexpected"},
    ):
        with pytest.raises(DomainError):
            service.capture_from_scope(value, "normal_oe_decision", **kwargs)
    value[1].fills *= 2
    with pytest.raises(DomainError):
        service.capture_from_scope(
            value, "normal_oe_fill", native_fill_id="normal/actual-fill"
        )


def resolver(owner):
    return NS(
        owner_id=owner,
        session=object(),
        settings=object(),
        normal_oe_scopes={},
        normal_oe_view_bytes=0,
        check_budget=lambda: None,
        pin=lambda kind, identifier, sha, **details: {
            "resource_kind": kind,
            "resource_id": str(identifier),
            "sha256": sha,
            "details": details,
        },
    )


def test_resolver_rejects_changed_row_hash_and_foreign_owner(monkeypatch):
    actual = scope()
    monkeypatch.setattr(
        service, "scope_identity", lambda session, settings: (session, session)
    )
    ref = service.capture_from_scope(actual, "normal_oe_decision", decision_sequence=0)
    calls = []
    monkeypatch.setattr(
        service, "load_scope", lambda *a: calls.append("loaded") or actual
    )
    result = service.resolve(resolver(ref.owner_id), ref)
    assert result.bindings == []  # never native EvaluationBinding
    assert (
        len(result.pins) == 1
        and result.pins[0]["resource_kind"] == "normal_oe_execution"
    )
    with pytest.raises(DomainError):
        service.resolve(resolver(uuid4()), ref)
    assert calls == ["loaded"]
    actual[1].decisions[0] = Decision(event_seq=0, actual_value=3)
    with pytest.raises(DomainError, match="differs"):
        service.resolve(resolver(ref.owner_id), ref)


def test_multiple_observations_share_one_exact_execution_pin(monkeypatch):
    actual = scope()
    monkeypatch.setattr(
        service, "scope_identity", lambda session, settings: (session, session)
    )
    monkeypatch.setattr(service, "load_scope", lambda *a: actual)
    refs = [
        service.capture_from_scope(actual, "normal_oe_execution"),
        service.capture_from_scope(actual, "normal_oe_decision", decision_sequence=0),
        service.capture_from_scope(
            actual, "normal_oe_fill", native_fill_id="normal/actual-fill"
        ),
    ]
    pins = [service.resolve(resolver(ref.owner_id), ref).pins for ref in refs]
    assert pins[0] == pins[1] == pins[2]
    assert "execution_attempt_id" not in pins[0][0]["details"]
    assert pins[0][0]["details"]["attempt_id"] == str(actual[0]["execution_attempt_id"])


def test_unreleased_execution_never_reaches_private_reader(monkeypatch):
    monkeypatch.setattr(service, "transaction_fence", lambda *a: None)
    monkeypatch.setattr(service, "owned_execution", lambda *a: (None, None, None))

    def denied(*a):
        raise DomainError("engine_result_release_required", "No release", 409)

    monkeypatch.setattr(service, "require_receipts", denied)
    monkeypatch.setattr(
        service,
        "read_result_in_session",
        lambda *a, **kw: pytest.fail("Must not read private bytes"),
    )
    with pytest.raises(DomainError) as error:
        service.load_scope(object(), uuid4(), object(), uuid4(), uuid4())
    assert error.value.code == "engine_result_release_required"


def test_scope_retains_real_guards_until_commit_and_rolls_back_before_release(
    monkeypatch,
):
    events = []

    @contextmanager
    def lifecycle(*a):
        events.append("lock")
        try:
            yield
        finally:
            events.append("unlock")

    @contextmanager
    def native(*a):
        events.append("native")
        try:
            yield
        finally:
            events.append("native-close")

    monkeypatch.setattr(service, "shared_lifecycle", lifecycle)
    monkeypatch.setattr(service, "native_storage_guard", native)
    monkeypatch.setattr(service, "require_capability", lambda s: None)
    session = NS(
        in_transaction=lambda: False,
        get_bind=lambda: None,
        commit=lambda: events.append("commit"),
        rollback=lambda: events.append("rollback"),
    )
    settings = NS(storage_root="unused")
    with pytest.raises(DomainError):
        service.transaction_fence(session, settings)
    with service.normal_note_scope(session):
        service.transaction_fence(session, settings)
        with service.normal_note_scope(session):
            service.transaction_fence(session, settings)
        with pytest.raises(DomainError), service.normal_note_scope(object()):
            pass
        session.commit()
    assert events == ["lock", "native", "commit", "native-close", "unlock"]
    events.clear()
    with pytest.raises(RuntimeError), service.normal_note_scope(session):
        service.transaction_fence(session, settings)
        raise RuntimeError("failed before commit")
    assert events == ["lock", "native", "rollback", "native-close", "unlock"]
    with pytest.raises(DomainError):
        service.transaction_fence(session, settings)


def test_legacy_request_scope_does_not_acquire_normal_guards(monkeypatch):
    monkeypatch.setattr(
        service,
        "shared_lifecycle",
        lambda *a: pytest.fail("Legacy must not acquire native lifecycle"),
    )
    with service.normal_note_scope(NS(rollback=lambda: None)):
        pass


def test_saved_revision_requires_real_typed_pin_attachment(monkeypatch):
    actual = scope()
    ref = service.capture_from_scope(actual, "normal_oe_execution")
    generic = NS(
        id=uuid4(),
        resource_id=ref.execution_id,
        sha256=ref.result_sha256,
        details=service.pin_details(ref),
    )
    revision = NS(
        id=uuid4(),
        owner_id=ref.owner_id,
        admission_snapshot=[
            {
                "schema_version": "mineagent.normal-oe-note-admission.v1",
                "reference": ref.model_dump(mode="json"),
            }
        ],
        evidence_snapshot=[
            {
                "schema_version": "mineagent.normal-oe-note-admission.v1",
                "reference": ref.model_dump(mode="json"),
            }
        ],
    )
    monkeypatch.setattr(service, "require_capability", lambda s: None)
    monkeypatch.setattr(
        service,
        "owned_execution",
        lambda *a: (NS(id=ref.run_id), NS(id=ref.execution_id), None),
    )
    monkeypatch.setattr(service, "require_receipts", lambda *a: None)
    outcome = NS(id=uuid4(), result_sha256=ref.result_sha256)
    exposures = [uuid4(), uuid4()]
    values = iter([outcome, *exposures])
    session = NS(
        scalars=lambda _: NS(all=lambda: [generic]),
        get=lambda *a: None,
        scalar=lambda _: next(values),
    )
    with pytest.raises(DomainError, match="differs"):
        service.verify_revision_pins(session, revision)


def test_missing_request_scope_and_unfinished_transaction_fail_closed(monkeypatch):
    events = []

    @contextmanager
    def guard(*args):
        events.append("open")
        try:
            yield
        finally:
            events.append("close")

    monkeypatch.setattr(service, "require_capability", lambda s: None)
    monkeypatch.setattr(service, "shared_lifecycle", guard)
    monkeypatch.setattr(service, "native_storage_guard", guard)
    session = NS(
        get_bind=lambda: None,
        in_transaction=lambda: True,
        rollback=lambda: events.append("rollback"),
    )
    with pytest.raises(DomainError) as error, service.normal_note_scope(session):
        service.transaction_fence(session, NS(storage_root="unused"))
    assert error.value.code == "normal_oe_note_transaction_unfinished"
    assert events == ["open", "open", "rollback", "close", "close"]


def test_admission_snapshot_denial_is_not_cached_outcome_permission(monkeypatch):
    from backend.app.research_note_evidence import EvidenceResolver

    actual = scope()
    ref = service.capture_from_scope(actual, "normal_oe_execution")

    def denied(ref):
        raise DomainError("engine_result_release_required", "No release", 409)

    resolver = NS(check_budget=lambda: None, resolve=denied)
    with pytest.raises(DomainError) as error:
        EvidenceResolver.admit_snapshot(
            resolver,
            {
                "schema_version": "mineagent.normal-oe-note-admission.v1",
                "reference": ref.model_dump(mode="json"),
            },
        )
    assert error.value.code == "engine_result_release_required"


def test_scope_same_context_cleanup_on_session_close_and_partial_acquire(monkeypatch):
    events = []

    @contextmanager
    def lifecycle(*args):
        events.append("lifecycle")
        try:
            yield
        finally:
            events.append("lifecycle-close")

    @contextmanager
    def native(*args):
        events.append("native")
        raise OSError("namespace busy")
        yield  # pragma: no cover

    monkeypatch.setattr(service, "require_capability", lambda s: None)
    monkeypatch.setattr(service, "shared_lifecycle", lifecycle)
    monkeypatch.setattr(service, "native_storage_guard", native)
    session = NS(
        get_bind=lambda: None,
        in_transaction=lambda: False,
        rollback=lambda: events.append("rollback"),
    )
    with pytest.raises(OSError), service.normal_note_scope(session):
        service.transaction_fence(session, NS(storage_root="unused"))
    assert events == ["lifecycle", "native", "rollback", "lifecycle-close"]
    assert service._SCOPE.get() is None


def test_cache_stays_in_exact_request_transaction_and_rechecks_release(monkeypatch):
    actual = scope()
    first = service.capture_from_scope(actual, "normal_oe_execution")
    second = service.capture_from_scope(
        actual, "normal_oe_decision", decision_sequence=0
    )
    identity = [object(), object()]
    calls = []
    monkeypatch.setattr(service, "scope_identity", lambda *a: tuple(identity))
    monkeypatch.setattr(
        service, "load_scope", lambda *a: calls.append("read") or actual
    )
    monkeypatch.setattr(service, "require_receipts", lambda *a: calls.append("release"))
    retained = resolver(first.owner_id)
    service.resolve(retained, first)
    service.resolve(retained, second)
    assert calls == ["read", "release"]
    with pytest.raises(DomainError):
        service.resolve(retained, second.model_copy(update={"row_sha256": "0" * 64}))
    identity[1] = object()
    with pytest.raises(DomainError) as error:
        service.resolve(retained, second)
    assert error.value.code == "normal_oe_note_scope_required"
    # A new request resolver always reloads originals, never consumes another cache.
    service.resolve(resolver(first.owner_id), first)
    assert calls.count("read") == 2


def test_cached_release_loss_and_budget_exhaustion_never_return_a_view(monkeypatch):
    actual = scope()
    ref = service.capture_from_scope(actual, "normal_oe_execution")
    identity = (object(), object())
    monkeypatch.setattr(service, "scope_identity", lambda *a: identity)
    monkeypatch.setattr(service, "load_scope", lambda *a: actual)
    retained = resolver(ref.owner_id)
    service.resolve(retained, ref)

    def denied(*a):
        raise DomainError("engine_result_release_required", "No release", 409)

    monkeypatch.setattr(service, "require_receipts", denied)
    with pytest.raises(DomainError) as error:
        service.resolve(retained, ref)
    assert error.value.code == "engine_result_release_required"

    class TooLarge:
        def __len__(self):
            return 32 * 1024**2 + 1

    monkeypatch.setattr(service, "canonical_bytes", lambda _: TooLarge())
    with pytest.raises(DomainError) as error:
        service.resolve(resolver(ref.owner_id), ref)
    assert error.value.code == "note_evidence_capacity"


def test_cached_deadline_and_cross_owner_or_run_cannot_be_reinterpreted(monkeypatch):
    actual = scope()
    ref = service.capture_from_scope(actual, "normal_oe_execution")
    identity = (object(), object())
    monkeypatch.setattr(service, "scope_identity", lambda *a: identity)
    monkeypatch.setattr(service, "load_scope", lambda *a: actual)
    monkeypatch.setattr(service, "require_receipts", lambda *a: None)
    retained = resolver(ref.owner_id)
    service.resolve(retained, ref)
    with pytest.raises(DomainError):
        service.resolve(retained, ref.model_copy(update={"owner_id": uuid4()}))
    with pytest.raises(DomainError):
        service.resolve(retained, ref.model_copy(update={"run_id": uuid4()}))
    with pytest.raises(DomainError):
        service.resolve(retained, ref.model_copy(update={"execution_id": uuid4()}))

    def expired():
        raise DomainError("note_evidence_capacity", "Original deadline expired", 422)

    retained.check_budget = expired
    with pytest.raises(DomainError, match="deadline"):
        service.resolve(retained, ref)


@pytest.mark.parametrize(
    "changed",
    [
        "outside_scope",
        "new_scope",
        "new_transaction",
        "commit",
        "failed_transaction",
        "native_guard",
        "release",
    ],
)
def test_actual_resolver_same_ref_never_bypasses_current_fence(monkeypatch, changed):
    from backend.app import research_note_evidence as notes

    actual = scope()
    reference = service.capture_from_scope(actual, "normal_oe_execution")
    events, live = [], {"native": False}
    tx = {"value": NS(is_active=True)}
    session = NS(
        get_bind=lambda: None,
        get_transaction=lambda: tx["value"],
        in_transaction=lambda: tx["value"] is not None,
        rollback=lambda: tx.update(value=None),
        commit=lambda: tx.update(value=None),
    )
    settings = NS(storage_root="portable guard double")

    @contextmanager
    def lifecycle(*a):
        yield

    @contextmanager
    def native(*a):
        live["native"] = True
        try:
            yield
        finally:
            live["native"] = False

    def require_native(*a):
        if not live["native"]:
            raise ValueError("Missing guard")

    def load(*a):
        service.transaction_fence(session, settings)
        events.append("physical")
        return actual

    monkeypatch.setattr(notes, "advisory_lock", lambda *a: None)
    monkeypatch.setattr(service, "require_capability", lambda *a: None)
    monkeypatch.setattr(service, "shared_lifecycle", lifecycle)
    monkeypatch.setattr(service, "native_storage_guard", native)
    monkeypatch.setattr(service, "require_native_storage_guard", require_native)
    monkeypatch.setattr(service, "load_scope", load)
    monkeypatch.setattr(
        service, "require_receipts", lambda *a: events.append("release")
    )
    retained = notes.EvidenceResolver(session, reference.owner_id, settings)
    with service.normal_note_scope(session):
        retained.resolve(reference)
        retained.resolve(reference)
        assert retained.resolve_calls == 1
        assert events == ["physical", "release"]
        if changed not in {"outside_scope", "new_scope"}:
            if changed == "new_transaction":
                tx["value"] = NS(is_active=True)
            elif changed == "commit":
                session.commit()
            elif changed == "failed_transaction":
                tx["value"].is_active = False
            elif changed == "native_guard":
                live["native"] = False
            elif changed == "release":

                def denied(*a):
                    raise DomainError(
                        "engine_result_release_required", "Receipt missing", 409
                    )

                monkeypatch.setattr(service, "require_receipts", denied)
            with pytest.raises(DomainError):
                retained.resolve(reference)
        session.rollback()
    if changed == "outside_scope":
        with pytest.raises(DomainError):
            retained.resolve(reference)
    elif changed == "new_scope":
        tx["value"] = NS(is_active=True)
        with service.normal_note_scope(session):
            service.transaction_fence(session, settings)
            with pytest.raises(DomainError):
                retained.resolve(reference)
            session.rollback()
    assert events.count("physical") == 1


@pytest.mark.parametrize("reference", [None, {}, {"execution_id": "bad"}])
def test_malformed_saved_normal_reference_fails_as_domain_error(monkeypatch, reference):
    monkeypatch.setattr(service, "require_capability", lambda s: None)
    item = {
        "schema_version": "mineagent.normal-oe-note-admission.v1",
        "reference": reference,
    }
    revision = NS(
        id=uuid4(),
        owner_id=uuid4(),
        admission_snapshot=[item],
        evidence_snapshot=[item],
    )
    session = NS(scalars=lambda _: NS(all=list))
    with pytest.raises(DomainError) as failure:
        service.verify_revision_pins(session, revision)
    assert failure.value.code == "note_evidence_pin_mismatch"
