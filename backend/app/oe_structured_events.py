"""Normal027 fixed event handlers; ACKs follow actual transaction commits.

No generic callback registry or caller-selected execution implementation exists.
The socket owner authenticates each actual channel; this layer rechecks that
nonserializable peer at every domain boundary. Checkpoint handlers retain opaque actual native files through separately charged
inventory/chunk/completion records; no reconstructed files substitute for them.
"""

import hashlib
import hmac
from uuid import UUID

from sqlalchemy import select

from backend.app.core.maintenance import shared_lifecycle
from backend.app.factor_native_io import native_storage_guard
from backend.app.oe_structured_checkpoint_models import (
    OEStructuredCheckpointChunk,
    OEStructuredCheckpointCompletion,
    OEStructuredCheckpointUpload,
)
from backend.app.oe_structured_checkpoint_services import (
    prepare_checkpoint,
    publish_checkpoint,
)
from backend.app.oe_structured_checkpoint_upload import (
    upload_begin,
    upload_chunk,
    upload_finish,
)
from backend.app.oe_structured_controller_context import (
    require_controller_context,
    scoped,
)
from backend.app.oe_structured_engine_services import fail
from backend.app.oe_structured_evaluation_services import (
    execute_evaluation,
    read_evaluation_response,
    register_evaluation,
)
from backend.app.oe_structured_event_wire import event_reply, reread_event
from backend.app.oe_structured_models import (
    OEStructuredCandidate,
    OEStructuredProviderExchange,
)
from backend.app.oe_structured_placement_services import record_placement
from backend.app.oe_structured_provider_evidence import read_completed_response
from backend.app.oe_structured_provider_operation import require_provider_operation
from backend.app.oe_structured_rendering_services import record_rendering
from backend.app.oe_structured_sampling_services import register_sampling
from backend.app.oe_structured_terminal_services import (
    register_terminal_application,
    register_worker_outcome,
)
from backend.app.oe_structured_worker_descriptor import require_worker_descriptor
from backend.app.research_lifecycle import neutral_transaction
from packages.factors.contracts import canonical_bytes, digest
from packages.openevolve_structured.contracts import EvaluationGrant
from packages.openevolve_structured.host_events import WorkerDescriptor
from packages.openevolve_structured.provider_contracts import RenderedPrompt
from packages.openevolve_structured.provider_response import parse_response
from runtimes.openevolve_structured_host.peer import (
    ControllerBinding,
    WorkerBinding,
    require_peer,
)


def _fields(payload, names):
    if type(payload) is not dict or set(payload) != set(names):
        fail("oe_structured_event_payload")
    return payload


def context_ticket(ticket_key, context):
    if type(ticket_key) is not bytes or len(ticket_key) != 32:
        fail("oe_structured_event_key")
    return hmac.new(
        ticket_key,
        b"oe-structured-context-v1\0" + canonical_bytes(context),
        hashlib.sha256,
    ).hexdigest()


def _peer(event, fence, peer):
    require_peer(
        peer,
        fence,
        admission_id=event.admission_id,
        epoch_id=event.epoch_id,
        intent_id=peer.binding.intent_id
        if type(peer.binding) is WorkerBinding
        else None,
    )


def _proposal(session, fence, event, settings, peer):
    payload = event.payload
    sampling = require_worker_descriptor(
        session, fence, payload["grant"], settings, peer=peer
    )
    bound = require_provider_operation(session, fence, sampling.id, settings, peer=peer)
    exchange = scoped(
        session.scalar(
            select(OEStructuredProviderExchange).filter_by(
                iteration_intent_id=sampling.id
            )
        ),
        fence,
    )
    request, response = read_completed_response(
        session, fence, exchange, bound.config, settings
    )
    if request != bound.request or exchange.epoch_id != event.epoch_id:
        fail("oe_structured_event_response")
    if event.kind == "proposal_response":
        _fields(payload, {"grant", "response"})
        if payload["response"] != response.content:
            fail("oe_structured_event_response")
    else:
        _fields(
            payload,
            {
                "grant",
                "stage",
                "source",
                "candidate_sha256",
                "source_bytes",
                "evidence",
            },
        )
        generated = parse_response(
            request=request,
            response=response,
            binding=bound.config.model_binding,
            domain=bound.config.search.domain,
        )
        if (
            payload["stage"] not in {"applied", "enforced"}
            or payload["source"] != generated.candidate_bytes.decode("ascii")
            or payload["candidate_sha256"] != generated.candidate_sha256
            or type(payload["source_bytes"]) is not int
            or payload["source_bytes"] != len(generated.candidate_bytes)
            or payload["evidence"] != "complete_source"
        ):
            fail("oe_structured_event_source")
    return exchange.id, {"acknowledged": True}


