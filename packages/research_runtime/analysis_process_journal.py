"""Distinct direct-analysis process grammar; no caller record grants authority.

Historical numeric admission/journal bytes are intentionally unchanged. The
analysis reader must also resolve the complete snapshot and retained labels.
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

PROFILE = "mineagent.qlib-direct-analysis.v1"


class AnalysisInputBinding(Closed):
    schema_version: Literal["mineagent.qlib-analysis-input-binding.v1"] = (
        "mineagent.qlib-analysis-input-binding.v1"
    )
    analysis_id: UUID
    snapshot_id: UUID
    label_source_id: UUID
    protocol_sha256: SHA
    analysis_input_sha256: SHA
    analysis_input_size_bytes: int = Field(gt=0, le=1_048_576)

    @property
    def files(self):
        from packages.research_runtime.process_manifests import InputBinding

        return (
            InputBinding(
                "/input/analysis.json",
                self.analysis_input_sha256,
                self.analysis_input_size_bytes,
            ),
        )


class AnalysisIntent(Closed):
    schema_version: Literal["mineagent.qlib-analysis-process-intent.v1"] = (
        "mineagent.qlib-analysis-process-intent.v1"
    )
    id: UUID
    admission: ProcessDeclarationV2
    inputs: AnalysisInputBinding
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
            raise ValueError("Exact no-IPC direct analysis profile required")
        if (
            self.id != a.id
            or self.inputs.analysis_id != a.logical_operation_id
            or a.input_sha256 != digest(self.inputs.model_dump(mode="json"))
        ):
            raise ValueError("Analysis intent binding differs")
        if (
            any(
                t.tzinfo is None or t.utcoffset() != UTC.utcoffset(t)
                for t in (self.issued_at, self.not_after)
            )
            or not 0 < (self.not_after - self.issued_at).total_seconds() <= 10
        ):
            raise ValueError("Analysis deadline must be bounded UTC")
        return self


class AnalysisLaunch(Closed):
    schema_version: Literal["mineagent.qlib-analysis-process-launch.v1"] = (
        "mineagent.qlib-analysis-process-launch.v1"
    )
    intent: AnalysisIntent
    runtime_name: str
    root_device: int = Field(ge=0)
    root_inode: int = Field(gt=0)

    @model_validator(mode="after")
    def exact_name(self):
        if self.runtime_name != "mineagent-analysis-" + str(self.intent.admission.id):
            raise ValueError("Analysis container name differs")
        return self


class AnalysisIdentity(NumericIdentity):
    schema_version: Literal["mineagent.qlib-analysis-process-identity.v1"] = (
        "mineagent.qlib-analysis-process-identity.v1"
    )


class AnalysisStop(NumericStop):
    schema_version: Literal["mineagent.qlib-analysis-process-stop.v1"] = (
        "mineagent.qlib-analysis-process-stop.v1"
    )


class AnalysisExtraction(Closed):
    schema_version: Literal["mineagent.qlib-analysis-process-extraction.v1"] = (
        "mineagent.qlib-analysis-process-extraction.v1"
    )
    launch_sha256: SHA
    identity_sha256: SHA
    protocol: Literal["MAQA1"] = "MAQA1"
    basename: Literal["analysis.json"] = "analysis.json"
    sha256: SHA
    size_bytes: int = Field(gt=0, le=1_048_576)
    analysis_input_sha256: SHA
    protocol_sha256: SHA
    protected_frame_complete: Literal[True]
    producer_quiesced: Literal[True]


def validate_analysis_journal(launch, identity, stop, extraction=None):
    launch = AnalysisLaunch.model_validate(launch)
    identity = AnalysisIdentity.model_validate(identity)
    stop = AnalysisStop.model_validate(stop)
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
        raise ProcessError("analysis_process_identity")
    if (
        stop.deadline_bound_met
        and not launch.intent.issued_at
        <= stop.observed_at
        <= launch.intent.not_after + timedelta(seconds=1)
    ):
        raise ProcessError("analysis_process_deadline")
    if extraction is not None:
        extraction = AnalysisExtraction.model_validate(extraction)
        if (
            not stop.deadline_bound_met
            or extraction.launch_sha256 != launch_hash
            or extraction.identity_sha256 != identity_hash
            or extraction.analysis_input_sha256
            != launch.intent.inputs.analysis_input_sha256
            or extraction.protocol_sha256 != launch.intent.inputs.protocol_sha256
        ):
            raise ProcessError("analysis_process_extraction")
    return launch, identity, stop, extraction
