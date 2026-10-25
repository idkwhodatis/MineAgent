"""One bounded Unix request per admitted mutation operation; no provider fallback.

The host must independently authenticate SO_PEERCRED, the registered unit, current
fence and exact context ticket. Possession of these client-side values is not
host authority. This module does not account or allocate budget locally.
"""

import asyncio
import hashlib
import json
import socket
import struct
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from uuid import UUID

from openevolve.llm.base import LLMInterface

from packages.research_runtime.control import BrokerAbort

from .contracts import SearchOperationContext

MAX_REQUEST_BYTES = 768 * 1024
MAX_REPLY_BYTES = 512 * 1024
MAX_RESPONSE_BYTES = 32 * 1024
SOCKET_PATH = "/ipc/broker.sock"


@dataclass(frozen=True)
class OperationGrant:
    admission_id: UUID
    context: SearchOperationContext
    expected_request_sha256: str
    process_ticket: str = field(repr=False)
    context_ticket: str = field(repr=False)

    def __post_init__(self):
        if (
            type(self.admission_id) is not UUID
            or type(self.context) is not SearchOperationContext
        ):
            raise ValueError("Typed operation grant required")
        for ticket in (
            self.process_ticket,
            self.context_ticket,
            self.expected_request_sha256,
        ):
            if (
                type(ticket) is not str
                or len(ticket) != 64
                or any(c not in "0123456789abcdef" for c in ticket)
            ):
                raise ValueError("Invalid operation grant")


_GRANT: ContextVar[OperationGrant | None] = ContextVar(
    "mineagent_openevolve_operation", default=None
)


@contextmanager
def bind_operation(grant: OperationGrant):
    if type(grant) is not OperationGrant or _GRANT.get() is not None:
        raise BrokerAbort("request_drift")
    token = _GRANT.set(grant)
    try:
        yield
    finally:
        _GRANT.reset(token)


def _canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BrokerAbort("request_drift")
        result[key] = value
    return result


def _read_exact(sock, count):
    chunks = bytearray()
    while len(chunks) < count:
        chunk = sock.recv(count - len(chunks))
        if not chunk:
            raise BrokerAbort("uncertain_transport")
        chunks.extend(chunk)
    return bytes(chunks)


def _exchange(payload):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
            peer.settimeout(65)
            peer.connect(SOCKET_PATH)
            peer.sendall(struct.pack("!I", len(payload)) + payload)
            size = struct.unpack("!I", _read_exact(peer, 4))[0]
            if not 0 < size <= MAX_REPLY_BYTES:
                raise BrokerAbort("request_drift")
            return _read_exact(peer, size)
    except (OSError, ValueError):
        # An interrupted reply is not proof of non-submission. Never retry here.
        raise BrokerAbort("uncertain_transport") from None


class MineAgentLLM(LLMInterface):
    def __init__(self, config):
        if (
            config.name != "openevolve-fixture.v1"
            or config.api_key is not None
            or config.api_base is not None
            or config.provider is not None
            or config.retries != 0
            or config.max_tokens != 32768
            or config.temperature != 0
            or config.manual_mode not in (None, False)
        ):
            raise BrokerAbort("request_drift")
        self.model = config.name
        self.last_usage = None

    async def generate(self, prompt: str, **kwargs) -> str:
        # Actual configured OpenEvolve uses generate_with_context. Do not invent
        # a default system prompt or an unbound purpose for convenience calls.
        raise BrokerAbort("request_drift")

    async def generate_with_context(
        self, system_message: str, messages: list[dict[str, str]], **kwargs
    ) -> str:
        grant = _GRANT.get()
        if (
            grant is None
            or kwargs
            or type(system_message) is not str
            or type(messages) is not list
            or len(messages) != 1
        ):
            raise BrokerAbort("request_drift")
        message = messages[0]
        if (
            type(message) is not dict
            or set(message) != {"role", "content"}
            or message["role"] != "user"
        ):
            raise BrokerAbort("request_drift")
        if any(
            type(x) is not str or not x or len(x) > 262144
            for x in (system_message, message["content"])
        ):
            raise BrokerAbort("budget_exhausted")
        request = {
            "kind": "chat",
            "model": self.model,
            "messages": [{"role": "system", "content": system_message}, message],
            "max_output_tokens": 32768,
            "temperature_millionths": 0,
        }
        request_sha = hashlib.sha256(_canonical(request)).hexdigest()
        if request_sha != grant.expected_request_sha256:
            raise BrokerAbort("request_drift")
        context = grant.context.model_dump(mode="json")
        context_sha = hashlib.sha256(_canonical(context)).hexdigest()
        payload = _canonical(
            {
                "schema_version": "mineagent.openevolve-broker-call.v1",
                "admission_id": str(grant.admission_id),
                "process_ticket": grant.process_ticket,
                "context_ticket": grant.context_ticket,
                "context": context,
                "request": request,
                "request_sha256": request_sha,
            }
        )
        if len(payload) > MAX_REQUEST_BYTES:
            raise BrokerAbort("budget_exhausted")
        reply_bytes = await asyncio.to_thread(_exchange, payload)
        try:
            reply = json.loads(
                reply_bytes,
                object_pairs_hook=_unique_pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
            if type(reply) is not dict or set(reply) != {
                "schema_version",
                "request_sha256",
                "context_sha256",
                "receipt_id",
                "content",
                "usage",
            }:
                raise ValueError()
            if (
                reply["schema_version"] != "mineagent.openevolve-broker-result.v1"
                or reply["request_sha256"] != request_sha
                or reply["context_sha256"] != context_sha
            ):
                raise ValueError()
            if str(UUID(reply["receipt_id"])) != reply["receipt_id"]:
                raise ValueError()
            content, usage = reply["content"], reply["usage"]
            if (
                type(content) is not str
                or not content
                or len(content.encode("utf-8")) > MAX_RESPONSE_BYTES
                or "\0" in content
            ):
                raise ValueError()
            if (
                type(usage) is not dict
                or set(usage) != {"prompt_tokens", "completion_tokens", "total_tokens"}
                or any(
                    type(v) is not int or not 0 <= v <= 2**53 - 1
                    for v in usage.values()
                )
            ):
                raise ValueError()
            if (
                usage["total_tokens"]
                != usage["prompt_tokens"] + usage["completion_tokens"]
            ):
                raise ValueError()
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise BrokerAbort("request_drift") from None
        self.last_usage = dict(usage)
        return content


def init_client(config):
    return MineAgentLLM(config)
