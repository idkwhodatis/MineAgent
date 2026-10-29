"""Normal027 worker-to-host chat socket, never a network/provider client.

The host independently verifies SO_PEERCRED, exact tickets, current scope and
immutable rendered context before any SDK admission. This client grants nothing.
"""

import asyncio
import json
import socket
import struct
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Lock
from uuid import UUID

from openevolve.llm.base import LLMInterface

from packages.factors.contracts import canonical_bytes, digest
from packages.research_runtime.control import BrokerAbort

from .broker_client import _read_exact, _unique_pairs
from .candidate import TypedFrozenCandidateDomain, _raw_fields
from .contracts import SearchOperationContext
from .provider_bootstrap import ProviderStructuredBootstrap
from .provider_contracts import (
    OEChatBinding,
    OEChatRequest,
    OEChatResponse,
    require_request_binding,
)
from .provider_response import parse_response


@dataclass(frozen=True)
class ProviderOperationGrant:
    admission_id: UUID
    context: SearchOperationContext
    request: OEChatRequest
    binding: OEChatBinding
    domain: TypedFrozenCandidateDomain
    process_ticket: str = field(repr=False)
    context_ticket: str = field(repr=False)

    def __post_init__(self):
        if (
            type(self.admission_id) is not UUID
            or type(self.context) is not SearchOperationContext
            or type(self.domain) is not TypedFrozenCandidateDomain
        ):
            raise ValueError("Exact normal provider operation required")
        request = require_request_binding(self.request, self.binding)
        context = SearchOperationContext.model_validate(_raw_fields(self.context))
        domain = TypedFrozenCandidateDomain.model_validate(_raw_fields(self.domain))
        if (
            request.context_sha256 != digest(context)
            or request.candidate_domain_sha256 != digest(domain)
            or request.parent_candidate_sha256 != context.parent_candidate_sha256
            or request.execution_binding_sha256 != context.execution_binding_sha256
            or request.rendered_context_sha256 != context.rendered_context_sha256
        ):
            raise ValueError("Provider request/context/domain differ")
        for value in (self.process_ticket, self.context_ticket):
            if (
                type(value) is not str
                or len(value) != 64
                or any(c not in "0123456789abcdef" for c in value)
            ):
                raise ValueError("Exact context/process ticket required")


_GRANT = ContextVar("oe_structured_provider_operation", default=None)


@dataclass
class _OperationUse:
    used: bool = False
    lock: object = field(default_factory=Lock, repr=False)

    def consume(self):
        with self.lock:
            if self.used:
                raise BrokerAbort("request_drift")
            self.used = True


# asyncio copies ContextVar bindings into tasks, not mutations of bound values.
# Keep one host-grant consumption cell so separate tasks cannot replay the grant.
_USED = ContextVar("oe_structured_provider_operation_used", default=None)


@contextmanager
def bind_operation(grant):
    if type(grant) is not ProviderOperationGrant or _GRANT.get() is not None:
        raise BrokerAbort("request_drift")
    grant.__post_init__()
    token, used = _GRANT.set(grant), _USED.set(_OperationUse())
    try:
        yield
    finally:
        _USED.reset(used)
        _GRANT.reset(token)


def _bootstrap():
    from .observer_transport import _bootstrap as read_bootstrap

    value = read_bootstrap()
    from .workflow_bootstrap import WorkflowStructuredBootstrap

    if type(value) not in {ProviderStructuredBootstrap, WorkflowStructuredBootstrap}:
        raise BrokerAbort("request_drift")
    return value


def _exchange(payload):
    if not 0 < len(payload) <= 524288:
        raise BrokerAbort("budget_exhausted")
    try:
        remaining = min(65.0, _bootstrap().remaining(time.time()))
        deadline = time.monotonic() + remaining
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
            peer.settimeout(remaining)
            peer.connect("/ipc/broker.sock")
            peer.sendall(struct.pack("!I", len(payload)) + payload)
            size = struct.unpack("!I", _read_exact(peer, 4, deadline))[0]
            if not 0 < size <= 262144:
                raise BrokerAbort("request_drift")
            return _read_exact(peer, size, deadline)
    except (OSError, ValueError):
        raise BrokerAbort("uncertain_transport") from None


