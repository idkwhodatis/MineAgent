"""Exact032 note evidence: existing disclosure first, private originals rechecked.

The lazy fence lasts through the session's outer transaction (including note pin
flush/commit). No release creation and no legacy strategy/evaluation identity.
"""

import json
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from functools import wraps
from inspect import signature

from pydantic import TypeAdapter
from sqlalchemy import select

from backend.app.core.maintenance import shared_lifecycle
from backend.app.experiment_services import _identities
from backend.app.factor_native_io import (
    native_storage_guard,
    require_native_storage_guard,
)
from backend.app.models import DataUsageExposure
from backend.app.normal_oe_note_contracts import (
    NormalOENoteEvidence,
    NormalOENotePinDetails,
    normal_note_href,
)
from backend.app.oe_structured_models import OEStructuredEngineOutcome
from backend.app.oe_structured_result_history import read_result_in_session
from backend.app.oe_structured_result_release import (
    bindings,
    owned_execution,
    release_key,
    require_receipts,
)
from backend.app.oe_structured_result_views import project
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest

adapter = TypeAdapter(NormalOENoteEvidence)
_SCOPE = ContextVar("normal_oe_note_request_scope", default=None)


def mismatch():
    return DomainError(
        "note_evidence_pin_mismatch", "Exact normal OE note evidence differs", 409
    )


def require_capability(session):
    from backend.app.normal_oe_note_capability import require_normal_oe_notes

    require_normal_oe_notes(session)


@contextmanager
def normal_note_scope(session):
    """Endpoint-local lifetime, including commit/rollback, never dependency teardown."""
    existing = _SCOPE.get()
    if existing is not None:
        if existing["session"] is not session:
            raise DomainError(
                "normal_oe_note_scope_required",
                "Nested foreign note session is forbidden",
                409,
            )
        yield
        return
    with ExitStack() as stack:
        state = {"session": session, "stack": stack, "fenced": False}
        token = _SCOPE.set(state)
        try:
            yield
            if state["fenced"] and session.in_transaction():
                raise DomainError(
                    "normal_oe_note_transaction_unfinished",
                    "Normal note transaction must finish inside its physical fence",
                    409,
                )
        except BaseException:
            session.rollback()
            raise
        finally:
            _SCOPE.reset(token)


def normal_note_route(function):
    sig = signature(function)

    @wraps(function)
    def wrapped(*args, **kwargs):
        session = sig.bind(*args, **kwargs).arguments["session"]
        with normal_note_scope(session):
            return function(*args, **kwargs)

    return wrapped


def transaction_fence(session, settings):
    require_capability(session)
    state = _SCOPE.get()
    if state is None or state["session"] is not session:
        raise DomainError(
            "normal_oe_note_scope_required",
            "Explicit normal note request scope required",
            409,
        )
    if state["fenced"]:
        return
    state["stack"].enter_context(
        shared_lifecycle(session.get_bind(), settings.storage_root)
    )
    state["stack"].enter_context(native_storage_guard(settings.storage_root))
    state["fenced"] = True


def scope_identity(session, settings):
    state = _SCOPE.get()
    transaction = session.get_transaction()
    if (
        state is None
        or state["session"] is not session
        or not state["fenced"]
        or transaction is None
        or not transaction.is_active
    ):
        raise DomainError(
            "normal_oe_note_scope_required",
            "Live normal note transaction fence required",
            409,
        )
    try:
        require_native_storage_guard(settings.storage_root)
    except (ValueError, OSError):
        raise DomainError(
            "normal_oe_note_scope_required",
            "Original normal note physical fence is unavailable",
            409,
        ) from None
    return state, transaction


