"""Pinned CoSTEER seams; no substitute search loop or ordinary workspace execution.

Import only after trusted runtime bootstrap. Hooks are authenticated host IPC,
not local budget counters or direct database/credential access in the runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from rdagent.components.coder.CoSTEER.evaluators import CoSTEERMultiEvaluator
from rdagent.components.coder.factor_coder import FactorCoSTEER
from rdagent.components.coder.factor_coder.config import FACTOR_COSTEER_SETTINGS
from rdagent.components.coder.factor_coder.evaluators import FactorEvaluatorForCoder
from rdagent.components.coder.factor_coder.evolving_strategy import (
    FactorMultiProcessEvolvingStrategy,
)
from rdagent.components.coder.factor_coder.factor import FactorFBWorkspace
from rdagent.core.conf import RD_AGENT_SETTINGS
from rdagent.core.scenario import Scenario

from packages.factors.contracts import source_sha256
from packages.rdagent_adapter.broker_bridge import model_phase
from packages.rdagent_adapter.contracts import FactorResearchConfig
from packages.research_runtime.control import BrokerAbort


class HostHooks(Protocol):
    def check_control(self) -> None: ...
    def begin_step(self, step: int) -> None: ...
    def publish_source(
        self, *, task_index: int, source: str, source_sha256: str
    ) -> UUID: ...
    def reject_source(self, *, task_index: int) -> None: ...
    def begin_evaluation(self, source_id: UUID | None) -> UUID: ...
    def execute_or_reuse(
        self, *, source_id: UUID, evaluation_ticket: UUID
    ) -> tuple[str, object]: ...
    def finish_evaluation(
        self, *, source_id: UUID | None, evaluation_ticket: UUID, feedback: dict
    ) -> None: ...


@dataclass(frozen=True)
class _HooksRef:
    host: HostHooks

    def __deepcopy__(self, memo):
        # Upstream fallback deep-copies its workspaces, never a new authority.
        return self


class FrozenFactorScenario(Scenario):
    def __init__(self, config: FactorResearchConfig):
        self.config = config
        self.input_shape = (None, len(config.input.input_fields))

    @property
    def background(self):
        return "Fixed-task factor implementation/refinement over host-projected frozen inputs. No source PIT verification or trading-profit claim."

    @property
    def rich_style_description(self):
        return self.background

    def get_source_data_desc(self, task=None):
        fields = ", ".join(
            f"{x.name} ({x.unit})" for x in self.config.input.input_fields
        )
        return f"Read only observations.json and listings.json. Fields: {fields}. The full static universe is retained; unavailable observations are absent. Only the declared lookback is mounted."

    def get_runtime_environment(self):
        return "Python 3.12; network disabled; no credentials, Docker, provider discovery or host storage access."

    def get_scenario_all_desc(
        self, task=None, filtered_tag=None, simple_background=None
    ):
        return (
            self.background
            + "\n"
            + self.get_source_data_desc(task)
            + "\n"
            + self.get_runtime_environment()
            + "\nWrite result.h5: group /factor only, one-dimensional uncompressed event_ns int64 LE, listing_code uint32 LE, value float64 LE, state uint8. Only manifest output event and listing codes are allowed. No attributes, extra nodes, links, filters, strings, objects or pickle."
        )


class MineAgentFactorWorkspace(FactorFBWorkspace):
    def __init__(self, *args, host: _HooksRef, **kwargs):
        super().__init__(*args, **kwargs)
        self._host = host
        self.source_id = None
        self.evaluation_ticket = None

    def execute(self, *args, **kwargs):
        # Completely replace native shell execution/cache/read_hdf boundary.
        self._host.host.check_control()
        if args or kwargs or self.evaluation_ticket is None:
            raise BrokerAbort("request_drift")
        if self.source_id is None:
            return "Source rejected before execution.", None
        feedback, frame = self._host.host.execute_or_reuse(
            source_id=self.source_id, evaluation_ticket=self.evaluation_ticket
        )
        if not isinstance(feedback, str) or len(feedback) > 8192:
            raise BrokerAbort("request_drift")
        return feedback, frame.copy(deep=True) if frame is not None else None


class RecordingFactorStrategy(FactorMultiProcessEvolvingStrategy):
    def __init__(self, *args, host: _HooksRef, **kwargs):
        super().__init__(*args, **kwargs)
        self._host = host

    def evolve_iter(self, *, evolving_trace=(), **kwargs):
        self._host.host.check_control()
        yield from super().evolve_iter(evolving_trace=list(evolving_trace), **kwargs)

    def implement_one_task(self, *args, **kwargs):
        with model_phase("generation"):
            return super().implement_one_task(*args, **kwargs)

    def assign_code_list_to_evo(self, code_list, evo):
        if len(evo.sub_tasks) != 1 or len(code_list) != 1:
            raise BrokerAbort("request_drift")
        for index, code in enumerate(code_list):
            if code is None:
                continue
            if isinstance(code, dict):
                if set(code) != {"factor.py"}:
                    raise BrokerAbort("request_drift")
                code = code["factor.py"]
            workspace = evo.sub_workspace_list[index]
            if workspace is None:
                workspace = MineAgentFactorWorkspace(
                    target_task=evo.sub_tasks[index], host=self._host
                )
                evo.sub_workspace_list[index] = workspace
            if not isinstance(workspace, MineAgentFactorWorkspace):
                raise BrokerAbort("request_drift")
            try:
                sha = source_sha256(code)
            except ValueError:
                self._host.host.reject_source(task_index=index)
                workspace.source_id = None
                workspace.evaluation_ticket = None
                # Do not retain a previous valid implementation under this failed
                # proposal. Upstream fallback has its own separately copied state.
                workspace.remove_files("factor.py")
                workspace.file_dict.clear()
                continue
            identity = self._host.host.publish_source(
                task_index=index, source=code, source_sha256=sha
            )
            if not isinstance(identity, UUID):
                raise BrokerAbort("publication_failed")
            workspace.source_id = identity
            workspace.evaluation_ticket = None
            workspace.inject_files(**{"factor.py": code})
        return evo


class _ScopedEvaluator:
    """Delegates unchanged evaluation; names the only model-producing phase."""

    def __init__(self, delegate, phase):
        self.delegate, self.phase = delegate, phase

    def evaluate(self, *args, **kwargs):
        with model_phase(self.phase):
            return self.delegate.evaluate(*args, **kwargs)


class _ScopedKnowledge:
    def __init__(self, delegate, host):
        self.delegate, self.host = delegate, host

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def query(self, evo, evolving_trace):
        # Upstream queries knowledge before generation. Persist the immutable
        # generation start before any possible query-model operation.
        self.host.host.begin_step(len(evolving_trace))
        with model_phase("knowledge_query"):
            return self.delegate.query(evo, evolving_trace)

    def generate_knowledge(self, evolving_trace):
        with model_phase("knowledge_update"):
            return self.delegate.generate_knowledge(evolving_trace)


class RecordingFactorEvaluator(FactorEvaluatorForCoder):
    def __init__(self, *args, host: _HooksRef, **kwargs):
        super().__init__(*args, **kwargs)
        self._host = host
        self.value_evaluator = _ScopedEvaluator(self.value_evaluator, "output_format")
        self.code_evaluator = _ScopedEvaluator(self.code_evaluator, "critique")
        self.final_decision_evaluator = _ScopedEvaluator(
            self.final_decision_evaluator, "final_decision"
        )

    def evaluate(
        self,
        target_task,
        implementation,
        gt_implementation=None,
        queried_knowledge=None,
        **kwargs,
    ):
        self._host.host.check_control()
        if (
            not isinstance(implementation, MineAgentFactorWorkspace)
            or gt_implementation is not None
        ):
            raise BrokerAbort("request_drift")
        ticket = self._host.host.begin_evaluation(implementation.source_id)
        implementation.evaluation_ticket = ticket
        feedback = super().evaluate(
            target_task, implementation, None, queried_knowledge, **kwargs
        )
        if feedback is None:
            raise BrokerAbort("publication_failed")
        self._host.host.finish_evaluation(
            source_id=implementation.source_id,
            evaluation_ticket=ticket,
            feedback={
                "execution": feedback.execution,
                "return_checking": feedback.return_checking,
                "code": feedback.code,
                "final_decision": feedback.final_decision,
            },
        )
        return feedback


class MineAgentFactorCoSTEER(FactorCoSTEER):
    def __init__(self, config: FactorResearchConfig, host: HostHooks):
        if RD_AGENT_SETTINGS.multi_proc_n != 1 or RD_AGENT_SETTINGS.cache_with_pickle:
            raise BrokerAbort("request_drift")
        if (
            FACTOR_COSTEER_SETTINGS.knowledge_base_path is not None
            or FACTOR_COSTEER_SETTINGS.new_knowledge_base_path is not None
            or FACTOR_COSTEER_SETTINGS.enable_filelock
        ):
            raise BrokerAbort("request_drift")
        scenario = FrozenFactorScenario(config)
        super().__init__(
            scen=scenario,
            max_loop=config.max_iterations,
            with_knowledge=True,
            knowledge_self_gen=True,
        )
        if (
            self.knowledge_base_path is not None
            or self.new_knowledge_base_path is not None
        ):
            raise BrokerAbort("request_drift")
        bound = _HooksRef(host)
        self.rag = _ScopedKnowledge(self.rag, bound)
        self.evolving_strategy = RecordingFactorStrategy(
            scen=scenario, settings=self.settings, host=bound
        )
        self.evaluator = CoSTEERMultiEvaluator(
            RecordingFactorEvaluator(scen=scenario, host=bound), scen=scenario
        )
