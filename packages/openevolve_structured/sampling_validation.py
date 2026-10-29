"""027 JSON-program sampling transition checks, never a host sampler.

Preserves phase8 observed sampling rules with the distinct closed JSON domain
normalizer. Neither RNG nor upstream diversity caches are recreated here.
"""

import copy
import math

from .checkpoint import _normalize, canonical


def validate_sampling_transition(before, after, parent_id, worker_snapshot, domain):
    before = _normalize(before["metadata"], before["programs"], domain)
    after = _normalize(after["metadata"], after["programs"], domain)
    if parent_id not in before["programs"] or canonical(
        before["programs"]
    ) != canonical(after["programs"]):
        raise ValueError("Sampling changed native Program records")
    allowed = copy.deepcopy(before)
    allowed["metadata"]["feature_stats"] = after["metadata"]["feature_stats"]
    # Native sampling clears only a genuinely stale island-best pointer.
    old_best = before["metadata"]["island_best_programs"][0]
    new_best = after["metadata"]["island_best_programs"][0]
    if old_best != new_best:
        if new_best is not None or old_best in before["metadata"]["islands"][0]:
            raise ValueError("Sampling changed a valid island-best pointer")
        allowed["metadata"]["island_best_programs"] = [None]
    if canonical(allowed) != canonical(after):
        raise ValueError(
            "Sampling changed unrelated native metadata or applied frontier"
        )
    old, new = before["metadata"]["feature_stats"], after["metadata"]["feature_stats"]
    if (
        not set(old) <= {"complexity", "diversity"}
        or not set(old) <= set(new)
        or not set(new) <= {"complexity", "diversity"}
    ):
        raise ValueError("Unexpected sampling feature dimensions")
    additions = {}
    for name, stats in new.items():
        prior = old.get(name)
        if (
            type(stats) is not dict
            or set(stats) != {"min", "max", "values"}
            or type(stats["values"]) is not list
            or len(stats["values"]) > 100
        ):
            raise ValueError("Invalid bounded sampling feature history")
        values = stats["values"]
        if any(
            type(x) not in (int, float) or not math.isfinite(x) or not 0 <= x <= 1000000
            for x in [*values, stats["min"], stats["max"]]
        ):
            raise ValueError("Invalid native sampling feature value")
        previous = prior["values"] if prior else []
        if canonical(values[: len(previous)]) != canonical(previous) or len(
            values
        ) < len(previous):
            raise ValueError("Sampling rewrote acknowledged feature history")
        appended = values[len(previous) :]
        if not appended:
            if prior is None or canonical(stats) != canonical(prior):
                raise ValueError("Sampling changed statistics without samples")
        elif stats["min"] != min(
            [prior["min"]] + appended if prior else appended
        ) or stats["max"] != max([prior["max"]] + appended if prior else appended):
            raise ValueError("Sampling extrema differ from exact appended history")
        additions[name] = appended
    complexity = additions.get("complexity", [])
    expected = [
        float(len(before["programs"][identity]["code"]))
        for identity in [parent_id, *worker_snapshot["islands"][0]]
    ]
    island_ids = worker_snapshot["islands"][0]
    if sorted(island_ids) != before["metadata"]["islands"][0]:
        raise ValueError("Sampling island snapshot differs")
    top_id = max(
        island_ids,
        key=lambda identity: before["programs"][identity]["metrics"]["combined_score"],
    )
    already_has_inspiration = (
        old_best is not None and old_best != parent_id and old_best in island_ids
    ) or top_id != parent_id
    expected = [] if already_has_inspiration else expected
    if complexity != expected:
        raise ValueError(
            "Sampling complexity history differs from observed parent/island order"
        )
    diversity = additions.get("diversity", [])
    expected_count = len(complexity) if len(before["programs"]) >= 2 else 0
    if len(diversity) != expected_count:
        raise ValueError(
            "Sampling diversity count differs from native coordinate calls"
        )
    # Values are observed from the pinned authenticated orchestrator. The host
    # does not recompute its private diversity cache/reference set or draw RNG.
    return after
