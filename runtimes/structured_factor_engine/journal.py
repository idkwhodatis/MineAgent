"""Distinct direct-structured process grammar; no caller record grants authority.

Historical numeric/analysis admission bytes remain unchanged. The new reader
must resolve the real structured strategy, complete snapshot and market data;
labels and arbitrary policy source are not part of this profile.
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

PROFILE = "mineagent.factor-strategy-engine.v1"


class StructuredInputBinding(Closed):
    schema_version: Literal["mineagent.structured-factor-engine-input-binding.v1"] = (
        "mineagent.structured-factor-engine-input-binding.v1"
    )
    execution_id: UUID
    structured_strategy_version_id: UUID
    snapshot_id: UUID
    strategy_input_sha256: SHA
    strategy_input_size_bytes: int = Field(gt=0, le=1_048_576)
    market_sha256: SHA
    market_size_bytes: int = Field(gt=0, le=33_554_432)

    @property
    def files(self):
        from packages.research_runtime.process_manifests import InputBinding

        return (
            InputBinding(
                "/input/strategy.json",
                self.strategy_input_sha256,
                self.strategy_input_size_bytes,
            ),
            InputBinding(
                "/input/market.json", self.market_sha256, self.market_size_bytes
            ),
        )


class StructuredIntent(Closed):
    schema_version: Literal["mineagent.structured-factor-engine-process-intent.v1"] = (
        "mineagent.structured-factor-engine-process-intent.v1"
    )
    id: UUID
    admission: ProcessDeclarationV2
    inputs: StructuredInputBinding
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
            raise ValueError("Exact no-IPC direct structured profile required")
        if (
            self.id != a.id
            or self.inputs.execution_id != a.logical_operation_id
            or a.input_sha256 != digest(self.inputs.model_dump(mode="json"))
        ):
            raise ValueError("Structured intent binding differs")
        if (
            any(
                t.tzinfo is None or t.utcoffset() != UTC.utcoffset(t)
                for t in (self.issued_at, self.not_after)
            )
            or not 0 < (self.not_after - self.issued_at).total_seconds() <= 60
        ):
            raise ValueError("Structured deadline must be bounded UTC")
        return self


class StructuredLaunch(Closed):
    schema_version: Literal["mineagent.structured-factor-engine-process-launch.v1"] = (
        "mineagent.structured-factor-engine-process-launch.v1"
    )
    intent: StructuredIntent
    runtime_name: str
    root_device: int = Field(ge=0)
    root_inode: int = Field(gt=0)

    @model_validator(mode="after")
    def exact_name(self):
        if self.runtime_name != "mineagent-factor-strategy-" + str(
            self.intent.admission.id
        ):
            raise ValueError("Structured container name differs")
        return self


class StructuredIdentity(NumericIdentity):
    schema_version: Literal[
        "mineagent.structured-factor-engine-process-identity.v1"
    ] = "mineagent.structured-factor-engine-process-identity.v1"


class StructuredStop(NumericStop):
    schema_version: Literal["mineagent.structured-factor-engine-process-stop.v1"] = (
        "mineagent.structured-factor-engine-process-stop.v1"
    )


class StructuredExtraction(Closed):
    schema_version: Literal[
        "mineagent.structured-factor-engine-process-extraction.v1"
    ] = "mineagent.structured-factor-engine-process-extraction.v1"
    launch_sha256: SHA
    identity_sha256: SHA
    protocol: Literal["MAFS1"] = "MAFS1"
    basename: Literal["result.json"] = "result.json"
    sha256: SHA
    size_bytes: int = Field(gt=0, le=16_777_216)
    strategy_input_sha256: SHA
    market_sha256: SHA
    qualification: Literal["closed_operator_trusted_engine"] = (
        "closed_operator_trusted_engine"
    )
    protected_frame_complete: Literal[True]
    producer_quiesced: Literal[True]


def validate_structured_journal(launch, identity, stop, extraction=None):
    launch = StructuredLaunch.model_validate(launch)
    identity = StructuredIdentity.model_validate(identity)
    stop = StructuredStop.model_validate(stop)
    a = launch.intent.admission
    launch_hash = digest(launch.model_dump(mode="json"))
    identity_hash = digest(identity.model_dump(mode="json"))
    if (
        identity.launch_sha256 != launch_hash
        or identity.admission_sha256 != digest(a.model_dump(mode="json"))
        or identity.image_id != a.image_id
        or (identity.root_device, identity.root_inode)
        != (launch.root_device, launch.root_inode)
        or stop.launch_sha256 != launch_hash
        or stop.identity_sha256 != identity_hash
        or stop.container_id != identity.container_id
    ):
        raise ProcessError("structured_process_identity")
    if (
        stop.deadline_bound_met
        and not launch.intent.issued_at
        <= stop.observed_at
        <= launch.intent.not_after + timedelta(seconds=1)
    ):
        raise ProcessError("structured_process_deadline")
    if extraction is not None:
        extraction = StructuredExtraction.model_validate(extraction)
        if (
            not stop.deadline_bound_met
            or extraction.launch_sha256 != launch_hash
            or extraction.identity_sha256 != identity_hash
            or extraction.strategy_input_sha256
            != launch.intent.inputs.strategy_input_sha256
            or extraction.market_sha256 != launch.intent.inputs.market_sha256
        ):
            raise ProcessError("structured_process_extraction")
    return launch, identity, stop, extraction
