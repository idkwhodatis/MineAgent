"""Bounded actual workflow source evidence; never a fence, release or grant.

Caller retains terminal run/attempt locks and native storage/lifecycle guard.
The provider-history module is supplied by the numeric-history integration.
"""

import hashlib
import time
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select

from backend.app.factor_analysis_history import retained_reader
from backend.app.factor_native_io import require_native_storage_guard
from packages.factors.contracts import canonical_bytes


def _document(row):
    return {
        c.name: str(value)
        if isinstance(value, UUID)
        else value.isoformat()
        if isinstance(value, (date, datetime))
        else value
        for c in row.__table__.columns
        for value in (getattr(row, c.name),)
    }


def _exact(rows, predicate, reason):
    found = [row for row in rows.values() if predicate(row)]
    if len(found) != 1:
        raise ValueError(reason)
    return found[0]


def _members(rows, source):
    members = sorted(
        (r for r in rows.values() if r["call_id"] == source["call_id"]),
        key=lambda r: r["request_ordinal"],
    )
    if not members or len(members) > 32:
        raise ValueError("Source operation count differs")
    for n, member in enumerate(members):
        if (
            type(member["request_ordinal"]) is not int
            or member["request_ordinal"] != n
            or member["start_id"] != source["start_id"]
            or member["context_id"] != source["context_id"]
            or member["relation"] != ("initial" if n == 0 else "continuation")
            or member["predecessor_id"] != (None if n == 0 else members[n - 1]["id"])
            or any(
                member[k] is not None
                for k in (
                    "parse_failure_object_id",
                    "parse_failure_object_kind",
                    "parse_failure_sha256",
                )
            )
        ):
            raise ValueError("Source operation ancestry differs")
    return members


def _physical_reader(root, deadline):
    read = retained_reader(root)
    total = 0

    def read_bytes(key, sha, maximum):
        nonlocal total
        if time.monotonic() >= deadline:
            raise ValueError("Source history read deadline")
        raw = read(key, maximum)
        total += len(raw)
        if total > 16 * 1024**2 or time.monotonic() >= deadline:
            raise ValueError("Source history aggregate read bound")
        if hashlib.sha256(raw).hexdigest() != sha:
            raise ValueError("Actual source bytes differ")
        return raw

    return read_bytes


def _guidance(
    config, source, call, member, operation, transport, consumption, request_bytes, seed
):
    """Exact persisted first-request inclusion, never semantic compliance."""
    from packages.rdagent_adapter.workflow_prompt import first_workflow_messages
    from packages.rdagent_provider import codec

    binding = config.workflow_inputs.binding
    expected = {
        **{
            key: source[key]
            for key in ("owner_id", "run_id", "attempt_id", "generation")
        },
        "workflow_id": str(binding.workflow_id),
        "stage_id": str(binding.stage_id),
        "input_revision_id": str(binding.input_revision_id),
        "input_sha256": config.workflow_inputs_sha256,
        "operation_id": operation["id"],
        "transport_id": transport["id"],
        "request_sha256": operation["request_sha256"],
    }
    if (
        any(consumption.get(key) != value for key, value in expected.items())
        or call["phase"] != "generation"
        or call["kind"] != "chat"
        or type(call["ordinal"]) is not int
        or call["ordinal"] != 0
        or member["request_ordinal"] != 0
        or member["relation"] != "initial"
        or member["call_id"] != call["id"]
        or member["operation_id"] != operation["id"]
        or transport["operation_id"] != operation["id"]
        or type(transport["ordinal"]) is not int
        or transport["ordinal"] != 1
    ):
        raise ValueError("Actual first workflow consumption differs")
    if (seed is None) != (config.workflow_parent is None) or (
        seed is not None and seed.reference != config.workflow_parent
    ):
        raise ValueError("Actual workflow seed differs")
    request = codec.parse_request(operation["protocol"], operation["request"])
    if (
        request_bytes != codec.canonical_bytes(operation["protocol"], request)
        or hashlib.sha256(request_bytes).hexdigest() != operation["request_sha256"]
    ):
        raise ValueError("Actual first workflow request bytes differ")
    system, user = first_workflow_messages(config, seed)
    if tuple((m.role, m.content) for m in request.messages) != (
        ("system", system),
        ("user", user),
    ):
        raise ValueError("Actual first workflow guidance differs")


