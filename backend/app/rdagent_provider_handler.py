"""Configured actual SDK dispatch; no maintained source or objective substitution."""

import json

from backend.app.rdagent_configured_handler import ConfiguredHandler
from backend.app.rdagent_operational_handler import _id, _keys
from backend.app.rdagent_provider_broker import submit_provider_request
from runtimes.rdagent_operational.rpc import RPCError


class ProviderHandler(ConfiguredHandler):
    def _model(self, peer, method, body):
        _keys(body, "exchange_id idempotency_key relation protocol request_utf8")
        protocol = (
            "mineagent.rdagent-provider-chat.v1"
            if method == "submit_chat"
            else "mineagent.rdagent-provider-embedding.v1"
        )
        if body["protocol"] != protocol or type(body["request_utf8"]) is not str:
            raise RPCError("configured_provider_request")
        deadline = getattr(self, "original_deadline", None)
        if deadline is None:
            raise RPCError("original_controller_deadline_required")

        operation_id, raw = submit_provider_request(
            self.factory,
            self.fence,
            call_id=_id(body["exchange_id"]),
            idempotency_key=_id(body["idempotency_key"]),
            relation=body["relation"],
            protocol=protocol,
            request_bytes=body["request_utf8"].encode("utf-8"),
            settings=self.settings,
            controller_peer=peer,
            original_deadline=deadline,
        )
        return {"operation_id": str(operation_id), "response": json.loads(raw)}
