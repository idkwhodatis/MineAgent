"""028 actual pinned CoSTEER with explicitly bound combined-input generation."""

from contextvars import ContextVar

from rdagent.oai.llm_conf import LLM_SETTINGS

from packages.rdagent_adapter.broker_bridge import (
    _PHASE,
)
from packages.rdagent_adapter.broker_bridge import (
    APIBackend as BrokerAPIBackend,
)
from packages.rdagent_adapter.workflow_prompt import (
    first_workflow_messages,
    workflow_generation_scenario,
    workflow_sdk_base_messages,
)
from packages.research_runtime.control import BrokerAbort
from runtimes.rdagent_operational.adapter import OperationalCoSTEER, OperationalScenario

_GENERATION_MESSAGES = ContextVar(
    "mineagent_workflow_generation_messages", default=None
)
_BASE_BACKEND = "packages.rdagent_adapter.broker_bridge.APIBackend"
_WORKFLOW_BACKEND = "runtimes.rdagent_operational.workflow_scenario.WorkflowAPIBackend"


class WorkflowAPIBackend(BrokerAPIBackend):
    """Pinned SDK token counting and sending both call this same message builder."""

    def _build_messages(self, *args, **kwargs):
        messages = super()._build_messages(*args, **kwargs)
        expected = _GENERATION_MESSAGES.get()
        if expected is None:
            raise BrokerAbort("request_drift")
        if _PHASE.get() != "generation":
            return messages
        base, complete = expected
        if messages != [
            {"role": "system", "content": base[0]},
            {"role": "user", "content": base[1]},
        ]:
            # No alternate generation entry may silently drop selected inputs.
            raise BrokerAbort("request_drift")
        if any(len(value) > 8192 for value in complete):
            raise BrokerAbort("request_drift")
        return [
            {"role": "system", "content": complete[0]},
            {"role": "user", "content": complete[1]},
        ]


class WorkflowGenerationScenario(OperationalScenario):
    def __init__(self, config, seed=None):
        super().__init__(config)
        self._description = workflow_generation_scenario(config, seed)

    def get_scenario_all_desc(
        self, task=None, filtered_tag=None, simple_background=None
    ):
        return self._description


class WorkflowOperationalCoSTEER(OperationalCoSTEER):
    def __init__(self, config, host, seed=None):
        from packages.rdagent_adapter.upstream import RecordingFactorStrategy, _HooksRef

        self._workflow_messages = (
            (
                workflow_sdk_base_messages(config, seed),
                first_workflow_messages(config, seed),
            )
            if config.workflow_prompt_layout == "generation_user.v1"
            else None
        )
        first_workflow_messages(config, seed)
        super().__init__(config, host)
        self.evolving_strategy = RecordingFactorStrategy(
            scen=WorkflowGenerationScenario(config, seed),
            settings=self.settings,
            host=_HooksRef(host),
        )

    def develop(self, exp):
        if self._workflow_messages is None:
            return super().develop(exp)
        if (
            LLM_SETTINGS.backend != _BASE_BACKEND
            or _GENERATION_MESSAGES.get() is not None
        ):
            raise BrokerAbort("request_drift")
        token = _GENERATION_MESSAGES.set(self._workflow_messages)
        LLM_SETTINGS.backend = _WORKFLOW_BACKEND
        try:
            return super().develop(exp)
        finally:
            LLM_SETTINGS.backend = _BASE_BACKEND
            _GENERATION_MESSAGES.reset(token)
