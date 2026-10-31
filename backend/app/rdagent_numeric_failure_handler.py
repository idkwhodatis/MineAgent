"""Exact027 configured result protocol, preserving old fixture and success frames."""

from sqlalchemy import select

from backend.app.rdagent_configured_evaluation import execute_configured_evaluation
from backend.app.rdagent_models import FactorCutoffExecution, FactorEvaluationStart
from backend.app.rdagent_numeric_failure_finalization import (
    retain_numeric_failure_feedback,
)
from backend.app.rdagent_numeric_failure_services import (
    read_candidate_failure,
    require_failure_profile,
)
from backend.app.rdagent_operational_evaluation import snapshot_transaction
from backend.app.rdagent_operational_handler import _id, _keys
from backend.app.rdagent_provider_handler import ProviderHandler
from backend.app.rdagent_services import _scope
from backend.app.research_process_models import ResearchProcessReceipt
from runtimes.rdagent_operational.rpc import RPCError


class ProviderRejectionHandler(ProviderHandler):
    def __call__(self, peer, method, body):
        if method not in {
            "execute_or_reuse",
            "finish_evaluation",
            "generation_boundary",
        }:
            return super().__call__(peer, method, body)
        self._authorize(peer)
        if self.returned:
            raise RPCError("returned_controller")
        if method == "execute_or_reuse":
            _keys(body, "source_id evaluation_id")
            return execute_configured_evaluation(
                self.factory,
                self.fence,
                peer,
                source_id=_id(body["source_id"]),
                evaluation_id=_id(body["evaluation_id"]),
                settings=self.settings,
                capture_rejection=True,
            )
        if method == "finish_evaluation":
            _keys(body, "start_id source_id evaluation_id feedback")
            with snapshot_transaction(
                self.factory, self.fence, self.settings
            ) as session:
                failed = read_candidate_failure(
                    session,
                    self.fence,
                    peer,
                    source_id=_id(body["source_id"]),
                    evaluation_id=_id(body["evaluation_id"]),
                    settings=self.settings,
                )
            if failed is not None:
                identity = retain_numeric_failure_feedback(
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
        else:
            # A positive model vote is not authority to adopt rejected output.
            # Check durable evidence, not an in-memory flag or the caller's body.
            with snapshot_transaction(
                self.factory, self.fence, self.settings
            ) as session:
                require_failure_profile(session, self.fence, peer)
                found = session.scalar(
                    select(ResearchProcessReceipt.id)
                    .join(
                        FactorCutoffExecution,
                        FactorCutoffExecution.candidate_admission_id
                        == ResearchProcessReceipt.admission_id,
                    )
                    .join(
                        FactorEvaluationStart,
                        FactorEvaluationStart.id == FactorCutoffExecution.evaluation_id,
                    )
                    .where(
                        *(
                            getattr(ResearchProcessReceipt, key) == value
                            for key, value in _scope(self.fence).items()
                        ),
                        *(
                            getattr(FactorCutoffExecution, key) == value
                            for key, value in _scope(self.fence).items()
                        ),
                        *(
                            getattr(FactorEvaluationStart, key) == value
                            for key, value in _scope(self.fence).items()
                        ),
                        FactorEvaluationStart.start_id == _id(body.get("start_id")),
                        ResearchProcessReceipt.kind == "rejection",
                    )
                )
                if found is not None:
                    raise RPCError("numeric_rejection_cannot_adopt_or_complete")
        return super().__call__(peer, method, body)


def select_provider_result_handler(config, revisions):
    """Explicit installed provider result grammar; fixture success remains separate."""
    from packages.rdagent_adapter.provider_config import ProviderFactorResearchConfig
    from packages.rdagent_adapter.workflow_config import WorkflowFactorResearchConfig

    if type(config) is ProviderFactorResearchConfig:
        if revisions in (
            ["026_rd_seeded_submissions"],
            ["027_rd_controller_v3"],
            ["027a_contract_repairs"],
            ["027b_provider_material"],
            ["027c_seeded_controller_v4"],
        ):
            return ProviderHandler
        if revisions in (
            ["028_oe_structured_search"],
            ["029_research_workflow"],
            ["030_declared_upload_basket"],
            ["031_workflow_structured_stage"],
            ["032_normal_oe_note_pins"],
            ["032a_rd_terminal_results"],
            ["032b_oe_append_run_state"],
        ):
            return ProviderRejectionHandler
    elif type(config) is WorkflowFactorResearchConfig and revisions in (
        ["029_research_workflow"],
        ["030_declared_upload_basket"],
        ["031_workflow_structured_stage"],
        ["032_normal_oe_note_pins"],
        ["032a_rd_terminal_results"],
        ["032b_oe_append_run_state"],
    ):
        return ProviderRejectionHandler
    raise RPCError("numeric_failure_configured_profile_required")
