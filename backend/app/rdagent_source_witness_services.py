"""Actual host reread/parse witness, not a caller-provided parser declaration.

Only bounded metadata is duplicated. Raw envelopes remain in their existing
charged immutable objects; normalized JSON is deterministically recomputable.
"""

import hashlib
from uuid import UUID, uuid4

from sqlalchemy import select

from backend.app.factor_models import FactorObject, FactorVersion
from backend.app.factor_services import verify_version
from backend.app.rdagent_controller_services import require_recorded_controller
from backend.app.rdagent_models import (
    FactorGenerationOutcome,
    FactorGenerationSource,
    FactorResponseAssembly,
    FactorResponseAssemblyPart,
    FactorUpstreamCall,
    FactorUpstreamCallOutcome,
)
from backend.app.rdagent_services import _scope, _scope_row, _verify_response_parts
from backend.app.rdagent_source_witness_models import (
    FactorSourceReadPart,
    FactorSourceReadWitness,
)
from backend.app.research_broker_models import ResearchModelOperation
from backend.app.research_lifecycle import require_neutral_transaction
from backend.app.research_object_services import read_research_object
from backend.app.services import DomainError
from packages.rdagent_adapter.host_evidence import (
    ModelResponsePart,
    ResponseAssemblyDocument,
)
from packages.rdagent_adapter.response_parser import (
    UPSTREAM_FRAGMENT_SHA256,
    parse_selected_responses,
)
from packages.research_runtime.engine_broker_contracts import parse_request
from packages.research_runtime.process_contracts import digest

PARSER_PROFILE = "mineagent.rdagent-source-parser.v1"


def _error():
    return DomainError(
        "factor_source_read_witness_drift",
        "Exact retained response parsing does not close source identity",
        409,
    )


def _parse_responses(responses):
    from packages.rdagent_provider.contracts import RDProviderChatResponse

    if responses and type(responses[0]) is RDProviderChatResponse:
        from packages.rdagent_provider.source_parser import (
            parse_selected_provider_responses,
        )

        return parse_selected_provider_responses(responses)
    return parse_selected_responses(responses)


def require_exact_extracted_source(responses, document):
    try:
        parsed = _parse_responses(tuple(responses))
    except ValueError as error:
        raise _error() from error
    if (parsed.source, parsed.source_sha256) != (
        document.source,
        document.source_sha256,
    ):
        raise _error()
    return parsed


def _owned(session, model, owner, identity):
    row = session.scalar(select(model).filter_by(owner_id=owner, id=identity))
    if row is None:
        raise _error()
    return row