class ProviderLLM(LLMInterface):
    def __init__(self, config):
        binding = _bootstrap().model_binding
        if (
            config.name != binding.model
            or config.max_tokens != binding.max_output_tokens
            or config.api_key is not None
            or config.api_base is not None
            or config.provider is not None
            or config.retries != 0
            or config.temperature != 0
            or config.manual_mode not in (None, False)
        ):
            raise BrokerAbort("request_drift")
        self.binding = binding
        self.last_usage = None

    async def generate(self, prompt, **kwargs):
        raise BrokerAbort("request_drift")

    async def generate_with_context(self, system_message, messages, **kwargs):
        grant = _GRANT.get()
        if grant is None or _USED.get() is None or _USED.get().used or kwargs:
            raise BrokerAbort("request_drift")
        grant.__post_init__()
        request = grant.request
        initial = _bootstrap()
        if (
            initial.admission_id != grant.admission_id
            or initial.epoch_id != grant.context.epoch_id
            or initial.spec.owner_id != grant.context.owner_id
            or initial.spec.run_id != grant.context.run_id
            or initial.spec.domain != grant.domain
            or initial.spec.execution_binding_sha256
            != grant.context.execution_binding_sha256
        ):
            raise BrokerAbort("request_drift")
        if (
            self.binding != grant.binding
            or type(system_message) is not str
            or system_message != request.messages[0].content
            or messages != [request.messages[1].model_dump(mode="json")]
            or type(messages) is not list
            or initial.model_binding != self.binding
        ):
            raise BrokerAbort("request_drift")
        payload = canonical_bytes(
            {
                "schema_version": "mineagent.oe-structured-provider-broker-call.v1",
                "admission_id": str(grant.admission_id),
                "process_ticket": grant.process_ticket,
                "context_ticket": grant.context_ticket,
                "context": grant.context.model_dump(mode="json"),
                "request": request.model_dump(mode="json"),
                "request_sha256": request.sha256,
            }
        )
        _USED.get().consume()  # Atomic across tasks/threads; an interrupted ACK is final.
        raw = await asyncio.to_thread(_exchange, payload)
        try:
            value = json.loads(raw, object_pairs_hook=_unique_pairs)
            if (
                type(value) is not dict
                or canonical_bytes(value) != raw
                or set(value)
                != {
                    "schema_version",
                    "request_sha256",
                    "context_sha256",
                    "receipt_id",
                    "response",
                }
                or value["schema_version"]
                != "mineagent.oe-structured-provider-broker-result.v1"
                or value["request_sha256"] != request.sha256
                or value["context_sha256"] != digest(grant.context)
                or str(UUID(value["receipt_id"])) != value["receipt_id"]
            ):
                raise ValueError("committed provider ACK")
            response = OEChatResponse.model_validate_json(
                canonical_bytes(value["response"])
            )
            if response.request_sha256 != request.sha256:
                raise ValueError("response identity")
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise BrokerAbort("request_drift") from None
        # The host already retained/accounted this response. Unknown usage stays
        # unknown; it is not a synthetic zero-cost model call.
        if all(
            v is not None
            for v in (
                response.input_tokens,
                response.output_tokens,
                response.total_tokens,
            )
        ):
            self.last_usage = {
                "prompt_tokens": response.input_tokens,
                "completion_tokens": response.output_tokens,
                "total_tokens": response.total_tokens,
            }
        try:
            proposal = parse_response(
                request=request,
                response=response,
                binding=grant.binding,
                domain=grant.domain,
            )
        except (ValueError, TypeError):
            from .observer import CandidateSourceRejected

            raise CandidateSourceRejected("source_profile") from None
        return proposal.candidate_bytes.decode("utf-8")


def init_client(config):
    return ProviderLLM(config)
