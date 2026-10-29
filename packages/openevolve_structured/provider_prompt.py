"""Fixed normal OE JSON prompt settings; no provider/endpoint selection here."""

SYSTEM_MESSAGE = (
    "Return one complete canonical JSON structured candidate, not Python, "
    "not a patch, and not markdown. Use sorted keys, compact separators and finite numbers. "
    "Preserve every current program field except threshold, top_k, maximum_weight and "
    "maximum_gross_weight. Only these four bounded parameters may change. "
    "The completed factor snapshot, market, execution window and objective are fixed. "
    "Do not invent factor values, labels, fills or evaluation metrics."
)


def prompt_config():
    from openevolve.config import PromptConfig

    return PromptConfig(
        system_message=SYSTEM_MESSAGE,
        num_top_programs=1,
        num_diverse_programs=1,
        use_template_stochasticity=False,
        programs_as_changes_description=False,
        use_meta_prompting=False,
        include_artifacts=True,
        max_artifact_bytes=8192,
    )
