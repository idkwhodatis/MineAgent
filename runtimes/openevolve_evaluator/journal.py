"""OE native process records, not reusable execution or financial authority.

The small durable intent binds both the retained scope-bearing inventory object
and the unchanged native engine's artifact-list commitment. The full inventory
is separately retained and re-resolved before each physical mutation.
"""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.research_runtime.numeric_process_journal import (
    NumericIdentity,
    NumericStop,
)
from packages.research_runtime.process_contracts import ProcessError, digest
from packages.research_runtime.process_declarations_v2 import (
    SHA,
    Closed,
    NoIPC,
    ProcessDeclarationV2,
)
from runtimes.openevolve_evaluator.contracts import PROFILE, NativeInputManifest


class NativeInputBinding(Closed):
    schema_version: Literal["mineagent.openevolve-native-input-binding.v1"] = (
        "mineagent.openevolve-native-input-binding.v1"
    )
    evaluation_id: UUID
    manifest_object_id: UUID
    manifest_sha256: SHA
    manifest_size_bytes: int = Field(gt=0, le=65536)
    native_artifacts_sha256: SHA
    execution_plan_sha256: SHA
    source_sha256: SHA

    def require_manifest(self, manifest: NativeInputManifest):
        # Reparse even an existing instance; forged model_copy is not authority.
        manifest = NativeInputManifest.model_validate(manifest)
        from packages.research_runtime.process_contracts import canonical

        if (
            manifest.evaluation_id != self.evaluation_id
            or manifest.sha256 != self.manifest_sha256
            or len(canonical(manifest.model_dump(mode="json")))
            != self.manifest_size_bytes
            or manifest.native_artifacts_sha256 != self.native_artifacts_sha256
            or manifest.execution_plan_sha256 != self.execution_plan_sha256
            or manifest.source_sha256 != self.source_sha256
        ):
            raise ProcessError("oe_native_manifest_binding")
        return manifest


class NativeIntent(Closed):
    schema_version: Literal["mineagent.openevolve-native-process-intent.v1"] = (
        "mineagent.openevolve-native-process-intent.v1"
    )
    id: UUID
    admission: ProcessDeclarationV2
    inputs: NativeInputBinding
    issued_at: datetime
    not_after: datetime

    @model_validator(mode="after")
    def bindings(self):
        a = self.admission
        if (
            a.role != "native_evaluator"
            or a.profile != PROFILE
            or not isinstance(a.ipc, NoIPC)
        ):
            raise ValueError("Exact OE no-IPC native profile required")
        if (
            self.id != a.id
            or self.inputs.evaluation_id != a.logical_operation_id
            or a.input_sha256 != digest(self.inputs.model_dump(mode="json"))
        ):
            raise ValueError("OE native input commitment differs")
        if (
            any(
                t.tzinfo is None or t.utcoffset() != UTC.utcoffset(t)
                for t in (self.issued_at, self.not_after)
            )
            or not 0 < (self.not_after - self.issued_at).total_seconds() <= 60
        ):
            raise ValueError("Bounded UTC OE native deadline required")
        return self


class NativeLaunch(Closed):
    schema_version: Literal["mineagent.openevolve-native-process-launch.v1"] = (
        "mineagent.openevolve-native-process-launch.v1"
    )
    intent: NativeIntent
    runtime_name: str
    root_device: int = Field(ge=0)
    root_inode: int = Field(gt=0)

    @model_validator(mode="after")
    def name(self):
        if self.runtime_name != "mineagent-oe-native-" + str(self.intent.id):
            raise ValueError("OE native container name differs")
        return self


class NativeIdentity(NumericIdentity):
    schema_version: Literal["mineagent.openevolve-native-process-identity.v1"] = (
        "mineagent.openevolve-native-process-identity.v1"
    )


class NativeStop(NumericStop):
    schema_version: Literal["mineagent.openevolve-native-process-stop.v1"] = (
        "mineagent.openevolve-native-process-stop.v1"
    )


class NativeExtraction(Closed):
    schema_version: Literal["mineagent.openevolve-native-process-extraction.v1"] = (
        "mineagent.openevolve-native-process-extraction.v1"
    )
    launch_sha256: SHA
    identity_sha256: SHA
    protocol: Literal["MAOE1"] = "MAOE1"
    basename: Literal["native-export.bin"] = "native-export.bin"
    sha256: SHA
    size_bytes: int = Field(gt=0, le=16 * 1024**2)
    manifest_sha256: SHA
    native_artifacts_sha256: SHA
    protected_frame_complete: Literal[True]
    producer_quiesced: Literal[True]
    qualification: Literal["native_observation_not_producer_proof"] = (
        "native_observation_not_producer_proof"
    )


def validate_native_journal(launch, identity, stop, extraction=None):
    launch = NativeLaunch.model_validate(launch)
    identity = NativeIdentity.model_validate(identity)
    stop = NativeStop.model_validate(stop)
    a = launch.intent.admission
    lh = digest(launch.model_dump(mode="json"))
    ih = digest(identity.model_dump(mode="json"))
    if (
        identity.launch_sha256 != lh
        or identity.admission_sha256 != digest(a.model_dump(mode="json"))
        or identity.image_id != a.image_id
        or (identity.root_device, identity.root_inode)
        != (launch.root_device, launch.root_inode)
        or stop.launch_sha256 != lh
        or stop.identity_sha256 != ih
        or stop.container_id != identity.container_id
    ):
        raise ProcessError("oe_native_process_identity")
    if (
        stop.deadline_bound_met
        and not launch.intent.issued_at
        <= stop.observed_at
        <= launch.intent.not_after + timedelta(seconds=1)
    ):
        raise ProcessError("oe_native_process_deadline")
    if extraction is not None:
        extraction = NativeExtraction.model_validate(extraction)
        if (
            not stop.deadline_bound_met
            or extraction.launch_sha256 != lh
            or extraction.identity_sha256 != ih
            or extraction.manifest_sha256 != launch.intent.inputs.manifest_sha256
            or extraction.native_artifacts_sha256
            != launch.intent.inputs.native_artifacts_sha256
        ):
            raise ProcessError("oe_native_process_extraction")
    return launch, identity, stop, extraction
