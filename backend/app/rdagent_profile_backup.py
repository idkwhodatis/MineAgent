"""Exact historical cold-profile gates, never selected by current settings.

Missing controller_profile retains the old grammar. New v4 authority requires
research-v12/RD-v7; those closures can also retain old v2/v3 history unchanged.
"""

RESEARCH = "mineagent.research-restore-closure.v11"
SOURCE = "mineagent.rd-source-restore-closure.v6"
PROFILE = "mineagent.rdagent-orchestrator.v3"
SEEDED_RESEARCH = "mineagent.research-restore-closure.v12"
SEEDED_SOURCE = "mineagent.rd-source-restore-closure.v7"
SEEDED_PROFILE = "mineagent.rdagent-orchestrator.v4"


def require_config_revision(config, *, research_schema=None, source_schema=None):
    if type(config) is not dict:
        raise ValueError("Exact retained RD configuration required")
    if "controller_profile" not in config:
        return
    pairs = {
        PROFILE: {(RESEARCH, SOURCE), (SEEDED_RESEARCH, SEEDED_SOURCE)},
        SEEDED_PROFILE: {(SEEDED_RESEARCH, SEEDED_SOURCE)},
    }.get(
        config["controller_profile"]
        if type(config["controller_profile"]) is str
        else ""
    )
    if (
        (research_schema is None and source_schema is None)
        or pairs is None
        or not any(
            (research_schema is None or research_schema == research)
            and (source_schema is None or source_schema == source)
            for research, source in pairs
        )
    ):
        raise ValueError("Controller profile requires its exact versioned closure")


def require_admission_revision(profile, research_schema):
    if profile == PROFILE and research_schema not in {RESEARCH, SEEDED_RESEARCH}:
        raise ValueError("Controller v3 admission requires research-v11 or v12 closure")
    if profile == SEEDED_PROFILE and research_schema != SEEDED_RESEARCH:
        raise ValueError("Seeded controller v4 admission requires research-v12 closure")
