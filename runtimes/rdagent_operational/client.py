"""Operational IPC implementation of the pinned CoSTEER public adapter seams.

Request nonces are replay keys only. No ModelOperation is allocated until the
host receives the COMPLETE immutable request bytes and atomically reserves it.
The compatibility backend remains byte-unchanged; its `logical_id` argument here
is explicitly a transient nonce, never a host identity or execution authority.
"""

import json
from uuid import UUID, uuid4

from packages.research_runtime.engine_broker_contracts import (
    RDChatRequest,
    RDChatResponse,
    RDEmbeddingRequest,
    RDEmbeddingResponse,
    RDMessage,
    canonical_bytes,
)
from runtimes.rdagent_operational.rpc import RPCError


def _uuid(value):
    if type(value) is not str:
        raise RPCError("host_identity")
    result = UUID(value)
    if str(result) != value:
        raise RPCError("host_identity")
    return result


class OperationalHost:
    def __init__(self, channel):
        self.channel = channel
        self.pending = {}
        self.exchanges = set()
        self.step = None
        self.last_source = None

    def check_control(self):
        if self.channel.call("check_control", {}) != {"current": True}:
            raise RPCError("fence_lost")

    def begin_step(self, step):
        if type(step) is not int or step < 0:
            raise RPCError("step")
        result = self.channel.call("begin_step", {"step": step})
        self.step = _uuid(result["start_id"])

    def begin_exchange(self, *, kind, phase):
        if self.step is None:
            raise RPCError("missing_start")
        result = self.channel.call(
            "begin_exchange", {"start_id": str(self.step), "kind": kind, "phase": phase}
        )
        identity = _uuid(result["exchange_id"])
        if identity in self.exchanges:
            raise RPCError("exchange_reused")
        self.exchanges.add(identity)
        return identity

    def new_request(self, exchange_id, *, relation):
        if (
            exchange_id not in self.exchanges
            or relation not in {"initial", "continuation", "semantic_repair"}
            or self.pending
        ):
            raise RPCError("request_order")
        nonce = uuid4()
        self.pending[nonce] = (exchange_id, relation)
        return nonce

    def _submit(self, nonce, protocol, request, method):
        if nonce not in self.pending:
            raise RPCError("request_nonce")
        exchange, relation = self.pending.pop(nonce)
        raw = canonical_bytes(protocol, request)
        result = self.channel.call(
            method,
            {
                "exchange_id": str(exchange),
                "idempotency_key": str(nonce),
                "relation": relation,
                "protocol": protocol,
                "request_utf8": raw.decode("utf-8"),
            },
        )
        # This identity comes only from the atomically committed host admission.
        _uuid(result["operation_id"])
        return result["response"]

    def chat(self, logical_id, *, messages, response_format):
        request = RDChatRequest(
            messages=tuple(RDMessage.model_validate(message) for message in messages),
            max_output_tokens=4096,
            response_format=response_format,
        )
        response = RDChatResponse.model_validate(
            self._submit(
                logical_id, "mineagent.rdagent-chat.v1", request, "submit_chat"
            )
        )
        return response.content, response.finish_reason

    def embedding(self, logical_id, *, inputs):
        request = RDEmbeddingRequest(inputs=tuple(inputs))
        response = RDEmbeddingResponse.model_validate_json(
            json.dumps(
                self._submit(
                    logical_id,
                    "mineagent.rdagent-embedding.v1",
                    request,
                    "submit_embedding",
                )
            )
        )
        if len(response.vectors) != len(request.inputs):
            raise RPCError("embedding_cardinality")
        return [list(vector) for vector in response.vectors]

    def count_tokens(self, messages):
        # Upstream prompt-planning estimate only. Actual fixture accounting is
        # independently calculated/reserved by the host, never this estimate.
        request = RDChatRequest(
            messages=tuple(RDMessage.model_validate(message) for message in messages),
            max_output_tokens=4096,
            response_format=None,
        )
        return max(
            1, (len(canonical_bytes("mineagent.rdagent-chat.v1", request)) + 2) // 3
        )

    def semantic_retry(self, exchange_id, *, reason):
        self.channel.call(
            "semantic_retry", {"exchange_id": str(exchange_id), "reason": reason}
        )

    def end_exchange(self, exchange_id, *, succeeded):
        if (
            exchange_id not in self.exchanges
            or self.pending
            or type(succeeded) is not bool
        ):
            raise RPCError("exchange_order")
        self.channel.call(
            "end_exchange", {"exchange_id": str(exchange_id), "succeeded": succeeded}
        )
        self.exchanges.remove(exchange_id)

    def publish_source(self, *, task_index, source, source_sha256):
        result = self.channel.call(
            "publish_source",
            {
                "start_id": str(self.step),
                "task_index": task_index,
                "source": source,
                "source_sha256": source_sha256,
            },
        )
        self.last_source = _uuid(result["source_id"])
        return self.last_source

    def reject_source(self, *, task_index):
        self.channel.call(
            "reject_source", {"start_id": str(self.step), "task_index": task_index}
        )

    def begin_evaluation(self, source_id):
        result = self.channel.call(
            "begin_evaluation",
            {
                "start_id": str(self.step),
                "source_id": str(source_id) if source_id is not None else None,
            },
        )
        return _uuid(result["evaluation_id"])

    def finish_evaluation(self, *, source_id, evaluation_ticket, feedback):
        self.channel.call(
            "finish_evaluation",
            {
                "start_id": str(self.step),
                "source_id": str(source_id) if source_id is not None else None,
                "evaluation_id": str(evaluation_ticket),
                "feedback": feedback,
            },
        )

    def execute_or_reuse(self, *, source_id, evaluation_ticket):
        result = self.channel.call(
            "execute_or_reuse",
            {"source_id": str(source_id), "evaluation_id": str(evaluation_ticket)},
        )
        feedback, rows = decode_evaluation_frame(
            result, source_id=source_id, evaluation_ticket=evaluation_ticket
        )
        # Only already-validated primitive JSON is converted in the trusted RD
        # runtime. No HDF, Parquet, native candidate module or pickle is read.
        import pandas as pd

        index = pd.MultiIndex.from_tuples(
            [(pd.Timestamp(row.event_time), row.listing_id) for row in rows],
            names=["datetime", "instrument"],
        )
        frame = pd.DataFrame({"factor": [row.value for row in rows]}, index=index)
        return feedback, frame