def _transaction_event(session, fence, event, settings, peer, ticket_key):
    p = event.payload
    if event.kind == "seed_source":
        _fields(p, {"candidate_sha256"})
        if type(peer.binding) is not ControllerBinding:
            fail("oe_structured_seed_peer")
        current = require_controller_context(
            session, fence, event.epoch_id, settings, peer=peer
        )
        if p["candidate_sha256"] != current.config.search.seed_candidate_sha256:
            fail("oe_structured_seed_source")
        return current.epoch.id, {"acknowledged": True}
    if event.kind == "iteration_intent":
        sampling, token = register_sampling(
            session,
            fence,
            event.epoch_id,
            event.payload_raw,
            settings,
            peer=peer,
            ticket_key=ticket_key,
        )
        parent = scoped(
            session.get(OEStructuredCandidate, sampling.parent_candidate_id), fence
        )
        value = WorkerDescriptor(
            schema_version="mineagent.oe-structured-iteration-grant.v1",
            admission_id=event.admission_id,
            epoch_id=sampling.epoch_id,
            intent_id=sampling.id,
            intent_ticket=token,
            native_iteration=sampling.ordinal,
            parent_candidate_id=parent.id,
            parent_candidate_sha256=parent.candidate_sha256,
            selection_sha256=sampling.selection_sha256,
        )
        return sampling.id, value.model_dump(mode="json")
    if event.kind == "worker_placement":
        row = record_placement(
            session, fence, event.epoch_id, canonical_bytes(p), settings, peer=peer
        )
        return row.id, {
            "placement_receipt_id": str(row.id),
            "epoch_id": str(event.epoch_id),
            "intent_id": str(row.sampling_intent_id),
            "worker_namespace_pid": p["worker_namespace_pid"],
            "worker_start_ticks": p["worker_start_ticks"],
        }
    if event.kind == "worker_started":
        _fields(p, {"grant"})
        row = require_worker_descriptor(session, fence, p["grant"], settings, peer=peer)
        return row.id, {"acknowledged": True}
    if event.kind == "rendered_prompt":
        _fields(p, {"grant", "projection", "rendered_sha256"})
        sampling = require_worker_descriptor(
            session, fence, p["grant"], settings, peer=peer
        )
        rendered = RenderedPrompt.model_validate_json(canonical_bytes(p["projection"]))
        if digest(rendered) != p["rendered_sha256"]:
            fail("oe_structured_rendered_digest")
        intent, context = record_rendering(
            session, fence, sampling.id, canonical_bytes(rendered), settings, peer=peer
        )
        return intent.id, {
            "context": context.model_dump(mode="json"),
            "context_ticket": context_ticket(ticket_key, context),
        }
    if event.kind in {"proposal_response", "proposal_source"}:
        return _proposal(session, fence, event, settings, peer)
    if event.kind == "worker_outcome":
        row = register_worker_outcome(
            session, fence, event.payload_raw, settings, peer=peer
        )
        return row.id, {"acknowledged": True}
    if event.kind == "evaluation_complete":
        _fields(p, {"grant", "objective_receipt_id", "eligible", "metrics"})
        grant = EvaluationGrant.model_validate_json(canonical_bytes(p["grant"]))
        if grant.epoch_id != event.epoch_id:
            fail("oe_structured_evaluation_epoch")
        response = read_evaluation_response(
            session, fence, grant, settings, peer=peer, ticket_key=ticket_key
        )
        expected = (
            {"combined_score": response.combined_score} if response.eligible else {}
        )
        if (
            p["objective_receipt_id"] != str(response.objective_receipt_id)
            or type(p["eligible"]) is not bool
            or p["eligible"] != response.eligible
            or canonical_bytes(p["metrics"]) != canonical_bytes(expected)
        ):
            fail("oe_structured_evaluation_ack")
        return response.objective_receipt_id, {
            "acknowledged": True,
            "objective_receipt_id": str(response.objective_receipt_id),
        }
    if event.kind == "terminal_applied":
        row = register_terminal_application(
            session, fence, event.epoch_id, event.payload_raw, settings, peer=peer
        )
        # The publisher validated these exact original bytes against actual results.
        return row.id, {"frontier": p["iteration"], "state_sha256": p["state_sha256"]}
    if event.kind == "checkpoint_prepare":
        row = prepare_checkpoint(
            session, fence, event.epoch_id, event.payload_raw, settings, peer=peer
        )
        return row.id, {
            "token": str(row.id),
            "path": "/work/checkpoints/" + str(row.id),
        }
    if event.kind == "checkpoint_publish":
        row = publish_checkpoint(
            session, fence, event.epoch_id, event.payload_raw, settings, peer=peer
        )
        return row.id, {
            "published": True,
            "frontier": p["frontier"],
            "state_sha256": row.state_sha256,
        }
    fail("oe_structured_event_not_installed")


