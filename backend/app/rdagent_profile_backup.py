"""Exact 027 cold-profile gates, separate from the global live config parser.

An old closure must never reinterpret a new profile even if its process has no
remaining journal rows. Missing controller_profile retains the old grammar.
"""

RESEARCH = "mineagent.research-restore-closure.v11"
SOURCE = "mineagent.rd-source-restore-closure.v6"
PROFILE = "mineagent.rdagent-orchestrator.v3"


def require_config_revision(config, *, research_schema=None, source_schema=None):
    if type(config) is not dict:
        raise ValueError("Exact retained RD configuration required")
    if "controller_profile" not in config:
        return
    if (
        config["controller_profile"] != PROFILE
        or (research_schema is not None and research_schema != RESEARCH)
        or (source_schema is not None and source_schema != SOURCE)
        or (research_schema is None and source_schema is None)
    ):
        raise ValueError("Controller profile requires exact research-v11/RD-v6 closure")


def require_admission_revision(profile, research_schema):
    if profile == PROFILE and research_schema != RESEARCH:
        raise ValueError("Controller v3 admission requires research-v11 closure")
