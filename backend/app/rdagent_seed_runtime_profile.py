"""Operator-only seeded profile; distinct declaration from historical fresh runs.

Same numeric/Qlib dependency closure and fixed one-session workload; the exact
orchestrator image must implement the versioned seeded bootstrap. A declaration
is not an acceptance proof and cannot bypass image/child identity inspection.
"""

from typing import Literal

from backend.app.rdagent_server_profile import RDServerRuntimeProfile


class RDSeededServerRuntimeProfile(RDServerRuntimeProfile):
    schema_version: Literal["mineagent.rdagent-seeded-server-profile.v1"] = (
        "mineagent.rdagent-seeded-server-profile.v1"
    )
    profile: Literal["configured_rd_seeded_single_output_session_v1"] = (
        "configured_rd_seeded_single_output_session_v1"
    )