def verify_workflow_source_history(session, run, source, settings):
    """Verify one persisted final-attempt source from actual registered bytes."""
    from backend.app import rdagent_models as rd
    from backend.app import research_broker_models as broker
    from backend.app.factor_models import FactorObject, FactorVersion
    from backend.app.models import ExecutionAttempt, ResearchRun
    from backend.app.rdagent_backup import SCOPE, _validate_source
    from backend.app.rdagent_numeric_failure_history import (
        _provider_model_bytes,
        _ScopedRows,
    )
    from backend.app.rdagent_provider_containment import provider_attempt_stopped
    from backend.app.rdagent_source_witness_models import (
        FactorSourceReadPart,
        FactorSourceReadWitness,
    )
    from backend.app.rdagent_workflow_capability import require_workflow_revision
    from backend.app.research_object_models import ResearchObject
    from packages.rdagent_adapter.workflow_identity import (
        require_workflow_source_identity,
    )
    from packages.rdagent_provider.contracts import RDProviderChatResponse
    from packages.rdagent_provider.source_parser import (
        parse_selected_provider_responses,
    )

    require_native_storage_guard(settings.storage_root)
    require_workflow_revision(session)
    actual = session.scalar(
        select(ResearchRun).filter_by(id=run.id, owner_id=run.owner_id)
    )
    if actual is None or actual.status not in {
        "succeeded",
        "failed",
        "cancelled",
        "interrupted",
        "paused",
    }:
        raise ValueError("Actual terminal workflow run required")
    config = require_workflow_source_identity(actual)
    attempt = session.scalar(
        select(ExecutionAttempt).filter_by(
            id=actual.active_attempt_id, run_id=actual.id, generation=actual.generation
        )
    )
    if attempt is None or attempt.finished_at is None or not attempt.sandbox_stopped:
        raise ValueError("Actual final stopped attempt required")
    loader = _ScopedRows(
        session, actual.owner_id, actual.id, attempt.id, attempt.generation
    )
    idx = {}
    for model in (
        rd.FactorGenerationSource,
        rd.FactorGenerationStart,
        rd.FactorUpstreamCall,
        rd.FactorResponseAssembly,
        rd.FactorResponseAssemblyPart,
        rd.FactorSourcePublication,
        rd.FactorSourcePublicationOutcome,
        rd.FactorControllerLaunch,
        rd.FactorUpstreamCallOutcome,
        rd.FactorGenerationModelOperation,
        FactorSourceReadWitness,
        FactorSourceReadPart,
        broker.ResearchModelOperation,
        broker.ResearchModelTransport,
        broker.ResearchModelOutcome,
        broker.ResearchBudgetReservation,
        broker.ResearchBudgetSettlement,
    ):
        idx[model.__tablename__] = {row["id"]: row for row in loader.read(model)}
    saved = idx["factor_generation_sources"].get(str(source.id))
    if saved is None or any(
        saved[k]
        != (str(getattr(source, k)) if k != "generation" else source.generation)
        for k in SCOPE
    ):
        raise ValueError("Source is not from the final owned attempt")

    def owned(model, identity):
        if time.monotonic() >= loader.deadline:
            raise ValueError("Source history query deadline")
        row = session.scalar(
            select(model).filter_by(owner_id=actual.owner_id, id=UUID(str(identity)))
        )
        if row is None:
            raise ValueError("Missing registered owned source evidence")
        document = _document(row)
        loader.rows += 1
        loader.bytes += len(canonical_bytes(document))
        if loader.rows > 4096 or loader.bytes > 8 * 1024**2:
            raise ValueError("Historical aggregate inventory exceeds bound")
        return document

    start = idx["factor_generation_starts"][saved["start_id"]]
    spec = owned(rd.FactorResearchSpec, start["spec_id"])
    if (
        spec["run_id"] != str(actual.id)
        or spec["config"] != actual.config
        or spec["config_sha256"] != config.sha256
        or spec["input_sha256"] != config.input.sha256
    ):
        raise ValueError("Source spec differs from workflow child")
    version = owned(FactorVersion, saved["factor_version_id"])
    code = owned(FactorObject, version["code_object_id"])
    idx["factor_versions"] = {version["id"]: version}
    idx["factor_objects"] = {code["id"]: code}
    idx["research_objects"] = {}
    members = _members(idx["factor_generation_model_operations"], saved)
    operations = {
        m["operation_id"]: idx["research_model_operations"][m["operation_id"]]
        for m in members
    }
    for member in members:
        op = operations[member["operation_id"]]
        if (
            op["context_id"] != saved["context_id"]
            or op["protocol"] != member["protocol"]
            or op["protocol"] != "mineagent.rdagent-provider-chat.v1"
            or op["purpose"]
            != ("chat" if member["relation"] == "initial" else "continuation")
        ):
            raise ValueError("Actual source request membership differs")
    transports = {
        k: t
        for k, t in idx["research_model_transports"].items()
        if t["operation_id"] in operations
    }
    outcomes = {
        k: o
        for k, o in idx["research_model_outcomes"].items()
        if o["transport_id"] in transports
    }
    for identity in (
        {o["request_object_id"] for o in operations.values()}
        | {o["response_object_id"] for o in outcomes.values()}
        | {idx["factor_response_assemblies"][saved["assembly_id"]]["object_id"]}
    ):
        obj = owned(ResearchObject, identity)
        idx["research_objects"][obj["id"]] = obj
    read_bytes = _physical_reader(settings.storage_root, loader.deadline)

    def link(row, field, table, *, scope=True):
        other = idx[table][row[field]]
        if other["owner_id"] != row["owner_id"] or (
            scope and any(other[k] != row[k] for k in SCOPE if k in other and k in row)
        ):
            raise ValueError("Source linked scope differs")
        return other

    def raw(obj, kind, *, factor=False):
        limit = 131072 if factor else 1048576
        key = (
            f"factors/{obj['owner_id']}/objects/{obj['sha256']}.py"
            if factor
            else f"research-objects/{obj['owner_id']}/by-kind/{kind}/{obj['sha256']}.json"
        )
        if (
            obj["object_kind"] != kind
            or obj["storage_key"] != key
            or not 0 < obj["size_bytes"] <= limit
        ):
            raise ValueError("Registered source object differs")
        data = read_bytes(key, obj["sha256"], limit)
        if len(data) != obj["size_bytes"]:
            raise ValueError("Registered source object size differs")
        return data

    _provider_model_bytes(
        operations,
        transports,
        outcomes,
        idx["research_budget_reservations"],
        idx["research_budget_settlements"],
        idx["research_objects"],
        config.model_binding,
        read_bytes,
    )
    if not provider_attempt_stopped(session, actual, attempt, settings):
        raise ValueError("Actual provider containment unresolved")

    def completed(member):
        transport = _exact(
            transports,
            lambda t: t["operation_id"] == member["operation_id"],
            "Source transport differs",
        )
        outcome = _exact(
            outcomes,
            lambda o: o["transport_id"] == transport["id"],
            "Source outcome differs",
        )
        return outcome, RDProviderChatResponse.model_validate_json(
            raw(
                idx["research_objects"][outcome["response_object_id"]], "model_response"
            )
        )

    publication = _exact(
        idx["factor_source_publications"],
        lambda p: p["start_id"] == saved["start_id"],
        "Source publication differs",
    )
    reservation = idx["research_budget_reservations"][
        publication["budget_reservation_id"]
    ]
    settlement = _exact(
        idx["research_budget_settlements"],
        lambda s: s["reservation_id"] == reservation["id"],
        "Source publication charge missing",
    )
    if (
        reservation["purpose"] != "object_storage"
        or reservation["state"] != "settled"
        or reservation["amounts"]["storage_bytes"] < publication["peak_bytes"]
        or settlement["outcome"] != "conservative_unknown"
        or settlement["charge"] != reservation["amounts"]
    ):
        raise ValueError("Source publication conservative charge differs")
    from backend.app.rdagent_seed_services import resolve_seed_parent_under_native_guard
    from backend.app.research_workflow_models import ResearchWorkflowModelConsumption

    consumed = {
        row["id"]: row
        for row in loader.read(
            ResearchWorkflowModelConsumption,
            stage_id=config.workflow_inputs.binding.stage_id,
        )
    }
    first = members[0]
    operation = operations[first["operation_id"]]
    transport = _exact(
        transports,
        lambda t: t["operation_id"] == operation["id"],
        "First workflow transport differs",
    )
    consumption = _exact(
        consumed,
        lambda c: c["operation_id"] == operation["id"],
        "First workflow inclusion missing",
    )
    seed = (
        None
        if config.workflow_parent is None
        else resolve_seed_parent_under_native_guard(
            session, actual.owner_id, config.workflow_parent.factor_version_id, settings
        )
    )
    _guidance(
        config,
        saved,
        idx["factor_upstream_calls"][saved["call_id"]],
        first,
        operation,
        transport,
        consumption,
        raw(idx["research_objects"][operation["request_object_id"]], "model_request"),
        seed,
    )
    _validate_source(
        saved,
        idx,
        idx,
        {str(actual.id): config},
        link,
        _exact,
        raw,
        completed,
        {saved["call_id"]: members},
        read_bytes,
        parse_responses=parse_selected_provider_responses,
        workflow=True,
    )
    require_native_storage_guard(settings.storage_root)
