"""Fixed actual RD host RPC methods, internal maintained021 entrypoint only.

No user-selected callable, provider, SQL, path, source execution or launch token is
accepted by this router. Every event uses the currently authenticated child and
fresh fenced transactions. Long physical work lives in separately reviewed issuers.
"""

import json
from pathlib import Path
from uuid import UUID, uuid4

from backend.app.rdagent_controller_services import (
    authenticated_close_upstream_call,
    authenticated_open_evaluation,
    authenticated_open_upstream_call,
    require_rd_controller,
)
from backend.app.rdagent_operational_events import (
    publish_generated_source,
    start_generation,
)
from backend.app.rdagent_services import _scope_row, require_research_spec
from backend.app.research_lifecycle import neutral_transaction
from packages.research_runtime.engine_broker_contracts import (
    RDChatResponse,
    RDEmbeddingResponse,
    parse_request,
)
from runtimes.rdagent_operational.rpc import RPCError


def _keys(body, fields):
    if type(body) is not dict or set(body) != set(fields.split()):
        raise RPCError("fixed_method_body")


def _id(value):
    if type(value) is not str:
        raise RPCError("exact_uuid")
    parsed = UUID(value)
    if str(parsed) != value:
        raise RPCError("exact_uuid")
    return parsed


class OperationalHandler:
    def __init__(self, factory, fence, settings):
        self.factory, self.fence, self.settings = factory, fence, settings
        self.returned = False

    def _authorize(self, peer, scope="processes"):
        with neutral_transaction(
            self.factory, self.fence, resource_scope=scope
        ) as session:
            require_rd_controller(session, self.fence, peer)

    def __call__(self, peer, method, body):
        self._authorize(peer)
        if self.returned:
            raise RPCError("returned_controller")
        if method == "check_control":
            _keys(body, "")
            return {"current": True}
        if method == "begin_step":
            _keys(body, "step")
            with neutral_transaction(
                self.factory, self.fence, resource_scope="objects"
            ) as session:
                row = start_generation(
                    session, self.fence, peer, logical_step=body["step"]
                )
                return {"start_id": str(row.id)}
        if method == "begin_exchange":
            _keys(body, "start_id kind phase")
            with neutral_transaction(
                self.factory, self.fence, resource_scope="objects"
            ) as session:
                row = authenticated_open_upstream_call(
                    session,
                    self.fence,
                    peer,
                    start_id=_id(body["start_id"]),
                    call_id=uuid4(),
                    kind=body["kind"],
                    phase=body["phase"],
                )
                return {"exchange_id": str(row.id)}
        if method == "end_exchange":
            _keys(body, "exchange_id succeeded")
            with neutral_transaction(
                self.factory, self.fence, resource_scope="objects"
            ) as session:
                row = authenticated_close_upstream_call(
                    session,
                    self.fence,
                    peer,
                    call_id=_id(body["exchange_id"]),
                    succeeded=body["succeeded"],
                    settings=self.settings,
                )
                return {"outcome_id": str(row.id)}
        if method in {"submit_chat", "submit_embedding"}:
            return self._model(peer, method, body)
        if method == "publish_source":
            _keys(body, "start_id task_index source source_sha256")
            if (
                type(body["source"]) is not str
                or not 1 <= len(body["source"].encode("utf8")) <= 131072
            ):
                raise RPCError("bounded_source")
            row = publish_generated_source(
                self.factory,
                self.fence,
                peer,
                start_id=_id(body["start_id"]),
                task_index=body["task_index"],
                source=body["source"],
                source_sha256=body["source_sha256"],
                settings=self.settings,
            )
            return {"source_id": str(row.source_id)}
        if method == "begin_evaluation":
            from backend.app.rdagent_complete_evaluation import maintained_calendar

            _keys(body, "start_id source_id")
            with neutral_transaction(
                self.factory, self.fence, resource_scope="factor_objects"
            ) as session:
                _, config = require_research_spec(session, self.fence)
                row = authenticated_open_evaluation(
                    session,
                    self.fence,
                    peer,
                    start_id=_id(body["start_id"]),
                    source_id=_id(body["source_id"]),
                    calendar=maintained_calendar(config),
                    settings=self.settings,
                )
                return {"evaluation_id": str(row.id)}
        if method == "execute_or_reuse":
            from backend.app.rdagent_operational_evaluation import (
                execute_or_reuse_snapshot,
            )

            _keys(body, "source_id evaluation_id")
            return execute_or_reuse_snapshot(
                self.factory,
                self.fence,
                peer,
                source_id=_id(body["source_id"]),
                evaluation_id=_id(body["evaluation_id"]),
                settings=self.settings,
            )
        if method == "finish_evaluation":
            from backend.app.rdagent_operational_finalization import retain_feedback

            _keys(body, "start_id source_id evaluation_id feedback")
            identity = retain_feedback(
                self.factory,
                self.fence,
                peer,
                start_id=_id(body["start_id"]),
                source_id=_id(body["source_id"]),
                evaluation_id=_id(body["evaluation_id"]),
                upstream_feedback=body["feedback"],
                settings=self.settings,
            )
            return {"feedback_object_id": str(identity)}
        if method == "generation_boundary":
            from backend.app.rdagent_operational_finalization import finish_generation

            _keys(
                body,
                "start_id logical_step evaluated_source_id previous_adopted_source_id adopted_source_id continuation_source_id adopted_new boundary next_logical_step",
            )
            identity = finish_generation(
                self.factory, self.fence, peer, body=body, settings=self.settings
            )
            self.returned = True
            return {"generation_outcome_id": str(identity)}
        # First maintained valid-response generation deliberately cannot pretend
        # to cover semantic repair, rejection or resumable controller workflows.
        raise RPCError("unsupported_initial_controller_transition")

    def _measured_start(self, peer, start_id):
        from backend.app.rdagent_operational_evaluation import measured_start_snapshot

        return measured_start_snapshot(
            self.factory, self.fence, peer, start_id, self.settings
        )

    def _measurement_summary(self, snapshot, objective):
        return f"Actual retained complete snapshot {snapshot.snapshot_id}: {len(snapshot.rows)} rows; maintained momentum oracle passed={objective}."

    def _model(self, peer, method, body):
        from backend.app.rdagent_models import FactorUpstreamCall
        from backend.app.rdagent_operational_broker import submit_fixture_request

        _keys(body, "exchange_id idempotency_key relation protocol request_utf8")
        protocol = (
            "mineagent.rdagent-chat.v1"
            if method == "submit_chat"
            else "mineagent.rdagent-embedding.v1"
        )
        if (
            body["protocol"] != protocol
            or type(body["request_utf8"]) is not str
            or body["relation"] != "initial"
        ):
            raise RPCError("initial_fixture_request_only")
        raw = body["request_utf8"].encode("utf8")
        request = parse_request(protocol, raw)
        call_id = _id(body["exchange_id"])
        with neutral_transaction(
            self.factory, self.fence, resource_scope="objects"
        ) as session:
            require_rd_controller(session, self.fence, peer)
            call = _scope_row(session, FactorUpstreamCall, call_id, self.fence)
            phase, start_id, kind = call.phase, call.start_id, call.kind
        if kind != ("chat" if method == "submit_chat" else "embedding"):
            raise RPCError("persisted_call_kind")
        if method == "submit_embedding":
            response = RDEmbeddingResponse(
                vectors=tuple((0.25, 0.75) for _ in request.inputs)
            )
        elif phase == "generation":
            source = (
                Path(__file__).parents[2] / "runtimes/factor_execution/abi_fixture.py"
            ).read_bytes()
            # This maintained source is the only executable fixture admitted by
            # the numeric issuer; model responses still traverse actual ledger.
            response = RDChatResponse(
                content=json.dumps(
                    {"code": source.decode("utf8")}, separators=(",", ":")
                ),
                finish_reason="stop",
            )
        else:
            snapshot, objective = self._measured_start(peer, start_id)
            summary = self._measurement_summary(snapshot, objective)
            if phase == "output_format":
                content = json.dumps(
                    {"output_format_decision": True, "output_format_feedback": summary},
                    separators=(",", ":"),
                )
            elif phase == "critique":
                content = summary
            elif phase == "final_decision":
                content = json.dumps(
                    {"final_decision": objective, "final_feedback": summary},
                    separators=(",", ":"),
                )
            else:
                raise RPCError("fixed_chat_phase")
            response = RDChatResponse(content=content, finish_reason="stop")

        def revalidate(session, fence):
            require_rd_controller(session, fence, peer)
            return peer

        receipt = submit_fixture_request(
            self.factory,
            self.fence,
            call_id=call_id,
            idempotency_key=_id(body["idempotency_key"]),
            relation=body["relation"],
            protocol=protocol,
            request_bytes=raw,
            fixture_response=response,
            settings=self.settings,
            revalidate_controller=revalidate,
        )
        return {
            "operation_id": str(receipt.operation_id),
            "response": json.loads(receipt.response_bytes),
        }
