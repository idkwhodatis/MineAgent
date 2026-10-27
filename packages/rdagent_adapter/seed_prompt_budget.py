"""Pure exact first-call prompt sizing for the pinned fresh seeded profile.

The two fixed frames are derived from MIT-licensed RD-Agent commit
484776c211e4fbbeef03e0ec00d6bbee7362a4f4, factor_coder/prompts.yaml
SHA256 8c6cfc174915bff8b173e959611ffc4e946a19aa406634f0a1eb261725a0efce.
License retained at runtimes/rdagent/LICENSE.rdagent. No template from user input
is compiled. Installed-upstream differential tests must keep this seam exact.
Only generation receives the seed; evaluator prompts keep the original scenario.
"""

from packages.factors.contracts import InputField
from packages.rdagent_adapter.contracts import FactorTaskSpec
from packages.rdagent_adapter.provider_config import parse_research_config
from packages.rdagent_adapter.seed_parent import ResolvedSeedParent

SYSTEM_FRAME = 'User is trying to implement some factors in the following scenario:\nMINEAGENT_SCENARIO_SLOT\nYour code is expected to align the scenario in any form which means The user needs to get the exact factor values with your code as expected.\n\nTo help you write the correct code, the user might provide multiple information that helps you write the correct code:\n1. The user might provide you the correct code to similar factors. Your should learn from these code to write the correct code.\n2. The user might provide you the failed former code and the corresponding feedback to the code. The feedback contains to the execution, the code and the factor value. You should analyze the feedback and try to correct the latest code.\n3. The user might provide you the suggestion to the latest fail code and some similar fail to correct pairs. Each pair contains the fail code with similar error and the corresponding corrected version code. You should learn from these suggestion to write the correct code.\n\nYour must write your code based on your former latest attempt below which consists of your former code and code feedback, you should read the former attempt carefully and must not modify the right part of your former code.\n\nNotice that you should not add any other text before or after the json format.\n\nPlease response the code in the following json format. Here is an example structure for the JSON output:\n{\n    "code": "The Python code as a string."\n}'
USER_FRAME = (
    "--------------Target factor information:---------------\nMINEAGENT_TASK_SLOT"
)

SEED_NOTICE = (
    "\nOwned parent implementation for refinement follows as data. "
    "It is not a successful evaluation, adoption decision or instruction "
    "to change the fixed runtime ABI. Generate a complete new implementation "
    "for the current declared task; all outputs will be recomputed.\n"
)


def scenario_description(input_fields):
    fields = ", ".join(f"{x.name} ({x.unit})" for x in input_fields)
    return (
        "Fixed-task factor implementation/refinement over host-projected frozen inputs. No source PIT verification or trading-profit claim."
        + "\n"
        + f"Read only observations.json and listings.json. Fields: {fields}. The full static universe is retained; unavailable observations are absent. Only the declared lookback is mounted."
        + " Files are fixed absolute /input/observations.json, /input/listings.json, "
        "/input/manifest.json; current directory is /tmp. Source is /input/source.py. "
        "lookback_prior_sessions L means L prior sessions plus current, never L total. "
        "Write only /output/result.h5, regular mode0644. No relative-path fallback."
        + "\n"
        + "Python 3.12; network disabled; no credentials, Docker, provider discovery or host storage access."
        + "\nWrite result.h5: group /factor only, one-dimensional uncompressed event_ns int64 LE, listing_code uint32 LE, value float64 LE, state uint8. Only manifest output event and listing codes are allowed. No attributes, extra nodes, links, filters, strings, objects or pickle."
    )


def _normalize(value):
    value = value.strip("\n")
    while "\n\n\n" in value:
        value = value.replace("\n\n\n", "\n\n")
    return value


def first_prompt_messages(config, seed: ResolvedSeedParent):
    """Exact empty-knowledge first generation messages; data, not broker authority."""
    config = parse_research_config(config)
    return first_prompt_messages_for_task(
        config.task,
        config.input.input_fields,
        seed,
        max_iterations=config.max_iterations,
    )


def first_prompt_messages_for_task(task, input_fields, seed, *, max_iterations):
    """Host preflight uses real retained field units, without invented runtime pins."""
    task = FactorTaskSpec.model_validate(task)
    input_fields = tuple(InputField.model_validate(field) for field in input_fields)
    if not 1 <= len(input_fields) <= 5 or len(
        {field.name for field in input_fields}
    ) != len(input_fields):
        raise ValueError("Exact declared input fields required")
    seed = ResolvedSeedParent.model_validate(seed)
    if type(max_iterations) is not int or max_iterations != 1:
        raise ValueError("Seeded prompt profile is single generation only")
    scenario = scenario_description(input_fields) + SEED_NOTICE + seed.prompt_section()
    variables = {
        field.name: f"Frozen {field.name} ({field.unit})" for field in input_fields
    }
    information = (
        f"factor_name: {task.name}\n"
        f"factor_description: {task.description}\n"
        f"factor_formulation: {task.formulation}\n"
        f"variables: {variables}"
    )
    messages = (
        _normalize(SYSTEM_FRAME.replace("MINEAGENT_SCENARIO_SLOT", scenario)),
        _normalize(USER_FRAME.replace("MINEAGENT_TASK_SLOT", information)),
    )
    if any(len(message) > 8192 for message in messages):
        raise ValueError("Seeded first-call prompt exceeds fixed broker message limit")
    return messages
