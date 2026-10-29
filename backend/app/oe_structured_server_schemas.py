"""Operator-only normal027 runtime pins, free of service/DB imports."""

from typing import Literal

from packages.openevolve_structured.candidate import Closed
from packages.openevolve_structured.provider_run import StructuredRuntimePins


class OEStructuredServerProfile(Closed):
    schema_version: Literal["mineagent.oe-structured-provider-server.v1"] = (
        "mineagent.oe-structured-provider-server.v1"
    )
    runtime: StructuredRuntimePins