def derive_source_read_witness(session, fence, *, source_id, settings):
    """No caller supplied content/hash/finish reason enters this derivation."""
    require_neutral_transaction(session, fence, resource_scope="factor_objects")
    from backend.app.rdagent_services import require_research_spec

    _, config = require_research_spec(session, fence)
    provider = config.schema_version == "mineagent.factor-research.v2"
    source = _scope_row(session, FactorGenerationSource, source_id, fence, lock=True)
    assembly = _scope_row(session, FactorResponseAssembly, source.assembly_id, fence)
    call = _scope_row(session, FactorUpstreamCall, source.call_id, fence)
    if (
        call.phase,
        call.kind,
        call.start_id,
        call.context_id,
        assembly.call_id,
        assembly.start_id,
        assembly.context_id,
    ) != (
        "generation",
        "chat",
        source.start_id,
        source.context_id,
        call.id,
        source.start_id,
        source.context_id,
    ):
        raise _error()
    closed = session.scalar(
        select(FactorUpstreamCallOutcome).filter_by(**_scope(fence), call_id=call.id)
    )
    if closed is None or closed.status != "completed":
        raise _error()
    raw = read_research_object(
        session,
        fence,
        assembly.object_id,
        "factor_response_assembly",
        assembly.object_sha256,
        settings,
    )
    document = ResponseAssemblyDocument.model_validate_json(raw)
    if document.call_id != call.id:
        raise _error()
    launch = require_recorded_controller(
        session, fence, document.orchestrator_admission_id
    )
    if closed.controller_launch_id != launch.id:
        raise _error()
    parts = session.scalars(
        select(FactorResponseAssemblyPart)
        .filter_by(**_scope(fence), assembly_id=assembly.id)
        .order_by(FactorResponseAssemblyPart.ordinal)
        .limit(7)
    ).all()
    if (
        not 1 <= len(parts) <= 6
        or assembly.part_count != len(parts)
        or [part.ordinal for part in parts] != list(range(len(parts)))
    ):
        raise _error()
    projected = tuple(
        ModelResponsePart(
            member_id=part.member_id,
            outcome_id=part.outcome_id,
            response_object_id=part.response_object_id,
            response_sha256=part.response_sha256,
        )
        for part in parts
    )
    if projected != document.parts:
        raise _error()
    selected, responses = _verify_response_parts(
        session, fence, call, projected, settings
    )
    parsed = _parse_responses(tuple(responses))
    version = _owned(session, FactorVersion, fence.owner_id, source.factor_version_id)
    verify_version(session, fence.owner_id, version, settings)
    obj = _owned(session, FactorObject, fence.owner_id, version.code_object_id)
    if (
        document.source,
        document.source_sha256,
        version.code,
        version.code_sha256,
        source.source_sha256,
        obj.object_kind,
        obj.media_type,
        obj.sha256,
        obj.size_bytes,
    ) != (
        parsed.source,
        parsed.source_sha256,
        parsed.source,
        parsed.source_sha256,
        parsed.source_sha256,
        "code",
        "text/plain",
        parsed.source_sha256,
        parsed.source_size_bytes,
    ):
        raise _error()
    entries = []
    for index, (part, member, response) in enumerate(
        zip(parts, selected, responses, strict=True)
    ):
        operation = _scope_row(
            session, ResearchModelOperation, member.operation_id, fence
        )
        decoder = parse_request
        if provider:
            from packages.rdagent_provider.codec import parse_request as decoder
        request = decoder(member.protocol, operation.request)
        if (
            member.protocol
            != (
                "mineagent.rdagent-provider-chat.v1"
                if provider
                else "mineagent.rdagent-chat.v1"
            )
            or request.kind != "chat"
            or request.response_format is None
            or request.response_format.type != "json_object"
        ):
            raise _error()
        entries.append(
            {
                "assembly_part_id": part.id,
                "member_id": member.id,
                "outcome_id": part.outcome_id,
                "response_object_id": part.response_object_id,
                "response_object_kind": "model_response",
                "response_sha256": part.response_sha256,
                "ordinal": index,
                "finish_reason": response.finish_reason,
                "content_sha256": hashlib.sha256(
                    response.content.encode("utf-8")
                ).hexdigest(),
                "content_size_bytes": len(response.content.encode("utf-8")),
            }
        )
    header = {
        "source_id": source.id,
        "assembly_id": assembly.id,
        "call_id": call.id,
        "controller_launch_id": launch.id,
        "parser_profile": "mineagent.rdagent-provider-source-parser.v1"
        if provider
        else PARSER_PROFILE,
        "parser_source_sha256": UPSTREAM_FRAGMENT_SHA256,
        "source_sha256": parsed.source_sha256,
        "source_size_bytes": parsed.source_size_bytes,
        "source_object_id": obj.id,
        "source_object_kind": "code",
        "raw_text_sha256": parsed.raw_text_sha256,
        "raw_text_bytes": len(parsed.raw_text.encode("utf-8")),
        "normalized_json_sha256": parsed.normalized_json_sha256,
        "normalized_json_bytes": len(parsed.normalized_json.encode("utf-8")),
        "selected_first_ordinal": selected[0].request_ordinal,
        "part_count": len(parts),
    }
    # Canonical bytes use UUID strings; raw/normalized response payloads are NOT
    # duplicated into SQL storage or charged a second time implicitly.
    public = lambda fields: {
        key: str(value) if isinstance(value, UUID) else value
        for key, value in fields.items()
    }
    header["witness_sha256"] = digest(
        {
            "schema_version": "mineagent.source-read-witness.v1",
            "scope": public(_scope(fence)),
            "header": public(header),
            "parts": [public(item) for item in entries],
        }
    )
    return header, entries


def persist_source_read_witness(
    session, fence, *, source_id, settings, allow_create=False
):
    try:
        header, entries = derive_source_read_witness(
            session, fence, source_id=source_id, settings=settings
        )
    except (ValueError, KeyError) as error:
        raise _error() from error
    existing = session.scalar(
        select(FactorSourceReadWitness).filter_by(**_scope(fence), source_id=source_id)
    )
    if existing is not None:
        parts = session.scalars(
            select(FactorSourceReadPart)
            .filter_by(**_scope(fence), witness_id=existing.id)
            .order_by(FactorSourceReadPart.ordinal)
            .limit(7)
        ).all()
        if any(
            getattr(existing, key) != value
            for key, value in {**_scope(fence), **header}.items()
        ) or len(parts) != len(entries):
            raise _error()
        for part, expected in zip(parts, entries, strict=True):
            if part.witness_id != existing.id or any(
                getattr(part, key) != value
                for key, value in {**_scope(fence), **expected}.items()
            ):
                raise _error()
        return existing
    if allow_create is not True:
        raise _error()
    from backend.app.rdagent_boundary_services import require_unobserved_generation

    source = _scope_row(session, FactorGenerationSource, source_id, fence)
    require_unobserved_generation(session, fence, source.start_id)
    if (
        session.scalar(
            select(FactorGenerationOutcome).filter_by(
                **_scope(fence), start_id=source.start_id
            )
        )
        is not None
    ):
        raise _error()
    row = FactorSourceReadWitness(id=uuid4(), **_scope(fence), **header)
    session.add(row)
    session.flush()
    for item in entries:
        session.add(
            FactorSourceReadPart(id=uuid4(), **_scope(fence), witness_id=row.id, **item)
        )
    session.flush()
    return row
