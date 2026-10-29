"""Closed stdlib-only controller limits for trusted PID1; no authority."""

LEGACY_CONTROLLER_PROFILE = "mineagent.rdagent-orchestrator.v2"
COMPOSED_CONTROLLER_PROFILE = "mineagent.rdagent-orchestrator.v3"
SEEDED_CONTROLLER_PROFILE = "mineagent.rdagent-orchestrator.v4"


def controller_wall_milliseconds(profile):
    if type(profile) is not str:
        raise ValueError("Exact controller profile required")
    if profile == LEGACY_CONTROLLER_PROFILE:
        return 60000
    if profile == COMPOSED_CONTROLLER_PROFILE:
        return 120000
    if profile == SEEDED_CONTROLLER_PROFILE:
        return 180000
    raise ValueError("Unsupported controller profile")


def controller_wall_seconds(profile):
    return controller_wall_milliseconds(profile) // 1000


def composed_wall_floor(profile):
    # The three independently bounded child units are unchanged at 60 seconds.
    return controller_wall_milliseconds(profile) + 180000
