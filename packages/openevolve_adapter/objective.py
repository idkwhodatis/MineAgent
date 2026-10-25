"""Score validated observations, never upstream fallback metrics or rounded rewards."""

import math
from dataclasses import dataclass
from uuid import UUID

from packages.research_runtime.broker_contracts import digest
from packages.tradegrad_adapter.independent_results import validate_result_bundle
from packages.tradegrad_adapter.results import canonical_json_bytes, sha256_bytes

from .contracts import ObjectiveProfile


@dataclass(frozen=True)
class Observation:
    window_id: str
    evaluation_id: UUID
    bundle_sha256: str
    annualized_return: float | None
    max_drawdown: float | None
    closed_trades: int


@dataclass(frozen=True)
class ObjectiveReceipt:
    profile_sha256: str
    observations: tuple[Observation, ...]
    eligible: bool
    combined_score: float | None
    reason: str


def native_observation(
    bundle: dict, *, window_id: str, evaluation_id: UUID, expected_bundle_sha256: str
) -> Observation:
    """Strict physical codec validation; durable admission/fencing remains host-owned."""
    validate_result_bundle(bundle)
    actual = sha256_bytes(canonical_json_bytes(bundle))
    if actual != expected_bundle_sha256 or type(evaluation_id) is not UUID:
        raise ValueError("Observation identity differs from admitted evaluation")
    metrics = {x["name"]: x for x in bundle["metrics"]}
    values = []
    for name in ("annualized_return", "max_drawdown"):
        metric = metrics[name]
        if metric["unit"] != "percent":
            raise ValueError("Objective metric units differ")
        values.append(metric["value"])
    native = {x["name"]: x["value"] for x in bundle["native_metrics"]}
    trades = native["closed_trade_count"]
    if (
        type(trades) not in (int, float)
        or not math.isfinite(trades)
        or int(trades) != trades
        or trades < 0
    ):
        raise ValueError("Invalid native trade count")
    return Observation(window_id, evaluation_id, actual, *values, int(trades))


def score(
    profile: ObjectiveProfile, observations: tuple[Observation, ...]
) -> ObjectiveReceipt:
    """No-trade, negative, and zero are not implicitly invalid."""
    definition = digest(profile)
    if len(observations) != len(profile.windows) or tuple(
        o.window_id for o in observations
    ) != tuple(w.id for w in profile.windows):
        raise ValueError("Exact ordered objective windows required")
    if len({o.evaluation_id for o in observations}) != len(observations):
        raise ValueError("Evaluation cannot be substituted across windows")
    terms = []
    for window, observed in zip(profile.windows, observations, strict=True):
        values = (observed.annualized_return, observed.max_drawdown)
        if any(type(x) not in (int, float) or not math.isfinite(x) for x in values):
            return ObjectiveReceipt(
                definition, observations, False, None, "undefined_objective"
            )
        if observed.max_drawdown < 0:
            return ObjectiveReceipt(
                definition, observations, False, None, "invalid_drawdown"
            )
        if (
            profile.minimum_closed_trades is not None
            and observed.closed_trades < profile.minimum_closed_trades
        ):
            return ObjectiveReceipt(
                definition, observations, False, None, "explicit_trade_constraint"
            )
        terms.append(
            (observed.annualized_return - observed.max_drawdown)
            * (window.weight_millionths / 1_000_000)
        )
    value = math.fsum(terms)
    if not math.isfinite(value):
        return ObjectiveReceipt(
            definition, observations, False, None, "nonfinite_aggregation"
        )
    return ObjectiveReceipt(definition, observations, True, value, "eligible")


def population_metrics(receipt: ObjectiveReceipt) -> dict[str, float]:
    if (
        not receipt.eligible
        or type(receipt.combined_score) not in (int, float)
        or not math.isfinite(receipt.combined_score)
    ):
        raise ValueError("Ineligible observation cannot enter native population")
    return {"combined_score": receipt.combined_score}


def verify_population_score(metrics: dict, receipt: ObjectiveReceipt) -> bool:
    return (
        type(metrics) is dict
        and set(metrics) == {"combined_score"}
        and type(metrics["combined_score"]) in (int, float)
        and receipt.eligible
        and metrics["combined_score"] == receipt.combined_score
    )
