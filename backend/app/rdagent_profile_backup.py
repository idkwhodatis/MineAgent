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
    seeded_pairs = {
        (SEEDED_RESEARCH, SEEDED_SOURCE),
        (
            "mineagent.research-restore-closure.v13",
            "mineagent.rd-source-restore-closure.v8",
        ),
        (
            "mineagent.research-restore-closure.v14",
            "mineagent.rd-source-restore-closure.v9",
        ),
        (
            "mineagent.research-restore-closure.v15",
            "mineagent.rd-source-restore-closure.v10",
        ),
        (
            "mineagent.research-restore-closure.v16",
            "mineagent.rd-source-restore-closure.v11",
        ),
    }
    pairs = {
        PROFILE: {(RESEARCH, SOURCE)} | seeded_pairs,
        SEEDED_PROFILE: seeded_pairs,
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
    if profile in {PROFILE, SEEDED_PROFILE}:
        require_config_revision(
            {"controller_profile": profile}, research_schema=research_schema
        )