def decode_evaluation_frame(result, *, source_id, evaluation_ticket):
    """Validate primitive transport data only; no proof or publication grant."""
    from packages.factors.contracts import FeatureRow, digest

    if (
        type(result) is not dict
        or set(result)
        != {
            "schema_version",
            "source_id",
            "evaluation_id",
            "snapshot_id",
            "factor_version_id",
            "rows",
            "rows_sha256",
            "feedback",
        }
        or result["schema_version"] != "mineagent.rdagent-evaluation-frame.v1"
        or result["source_id"] != str(source_id)
        or result["evaluation_id"] != str(evaluation_ticket)
    ):
        raise RPCError("evaluation_binding")
    _uuid(result["snapshot_id"])
    version = _uuid(result["factor_version_id"])
    if (
        type(result["feedback"]) is not str
        or not 1 <= len(result["feedback"]) <= 32768
        or type(result["rows"]) is not list
        or not 1 <= len(result["rows"]) <= 1000
    ):
        raise RPCError("evaluation_bound")
    rows = tuple(
        FeatureRow.model_validate_json(json.dumps(row)) for row in result["rows"]
    )
    canonical = [row.model_dump(mode="json") for row in rows]
    keys = [(row.event_time, row.listing_id) for row in rows]
    if (
        canonical != result["rows"]
        or digest(canonical) != result["rows_sha256"]
        or keys != sorted(set(keys))
        or any(row.factor_version_id != version for row in rows)
    ):
        raise RPCError("evaluation_rows")
    return result["feedback"], rows