def apply_event(factory, fence, event, settings, *, peer, ticket_key):
    """Actual durable handler; nothing is ACKed before its transaction commits."""
    event = reread_event(event)
    if type(ticket_key) is not bytes or len(ticket_key) != 32:
        fail("oe_structured_event_key")
    _peer(event, fence, peer)
    with shared_lifecycle(factory.kw["bind"], settings.storage_root):
        if event.kind == "candidate_intent":
            p = _fields(event.payload, {"grant", "upstream_program_id", "source"})
            if (
                type(p["source"]) is not str
                or type(p["upstream_program_id"]) is not str
                or str(UUID(p["upstream_program_id"])) != p["upstream_program_id"]
            ):
                fail("oe_structured_candidate_event")
            if len(p["source"].encode("utf-8")) > 32768:
                fail("oe_structured_candidate_bound")
            with (
                native_storage_guard(settings.storage_root),
                neutral_transaction(
                    factory, fence, resource_scope="factor_numeric"
                ) as session,
            ):
                if p["grant"] is None:
                    if type(peer.binding) is not ControllerBinding:
                        fail("oe_structured_seed_peer")
                else:
                    require_worker_descriptor(
                        session, fence, p["grant"], settings, peer=peer
                    )
            grant = register_evaluation(
                factory,
                fence,
                event.epoch_id,
                UUID(p["upstream_program_id"]),
                p["source"].encode("utf-8"),
                settings,
                peer=peer,
                ticket_key=ticket_key,
            )
            receipt, result = grant.execution_id, grant.model_dump(mode="json")
        elif event.kind == "execute_evaluation":
            p = _fields(event.payload, {"grant"})
            grant = EvaluationGrant.model_validate_json(canonical_bytes(p["grant"]))
            if grant.epoch_id != event.epoch_id:
                fail("oe_structured_evaluation_epoch")
            response = execute_evaluation(
                factory, fence, grant, settings, peer=peer, ticket_key=ticket_key
            )
            receipt, result = (
                response.objective_receipt_id,
                response.model_dump(mode="json"),
            )
        elif event.kind in {
            "checkpoint_upload_begin",
            "checkpoint_upload_chunk",
            "checkpoint_upload_finish",
        }:
            callback = {
                "checkpoint_upload_begin": upload_begin,
                "checkpoint_upload_chunk": upload_chunk,
                "checkpoint_upload_finish": upload_finish,
            }[event.kind]
            with native_storage_guard(settings.storage_root):
                result = callback(
                    factory, fence, event.epoch_id, event.payload, settings, peer=peer
                )
                with neutral_transaction(
                    factory, fence, resource_scope="factor_numeric"
                ) as session:
                    require_controller_context(
                        session, fence, event.epoch_id, settings, peer=peer
                    )
                    upload = scoped(
                        session.scalar(
                            select(OEStructuredCheckpointUpload).filter_by(
                                intent_id=UUID(result["token"])
                            )
                        ),
                        fence,
                    )
                    if upload.epoch_id != event.epoch_id:
                        fail("oe_structured_checkpoint_epoch")
                    if event.kind == "checkpoint_upload_begin":
                        receipt = upload.id
                    elif event.kind == "checkpoint_upload_chunk":
                        chunk = scoped(
                            session.scalar(
                                select(OEStructuredCheckpointChunk).filter_by(
                                    upload_id=upload.id, sequence=result["sequence"]
                                )
                            ),
                            fence,
                        )
                        if chunk.chunk_sha256 != result["sha256"]:
                            fail("oe_structured_checkpoint_chunk")
                        receipt = chunk.id
                    else:
                        completion = scoped(
                            session.get(
                                OEStructuredCheckpointCompletion,
                                UUID(result["receipt_id"]),
                            ),
                            fence,
                        )
                        if (
                            completion.upload_id != upload.id
                            or completion.epoch_id != event.epoch_id
                        ):
                            fail("oe_structured_checkpoint_completion")
                        receipt = completion.id
        else:
            with (
                native_storage_guard(settings.storage_root),
                neutral_transaction(
                    factory, fence, resource_scope="factor_numeric"
                ) as session,
            ):
                receipt, result = _transaction_event(
                    session, fence, event, settings, peer, ticket_key
                )
                _peer(event, fence, peer)
        _peer(event, fence, peer)
        return event_reply(event, receipt, result)