def load_scope(session, owner_id, settings, run_id, execution_id):
    transaction_fence(session, settings)
    run, execution, _ = owned_execution(session, owner_id, run_id, execution_id)
    require_receipts(session, owner_id, execution_id)
    actual = read_result_in_session(
        session, settings, owner_id=owner_id, execution_id=execution_id
    )
    checked_run, sources = bindings(session, owner_id, run_id, execution_id, actual)
    require_receipts(session, owner_id, execution_id)
    outcome = session.scalar(
        select(OEStructuredEngineOutcome).filter_by(
            owner_id=owner_id, run_id=run_id, execution_id=execution_id
        )
    )
    if (
        outcome is None
        or checked_run.id != run.id
        or (
            outcome.attempt_id,
            outcome.generation,
            outcome.candidate_id,
            outcome.result_sha256,
            outcome.measurement_sha256,
        )
        != (
            execution.attempt_id,
            execution.generation,
            execution.candidate_id,
            actual.execution.result_sha256,
            actual.measurement_sha256,
        )
    ):
        raise mismatch()
    exposures = {}
    for role, dataset, evidence in sources:
        row = session.scalar(
            select(DataUsageExposure).filter_by(
                owner_id=owner_id, source_key=release_key(execution_id, role)
            )
        )
        expected = {
            **evidence,
            "role": role,
            "dataset_version_id": str(dataset.id),
            "dataset_content_sha256": dataset.content_sha256,
        }
        if row is None or (
            row.owner_id,
            row.run_id,
            row.experiment_id,
            row.split_version_id,
            row.dataset_version_id,
            row.purpose,
            row.start_date,
            row.end_date,
            row.identities,
            row.provenance,
        ) != (
            owner_id,
            run.id,
            run.experiment_id,
            run.split_version_id,
            dataset.id,
            "engine_structured_execution_release",
            dataset.start_date,
            dataset.end_date,
            _identities(session, dataset, None),
            expected,
        ):
            raise mismatch()
        exposures[role] = row.id
    if set(exposures) != {"factor", "market"}:
        raise mismatch()
    view = project(actual).execution
    identity = {
        "owner_id": owner_id,
        "run_id": run.id,
        "execution_attempt_id": execution.attempt_id,
        "generation": execution.generation,
        "execution_id": execution.id,
        "candidate_id": actual.candidate_id,
        "program_version_id": view.structured_strategy_version_id,
        "program_sha256": view.program_sha256,
        "factor_id": view.factor_id,
        "factor_version_id": view.factor_version_id,
        "snapshot_id": view.execution_snapshot_id,
        "snapshot_input_sha256": view.snapshot_input_sha256,
        "result_sha256": view.result_sha256,
        "market_sha256": view.market_sha256,
        "config_sha256": actual.config_sha256,
        "measurement_sha256": actual.measurement_sha256,
    }
    return identity, view, outcome.id, exposures


def capture_from_scope(
    scope, kind, *, decision_sequence=None, native_fill_id=None, label=""
):
    identity, view, _, _ = scope
    value = {**identity, "kind": kind, "label": label}
    if kind == "normal_oe_execution":
        if decision_sequence is not None or native_fill_id is not None:
            raise mismatch()
    elif kind == "normal_oe_decision":
        if type(decision_sequence) is not int or native_fill_id is not None:
            raise mismatch()
        rows = [row for row in view.decisions if row.event_seq == decision_sequence]
        if len(rows) != 1:
            raise mismatch()
        value.update(decision_sequence=decision_sequence, row_sha256=digest(rows[0]))
    elif kind == "normal_oe_fill":
        if decision_sequence is not None:
            raise mismatch()
        rows = [row for row in view.fills if row.native_fill_id == native_fill_id]
        if len(rows) != 1:
            raise mismatch()
        row = rows[0]
        value.update(
            native_fill_id=row.native_fill_id,
            listing_id=row.listing_id,
            session_date=str(row.session_date),
            row_sha256=digest(row),
        )
    else:
        raise mismatch()
    return adapter.validate_python(value)


def capture(
    session,
    owner_id,
    settings,
    run_id,
    execution_id,
    kind,
    *,
    decision_sequence=None,
    native_fill_id=None,
):
    return capture_from_scope(
        load_scope(session, owner_id, settings, run_id, execution_id),
        kind,
        decision_sequence=decision_sequence,
        native_fill_id=native_fill_id,
    )


def parse_pin_details(raw):
    try:
        return NormalOENotePinDetails.model_validate_json(json.dumps(raw))
    except (ValueError, TypeError):
        raise mismatch() from None


def pin_details(ref):
    fields = ref.model_dump(
        mode="json",
        exclude={
            "kind",
            "label",
            "owner_id",
            "execution_id",
            "result_sha256",
            "decision_sequence",
            "native_fill_id",
            "listing_id",
            "session_date",
            "row_sha256",
        },
    )
    fields["attempt_id"] = fields.pop("execution_attempt_id")
    return NormalOENotePinDetails.model_validate_json(
        json.dumps({"schema_version": "mineagent.note-normal-oe-pin.v1", **fields})
    ).model_dump(mode="json")


def resolve(resolver, ref):
    from backend.app.research_note_evidence import ResolvedEvidence

    if ref.owner_id != resolver.owner_id:
        raise DomainError("not_found", "Owned normal OE note evidence not found", 404)
    key = (resolver.owner_id, ref.run_id, ref.execution_id)
    cached = resolver.normal_oe_scopes.get(key)
    if cached is None:
        scope = load_scope(
            resolver.session,
            resolver.owner_id,
            resolver.settings,
            ref.run_id,
            ref.execution_id,
        )
        context, transaction = scope_identity(resolver.session, resolver.settings)
        size = len(canonical_bytes(scope[1]))
        if resolver.normal_oe_view_bytes + size > 32 * 1024**2:
            raise DomainError(
                "note_evidence_capacity",
                "Normal note views exceed the bounded 32 MiB request inventory",
                422,
            )
        resolver.normal_oe_view_bytes += size
        resolver.normal_oe_scopes[key] = (context, transaction, scope)
    else:
        context, transaction = scope_identity(resolver.session, resolver.settings)
        if context is not cached[0] or transaction is not cached[1]:
            raise DomainError(
                "normal_oe_note_scope_required",
                "Cached evidence cannot cross a request or transaction",
                409,
            )
        scope = cached[2]
        require_receipts(resolver.session, resolver.owner_id, ref.execution_id)
    resolver.check_budget()
    actual = capture_from_scope(
        scope,
        ref.kind,
        decision_sequence=getattr(ref, "decision_sequence", None),
        native_fill_id=getattr(ref, "native_fill_id", None),
        label=ref.label,
    )
    if actual != ref:
        raise mismatch()
    return ResolvedEvidence(
        snapshot={
            "schema_version": "mineagent.normal-oe-note-admission.v1",
            "reference": ref.model_dump(mode="json"),
        },
        pins=[
            resolver.pin(
                "normal_oe_execution",
                ref.execution_id,
                ref.result_sha256,
                **pin_details(ref),
            )
        ],
        href=normal_note_href(ref),
    )


