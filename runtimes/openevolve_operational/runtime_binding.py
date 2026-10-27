"""Host-measured protected evaluator provenance bound before controller admission.

Fields are evidence commitments, not a runtime registry or caller-chosen launcher
arguments. The host must resolve the exact image inventory and verify both parent
and wrapper build provenance before persisting a bootstrap carrying this object.
"""

from typing import Literal

from pydantic import Field

from packages.openevolve_adapter.contracts import Contract


class NativeRuntimeBinding(Contract):
    schema_version: Literal["mineagent.openevolve-native-runtime-binding.v1"] = (
        "mineagent.openevolve-native-runtime-binding.v1"
    )
    profile: Literal["mineagent.openevolve-native-evaluator.v1"] = (
        "mineagent.openevolve-native-evaluator.v1"
    )
    native_parent_image_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    protected_image_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    dependency_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    wrapper_inventory_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
