"""026 scenario input seam; old fresh runtime/installed image stay unchanged.

The actual pinned first-generation strategy reads scenario text, but does not
consume its workspace/previous-feedback arguments. Never manufacture historical
knowledge or a successful prior evaluation to inject the owned parent source.
"""

from packages.rdagent_adapter.seed_parent import ResolvedSeedParent
from packages.rdagent_adapter.seed_prompt_budget import SEED_NOTICE
from runtimes.rdagent_operational.adapter import OperationalCoSTEER, OperationalScenario


class SeededOperationalScenario(OperationalScenario):
    def __init__(self, config, seed: ResolvedSeedParent):
        super().__init__(config)
        # Revalidate typed bytes at runtime; this proves integrity, not authority.
        # Host admission must separately bind this to its persisted seed receipt.
        self.seed = ResolvedSeedParent.model_validate_json(seed.model_dump_json())
        self._seed_section = self.seed.prompt_section()

    def get_scenario_all_desc(
        self, task=None, filtered_tag=None, simple_background=None
    ):
        return (
            super().get_scenario_all_desc(task, filtered_tag, simple_background)
            + SEED_NOTICE
            + self._seed_section
        )


class SeededOperationalCoSTEER(OperationalCoSTEER):
    """Actual inherited loop, seed only in generation; evaluator scenario unchanged."""

    def __init__(self, config, host, seed):
        from packages.rdagent_adapter.seed_prompt_budget import first_prompt_messages
        from packages.rdagent_adapter.upstream import RecordingFactorStrategy, _HooksRef

        # Reject instead of truncating before any actual upstream model exchange.
        first_prompt_messages(config, seed)
        super().__init__(config, host)
        self.evolving_strategy = RecordingFactorStrategy(
            scen=SeededOperationalScenario(config, seed),
            settings=self.settings,
            host=_HooksRef(host),
        )
