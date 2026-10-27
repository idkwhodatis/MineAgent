"""Narrow operational scenario constructor; real pinned CoSTEER develop is inherited.

Search/evaluation/adoption logic is not replaced. The only constructor difference
from the compatibility adapter is the versioned absolute-path numeric ABI text.
"""

from rdagent.components.coder.CoSTEER.evaluators import CoSTEERMultiEvaluator
from rdagent.components.coder.factor_coder import FactorCoSTEER
from rdagent.components.coder.factor_coder.config import FACTOR_COSTEER_SETTINGS
from rdagent.core.conf import RD_AGENT_SETTINGS

from packages.rdagent_adapter.upstream import (
    FrozenFactorScenario,
    RecordingFactorEvaluator,
    RecordingFactorStrategy,
    _HooksRef,
    _ScopedKnowledge,
)
from packages.research_runtime.control import BrokerAbort


class OperationalScenario(FrozenFactorScenario):
    def get_source_data_desc(self, task=None):
        return (
            super().get_source_data_desc(task)
            + " Files are fixed absolute /input/observations.json, /input/listings.json, "
            "/input/manifest.json; current directory is /tmp. Source is /input/source.py. "
            "lookback_prior_sessions L means L prior sessions plus current, never L total. "
            "Write only /output/result.h5, regular mode0644. No relative-path fallback."
        )


class OperationalCoSTEER(FactorCoSTEER):
    def __init__(self, config, host):
        if (
            config.max_iterations != 1
            or RD_AGENT_SETTINGS.multi_proc_n != 1
            or RD_AGENT_SETTINGS.cache_with_pickle
            or FACTOR_COSTEER_SETTINGS.knowledge_base_path is not None
            or FACTOR_COSTEER_SETTINGS.new_knowledge_base_path is not None
            or FACTOR_COSTEER_SETTINGS.enable_filelock
        ):
            raise BrokerAbort("request_drift")
        scenario = OperationalScenario(config)
        super().__init__(
            scen=scenario, max_loop=1, with_knowledge=True, knowledge_self_gen=True
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
