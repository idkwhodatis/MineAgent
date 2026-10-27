"""Configured controller model requests; secrets/endpoints never enter image IPC."""

from packages.rdagent_provider.codec import canonical_bytes, validate_response
from packages.rdagent_provider.contracts import (
    RDModelBinding,
    RDProviderChatRequest,
    RDProviderChatResponse,
    RDProviderEmbeddingRequest,
    RDProviderEmbeddingResponse,
)
from runtimes.rdagent_operational.client import OperationalHost, _uuid
from runtimes.rdagent_operational.rpc import RPCError


class ProviderOperationalHost(OperationalHost):
    def __init__(self, channel, binding):
        super().__init__(channel)
        self.binding = RDModelBinding.model_validate(binding)

    def _submit(self, nonce, protocol, request, method):
        if nonce not in self.pending:
            raise RPCError("request_nonce")
        exchange, relation = self.pending.pop(nonce)
        result = self.channel.call(
            method,
            {
                "exchange_id": str(exchange),
                "idempotency_key": str(nonce),
                "relation": relation,
                "protocol": protocol,
                "request_utf8": canonical_bytes(protocol, request).decode("utf-8"),
            },
        )
        _uuid(result["operation_id"])
        return result["response"]

    def _chat_request(self, messages, response_format):
        return RDProviderChatRequest(
            model_profile_id=self.binding.profile_id,
            model_binding_sha256=self.binding.sha256,
            model=self.binding.chat_model,
            messages=tuple(messages),
            max_output_tokens=self.binding.max_output_tokens,
            response_format=response_format,
        )

    def chat(self, logical_id, *, messages, response_format):
        request = self._chat_request(messages, response_format)
        response = RDProviderChatResponse.model_validate(
            self._submit(logical_id, request.schema_version, request, "submit_chat")
        )
        response = validate_response(request.schema_version, request, response)
        return response.content, response.finish_reason

    def embedding(self, logical_id, *, inputs):
        request = RDProviderEmbeddingRequest(
            model_profile_id=self.binding.profile_id,
            model_binding_sha256=self.binding.sha256,
            model=self.binding.embedding_model,
            inputs=tuple(inputs),
            dimensions=self.binding.embedding_dimensions,
        )
        response = RDProviderEmbeddingResponse.model_validate(
            self._submit(
                logical_id, request.schema_version, request, "submit_embedding"
            )
        )
        response = validate_response(request.schema_version, request, response)
        return [list(vector) for vector in response.vectors]

    def count_tokens(self, messages):
        # Planning only. Host reservation and actual provider usage are separate.
        request = self._chat_request(messages, None)
        return max(1, (len(canonical_bytes(request.schema_version, request)) + 2) // 3)
