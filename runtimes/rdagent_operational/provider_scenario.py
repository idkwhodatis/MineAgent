"""Model-backed actual pinned CoSTEER; no fixed source/feedback substitution."""

from packages.rdagent_adapter.provider_prompt import (
    first_provider_messages,
    generation_scenario,
)
from runtimes.rdagent_operational.adapter import OperationalCoSTEER, OperationalScenario


class ProviderGenerationScenario(OperationalScenario):
    def __init__(self, config, seed=None):
        super().__init__(config)
        self._description = generation_scenario(config, seed)

    def get_scenario_all_desc(
        self, task=None, filtered_tag=None, simple_background=None
    ):
        return self._description


class ProviderOperationalCoSTEER(OperationalCoSTEER):
    def __init__(self, config, host, seed=None):
        from packages.rdagent_adapter.upstream import RecordingFactorStrategy, _HooksRef

        first_provider_messages(config, seed)
        super().__init__(config, host)
        self.evolving_strategy = RecordingFactorStrategy(
            scen=ProviderGenerationScenario(config, seed),
            settings=self.settings,
            host=_HooksRef(host),
        )
