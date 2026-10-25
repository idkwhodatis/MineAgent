"""Only the neutral fixture unit is registered before engine activation."""

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class ProcessProfile:
    role: str
    memory_bytes: int
    pids: int
    wall_seconds: int
    tmpfs_bytes: int
    shm_bytes: int

    @property
    def reservation(self):
        from packages.research_runtime.budget_contracts import BudgetVector

        return BudgetVector(
            process_slots=1,
            wall_milliseconds=self.wall_seconds * 1000,
            storage_bytes=self.tmpfs_bytes + self.shm_bytes + 65536,
        )


PROFILES = MappingProxyType(
    {
        "neutral_fixture.v1": ProcessProfile(
            "neutral_fixture", 256 * 1024**2, 16, 60, 1024**2, 65536
        )
    }
)


def profile_for(name, role):
    from packages.research_runtime.process_contracts import ProcessError

    profile = PROFILES.get(name)
    if profile is None or profile.role != role:
        raise ProcessError("research_process_profile")
    return profile


def require_configured_image(image_id):
    """Only the installation's explicitly pinned fixture image may create work."""
    import os
    import re

    from packages.research_runtime.process_contracts import ProcessError

    configured = os.environ.get("MINEAGENT_RESEARCH_SUPERVISOR_IMAGE", "")
    if (
        re.fullmatch(r"sha256:[a-f0-9]{64}", configured) is None
        or image_id != configured
    ):
        raise ProcessError("research_process_image")