def attach_pin(session, pin):
    """Generic and typed attachment are flushed in the same outer note transaction."""
    from backend.app.normal_oe_note_models import NormalOENotePin

    require_capability(session)
    details = parse_pin_details(pin.details)
    run, execution, _ = owned_execution(
        session, pin.owner_id, details.run_id, pin.resource_id
    )
    require_receipts(session, pin.owner_id, execution.id)
    outcome = session.scalar(
        select(OEStructuredEngineOutcome).filter_by(
            owner_id=pin.owner_id, run_id=run.id, execution_id=execution.id
        )
    )
    if (
        outcome is None
        or outcome.result_sha256 != pin.sha256
        or (execution.attempt_id, execution.generation)
        != (details.attempt_id, details.generation)
    ):
        raise mismatch()
    ids = {
        role: session.scalar(
            select(DataUsageExposure.id).filter_by(
                owner_id=pin.owner_id, source_key=release_key(execution.id, role)
            )
        )
        for role in ("factor", "market")
    }
    if any(value is None for value in ids.values()):
        raise mismatch()
    session.add(
        NormalOENotePin(
            pin_id=pin.id,
            owner_id=pin.owner_id,
            run_id=run.id,
            attempt_id=execution.attempt_id,
            generation=execution.generation,
            execution_id=execution.id,
            engine_outcome_id=outcome.id,
            factor_exposure_id=ids["factor"],
            market_exposure_id=ids["market"],
        )
    )


def verify_revision_pins(session, revision):
    """A retained generic UUID is not proof that its typed normal attachment exists."""
    from backend.app.models import ResearchNoteEvidencePin
    from backend.app.normal_oe_note_models import NormalOENotePin

    normal = any(
        isinstance(item, dict)
        and item.get("schema_version") == "mineagent.normal-oe-note-admission.v1"
        for item in revision.admission_snapshot
    )
    if not normal:
        return
    require_capability(session)
    pins = session.scalars(
        select(ResearchNoteEvidencePin).filter_by(
            revision_id=revision.id,
            owner_id=revision.owner_id,
            resource_kind="normal_oe_execution",
        )
    ).all()
    expected = set()
    for item in revision.evidence_snapshot:
        if (
            not isinstance(item, dict)
            or item.get("schema_version") != "mineagent.normal-oe-note-admission.v1"
        ):
            continue
        try:
            reference = adapter.validate_json(json.dumps(item["reference"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise mismatch() from exc
        if reference.owner_id != revision.owner_id:
            raise mismatch()
        expected.add(str(reference.execution_id))
    if not expected.issubset({str(pin.resource_id) for pin in pins}):
        raise mismatch()
    for pin in pins:
        details = parse_pin_details(pin.details)
        typed = session.get(NormalOENotePin, pin.id)
        run, execution, _ = owned_execution(
            session, revision.owner_id, details.run_id, pin.resource_id
        )
        require_receipts(session, revision.owner_id, execution.id)
        outcome = session.scalar(
            select(OEStructuredEngineOutcome).filter_by(
                owner_id=revision.owner_id, run_id=run.id, execution_id=execution.id
            )
        )
        ids = {
            role: session.scalar(
                select(DataUsageExposure.id).filter_by(
                    owner_id=revision.owner_id,
                    source_key=release_key(execution.id, role),
                )
            )
            for role in ("factor", "market")
        }
        if (
            typed is None
            or outcome is None
            or pin.sha256 != outcome.result_sha256
            or None in ids.values()
            or (
                typed.owner_id,
                typed.run_id,
                typed.attempt_id,
                typed.generation,
                typed.execution_id,
                typed.engine_outcome_id,
                typed.factor_exposure_id,
                typed.market_exposure_id,
            )
            != (
                revision.owner_id,
                run.id,
                details.attempt_id,
                details.generation,
                execution.id,
                outcome.id,
                ids["factor"],
                ids["market"],
            )
        ):
            raise mismatch()
