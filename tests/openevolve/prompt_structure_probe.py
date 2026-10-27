"""Compare closed host reference projection with actual installed native sampler.

Standalone synthetic structure probe; does not validate durable prompt receipts.
"""

import copy
import hashlib
import itertools
import json
import random
from unittest.mock import patch
from uuid import UUID

from openevolve.config import PromptConfig
from openevolve.database import Program
from openevolve.prompt.sampler import PromptSampler

from packages.openevolve_adapter import observer
from packages.openevolve_adapter.host_contracts import initial_prompt_references


def exercise(count, order, tied, inspiration_mode):
    ids = [str(UUID(int=i + 1)) for i in range(count)]
    programs = {
        pid: Program(
            id=pid,
            code=f"probe_value = {i}\n",
            metrics={"combined_score": -1.0 if tied else float(i - 4)},
            metadata={"island": 0},
        ).to_dict()
        for i, pid in enumerate(ids)
    }
    island = [ids[i] for i in order]
    inspirations = {
        "none": [],
        "all": ids,
        "reversed": list(reversed(ids)),
        "duplicates": [*ids, *ids, ids[-1]],
    }[inspiration_mode]
    selection = {
        "inspiration_ids": inspirations,
        "snapshot": {"programs": programs, "islands": [island]},
    }
    before = copy.deepcopy(selection)
    random.seed(731)
    rng_before = random.getstate()
    predicted = [
        r.model_dump(mode="json") for r in initial_prompt_references(selection)
    ]
    assert random.getstate() == rng_before, "Host structural checker consumed RNG"
    assert selection == before, "Host structural checker changed snapshot order"
    # Exactly the pinned worker's native input slicing: stable island-order sort,
    # best one for previous attempts and best two for top/diverse display.
    ordered = sorted(
        (programs[pid] for pid in island),
        key=lambda p: p["metrics"]["combined_score"],
        reverse=True,
    )
    observed = []

    def observe(role, program):
        observed.append(
            {
                "role": role,
                "upstream_program_id": program["id"],
                "source_sha256": hashlib.sha256(program["code"].encode()).hexdigest(),
                "representation": "summary_without_source_code"
                if role == "previous_summary"
                else "source_code",
            }
        )

    sampler = PromptSampler(
        PromptConfig(
            num_top_programs=1,
            num_diverse_programs=1,
            use_template_stochasticity=False,
            programs_as_changes_description=False,
        )
    )
    with patch.object(observer, "prompt_reference", observe):
        rendered = sampler._format_evolution_history(
            ordered[:1],
            ordered[:2],
            [programs[pid] for pid in inspirations],
            "python",
            ["complexity", "diversity"],
        )
    assert type(rendered) is str and rendered
    assert observed == predicted, {
        "count": count,
        "order": order,
        "tied": tied,
        "inspirations": inspiration_mode,
        "observed": observed,
        "predicted": predicted,
    }
    return len(observed)


def main():
    cases = references = 0
    for count in (1, 2, 3):
        for order in itertools.permutations(range(count)):
            for tied in (False, True):
                for inspiration_mode in ("none", "all", "reversed", "duplicates"):
                    references += exercise(count, order, tied, inspiration_mode)
                    cases += 1
    print(
        json.dumps(
            {
                "schema": "mineagent.prompt-structure-probe.v1",
                "actual_native_method": "PromptSampler._format_evolution_history",
                "profile": "top1_diverse1_worker_best2",
                "cases": cases,
                "observed_references": references,
                "host_rng_unchanged": True,
                "selection_unchanged": True,
                "all_ordered_references_equal": True,
                "scope": "synthetic structure only; no durable prompt receipt acceptance",
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
