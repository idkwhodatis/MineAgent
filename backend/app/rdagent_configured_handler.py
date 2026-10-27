"""022 fixed host router with configured source and actual analysis feedback."""

from backend.app.rdagent_analysis_finalization import (
    finish_analysis_generation,
    retain_analysis_feedback,
)
from backend.app.rdagent_configured_evaluation import (
    execute_configured_evaluation,
    measured_configured_start,
)
from backend.app.rdagent_configured_exposure import (
    authenticated_open_configured_evaluation,
)
from backend.app.rdagent_configured_source import resolve_configured_source
from backend.app.rdagent_operational_handler import OperationalHandler, _id, _keys
from backend.app.research_lifecycle import neutral_transaction
from runtimes.rdagent_operational.rpc import RPCError


class ConfiguredHandler(OperationalHandler):
    def _measured_start(self, peer, start_id):
        return measured_configured_start(
            self.factory, self.fence, peer, start_id, self.settings
        )

    def _measurement_summary(self, snapshot, objective):
        return f"Actual complete snapshot {snapshot.snapshot_id}: {len(snapshot.rows)} retained rows; stopped Qlib declared-objective passed={objective}. Undefined or insufficient analysis is not a passing objective; declared source is not independently verified PIT."

    def __call__(self, peer, method, body):
        if method not in {
            "begin_evaluation",
            "execute_or_reuse",
            "finish_evaluation",
            "generation_boundary",
        }:
            return super().__call__(peer, method, body)
        self._authorize(peer)
        if self.returned:
            raise RPCError("returned_controller")
        if method == "begin_evaluation":
            _keys(body, "start_id source_id")
            with neutral_transaction(
                self.factory, self.fence, resource_scope="factor_objects"
            ) as session:
                market = resolve_configured_source(session, self.fence, self.settings)
                evaluation = authenticated_open_configured_evaluation(
                    session,
                    self.fence,
                    peer,
                    start_id=_id(body["start_id"]),
                    source_id=_id(body["source_id"]),
                    calendar=market.calendar,
                    settings=self.settings,
                )
                return {"evaluation_id": str(evaluation.id)}
        if method == "execute_or_reuse":
            _keys(body, "source_id evaluation_id")
            return execute_configured_evaluation(
                self.factory,
                self.fence,
                peer,
                source_id=_id(body["source_id"]),
                evaluation_id=_id(body["evaluation_id"]),
                settings=self.settings,
            )
        if method == "finish_evaluation":
            _keys(body, "start_id source_id evaluation_id feedback")
            identity = retain_analysis_feedback(
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
        _keys(
            body,
            "start_id logical_step evaluated_source_id previous_adopted_source_id adopted_source_id continuation_source_id adopted_new boundary next_logical_step",
        )
        identity = finish_analysis_generation(
            self.factory, self.fence, peer, body=body, settings=self.settings
        )
        self.returned = True
        return {"generation_outcome_id": str(identity)}
