"""Explicit first operational capability; never silently narrows SearchSpec DTO.

Only fixed maintained-market profiles, no parameters, no factor-package injection
or marker preparation are accepted in revision024. Unsupported configurations are
rejected before reservation. Broader stage3 capability and resume remain pending.
"""

from packages.openevolve_adapter.contracts import SearchSpec
from packages.openevolve_adapter.execution_inputs import ResolvedNativeInputs
from packages.tradegrad_adapter.config import MarketConfig, VersionRef


def require_initial_profile(spec, native):
    if type(spec) is not SearchSpec or type(native) is not ResolvedNativeInputs:
        raise ValueError("exact initial operational profile")
    spec = SearchSpec.model_validate_json(spec.model_dump_json())
    native = ResolvedNativeInputs.model_validate_json(native.model_dump_json())
    if (
        spec.preparation.kind != "none"
        or spec.factors.kind != "none"
        or not 1 <= spec.maximum_mutation_slots <= 2
        or spec.population_size > 8
        or spec.archive_size > 8
        or native.strategy_contract.parameters
        or native.market_profile != MarketConfig.for_market(spec.execution.market)
    ):
        raise ValueError("unsupported initial OE capability")
    VersionRef(id=native.universe_id, sha256=spec.execution.universe_sha256)
    native.validate_plan(spec.execution)
    return spec, native
