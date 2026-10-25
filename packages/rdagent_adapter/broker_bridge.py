"""Narrow pinned upstream transport seam; identity/accounting remain host-owned.

No default client, provider credentials, local ordinal counter, fallback transport
or cache exists. This module is imported only inside the separate RD runtime.
"""

import json
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError
from rdagent.oai.backend.base import APIBackend as UpstreamAPIBackend
from rdagent.oai.llm_conf import LLM_SETTINGS

from packages.research_runtime.control import BrokerAbort


class HostBroker(Protocol):
    def check_control(self) -> None: ...
    def begin_exchange(self, *, kind: str, phase: str) -> UUID: ...
    def end_exchange(self, exchange_id: UUID, *, succeeded: bool) -> None: ...
    def new_request(self, exchange_id: UUID, *, relation: str) -> UUID: ...
    def chat(
        self, logical_id: UUID, *, messages: list[dict], response_format: dict | None
    ) -> tuple[str, str]: ...
    def embedding(
        self, logical_id: UUID, *, inputs: list[str]
    ) -> list[list[float]]: ...
    def count_tokens(self, messages: list[dict]) -> int: ...
    def semantic_retry(self, exchange_id: UUID, *, reason: str) -> None: ...


_CLIENT = ContextVar("mineagent_rd_host_broker", default=None)
_PHASE = ContextVar("mineagent_rd_model_phase", default=None)
PHASES = frozenset(
    {
        "generation",
        "output_format",
        "critique",
        "final_decision",
        "knowledge_update",
        "knowledge_query",
    }
)


@contextmanager
def model_phase(phase: str):
    if phase not in PHASES or _PHASE.get() is not None:
        raise BrokerAbort("request_drift")
    token = _PHASE.set(phase)
    try:
        yield
    finally:
        _PHASE.reset(token)


_RELATION = ContextVar("mineagent_rd_request_relation", default=None)
_EXCHANGE = ContextVar("mineagent_rd_upstream_call_context", default=None)


@contextmanager
def use_host_broker(client: HostBroker):
    if _CLIENT.get() is not None:
        raise BrokerAbort("request_drift")
    token = _CLIENT.set(client)
    try:
        client.check_control()
        yield
    finally:
        _CLIENT.reset(token)


def _client():
    client = _CLIENT.get()
    if client is None:
        raise BrokerAbort("fence_lost")
    client.check_control()
    return client


class APIBackend(UpstreamAPIBackend):
    def __init__(self, *args, **kwargs):
        _client()
        if (
            args
            or set(kwargs)
            - {
                "use_chat_cache",
                "dump_chat_cache",
                "use_embedding_cache",
                "dump_embedding_cache",
            }
            or any(type(value) is not bool for value in kwargs.values())
        ):
            raise BrokerAbort("request_drift")
        # Upstream callers request cache use; it is deliberately disabled for
        # this profile. Completed replay is exclusively the durable host broker.
        super().__init__(
            use_chat_cache=False,
            dump_chat_cache=False,
            use_embedding_cache=False,
            dump_embedding_cache=False,
        )

    def supports_response_schema(self):
        return False

    def _calculate_token_from_messages(self, messages):
        return _client().count_tokens(messages)

    def _try_create_chat_completion_or_embedding(
        self, max_retry=10, chat_completion=False, embedding=False, *args, **kwargs
    ):
        """Transport retries/sleep belong to the cancellable durable broker.

        Upstream generation, JSON parsing, task repair and automatic continuation
        remain intact. Each fresh outer call gets a host call-context identity;
        EVERY inner invocation gets a distinct immutable broker request operation.
        Changed continuation/repair messages never reuse an operation request hash.
        """
        client = _client()
        if chat_completion == embedding or _EXCHANGE.get() is not None:
            raise BrokerAbort("request_drift")
        phase = _PHASE.get()
        if phase is None or (
            embedding and phase not in {"knowledge_query", "knowledge_update"}
        ):
            raise BrokerAbort("request_drift")
        exchange_id = client.begin_exchange(
            kind="chat" if chat_completion else "embedding", phase=phase
        )
        token = _EXCHANGE.set(exchange_id)
        relation_token = _RELATION.set("initial")
        succeeded = False
        try:
            retries = (
                LLM_SETTINGS.max_retry
                if LLM_SETTINGS.max_retry is not None
                else max_retry
            )
            if type(retries) is not int or not 1 <= retries <= 10:
                raise BrokerAbort("request_drift")
            for attempt in range(retries):
                client.check_control()
                try:
                    result = (
                        self._create_chat_completion_auto_continue(*args, **kwargs)
                        if chat_completion
                        else self._create_embedding_with_cache(*args, **kwargs)
                    )
                    break
                except (json.JSONDecodeError, ValidationError) as error:
                    # Preserve upstream response parsing/repair, but never retry
                    # transport exceptions or silently truncate an embedding.
                    if not chat_completion or attempt + 1 == retries:
                        raise
                    if isinstance(error, json.JSONDecodeError) and hasattr(
                        error, "message"
                    ):
                        kwargs["add_json_in_prompt"] = True
                    _RELATION.set("semantic_repair")
                    client.semantic_retry(
                        exchange_id,
                        reason="invalid_json"
                        if isinstance(error, json.JSONDecodeError)
                        else "invalid_response_schema",
                    )
            client.check_control()
            succeeded = True
            return result
        finally:
            _RELATION.reset(relation_token)
            _EXCHANGE.reset(token)
            client.end_exchange(exchange_id, succeeded=succeeded)

    def _create_chat_completion_inner_function(
        self, messages, response_format=None, *args, **kwargs
    ):
        exchange_id = _EXCHANGE.get()
        if (
            exchange_id is None
            or args
            or kwargs
            or (
                response_format is not None
                and response_format != {"type": "json_object"}
            )
        ):
            raise BrokerAbort("request_drift")
        # Never infer identity from prompt bytes or backend instance lifetime.
        client = _client()
        logical_id = client.new_request(exchange_id, relation=_RELATION.get())
        if not isinstance(logical_id, UUID):
            raise BrokerAbort("publication_failed")
        result = client.chat(
            logical_id, messages=messages, response_format=response_format
        )
        _RELATION.set("continuation")
        return result

    def _create_embedding_inner_function(self, input_content_list):
        exchange_id = _EXCHANGE.get()
        if exchange_id is None:
            raise BrokerAbort("request_drift")
        client = _client()
        logical_id = client.new_request(exchange_id, relation="initial")
        if not isinstance(logical_id, UUID):
            raise BrokerAbort("publication_failed")
        return client.embedding(logical_id, inputs=input_content_list)
