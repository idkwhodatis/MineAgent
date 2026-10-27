"""Versioned numeric persistence grammar, not a launch or extraction authority.

Only protected supervisor records can be ingested. Public launch entrypoints stay
closed. This module never parses native output and never infers stop from EOF.
"""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.research_runtime.process_contracts import ProcessError, digest
from packages.research_runtime.process_declarations_v2 import (
    SHA,
    Closed,
    NoIPC,
    ProcessDeclarationV2,
)

PROFILES = {
    "factor_cutoff_candidate": "mineagent.factor-prefix-candidate.v2",
    "factor_normalizer": "mineagent.factor-numeric-hdf.v1",
}
MAX_OUTPUT = 16_777_216


def numeric_admission(value):
    value = ProcessDeclarationV2.model_validate(value)
    if not isinstance(value.ipc, NoIPC) or PROFILES.get(value.role) != value.profile:
        raise ProcessError("numeric_process_profile")
    return value


class NumericInputFile(Closed):
    destination: str
    sha256: SHA
    size_bytes: int = Field(ge=0, le=16_777_216)


class NumericInput(Closed):
    schema_version: Literal["mineagent.numeric-process-input.v2"]
    source_object_id: UUID
    source_sha256: SHA
    prefix_sha256: SHA
    cutoff: datetime
    files: tuple[NumericInputFile, ...] = Field(min_length=3, max_length=4)

    @model_validator(mode="after")
    def canonical_inputs(self):
        if self.cutoff.tzinfo is None or self.cutoff.utcoffset() != UTC.utcoffset(
            self.cutoff
        ):
            raise ValueError("Numeric cutoff must be UTC")
        names = tuple(item.destination for item in self.files)
        if names != tuple(sorted(set(names))):
            raise ValueError("Input inventory must be sorted and unique")
        return self


class NumericIntent(Closed):
    schema_version: Literal["mineagent.numeric-process-intent.v2"]
    id: UUID
    admission: ProcessDeclarationV2
    inputs: NumericInput
    issued_at: datetime
    not_after: datetime

    @model_validator(mode="after")
    def exact_binding(self):
        admission = numeric_admission(self.admission)
        if admission.input_sha256 != digest(self.inputs.model_dump(mode="json")):
            raise ValueError("Numeric full input commitment differs")
        from packages.rdagent_adapter.numeric_profile import (
            CANDIDATE_LIMITS,
            NORMALIZER_LIMITS,
        )

        limits = (
            CANDIDATE_LIMITS
            if admission.role == "factor_cutoff_candidate"
            else NORMALIZER_LIMITS
        )
        if {item.destination for item in self.inputs.files} != set(limits):
            raise ValueError("Numeric role input inventory differs")
        files = {item.destination: item for item in self.inputs.files}
        if (
            admission.role == "factor_cutoff_candidate"
            and files["/input/manifest.json"].sha256 != self.inputs.prefix_sha256
        ):
            raise ValueError("Numeric prefix commitment differs")
        if (
            admission.role == "factor_cutoff_candidate"
            and files["/input/source.py"].sha256 != self.inputs.source_sha256
        ):
            raise ValueError("Numeric source commitment differs")
        if any(
            item.size_bytes > limits[item.destination] for item in self.inputs.files
        ):
            raise ValueError("Numeric input exceeds profile")
        if any(
            t.tzinfo is None or t.utcoffset() != UTC.utcoffset(t)
            for t in (self.issued_at, self.not_after)
        ):
            raise ValueError("Numeric deadline must be UTC")
        if not 0 < (self.not_after - self.issued_at).total_seconds() <= 10:
            raise ValueError("Numeric deadline exceeds profile")
        return self


class NumericLaunch(Closed):
    schema_version: Literal["mineagent.numeric-process-launch.v2"]
    intent: NumericIntent
    runtime_name: str
    root_device: int = Field(ge=0)
    root_inode: int = Field(gt=0)

    @model_validator(mode="after")
    def unique_name(self):
        if self.runtime_name != "mineagent-numeric-" + str(self.intent.admission.id):
            raise ValueError("Numeric runtime identity differs")
        return self


class NumericIdentity(Closed):
    schema_version: Literal["mineagent.numeric-process-identity.v2"]
    admission_sha256: SHA
    launch_sha256: SHA
    container_id: SHA
    image_id: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    root_device: int = Field(ge=0)
    root_inode: int = Field(gt=0)
    init_pid: int = Field(gt=0)
    init_start_ticks: int = Field(gt=0)
    boot_id: UUID
    pid_namespace_inode: int = Field(gt=0)


class NumericExtraction(Closed):
    schema_version: Literal["mineagent.numeric-process-extraction.v2"]
    launch_sha256: SHA
    identity_sha256: SHA
    protocol: Literal["MANU2"]
    basename: Literal["result.h5", "values.parquet"]
    sha256: SHA
    size_bytes: int = Field(gt=0, le=MAX_OUTPUT)
    protected_frame_complete: Literal[True]
    producer_quiesced: Literal[True]


class NumericReportExtraction(NumericExtraction):
    schema_version: Literal["mineagent.numeric-process-extraction.v3"]
    protocol: Literal["MANU2-report-v1"]
    basename: Literal["values.parquet"]
    report_basename: Literal["rows.json"]
    report_sha256: SHA
    report_size_bytes: int = Field(gt=0, le=MAX_OUTPUT)
    input_manifest_sha256: SHA
    listings_sha256: SHA

    @model_validator(mode="after")
    def combined_output_bound(self):
        if self.size_bytes + self.report_size_bytes > MAX_OUTPUT:
            raise ValueError("Combined numeric output exceeds profile")
        return self


def parse_numeric_extraction(value):
    if isinstance(value, (NumericExtraction, NumericReportExtraction)):
        value = value.model_dump(mode="json")
    if type(value) is not dict:
        raise ValueError("Numeric extraction must be a closed record")
    model = (
        NumericReportExtraction
        if value.get("schema_version") == "mineagent.numeric-process-extraction.v3"
        else NumericExtraction
    )
    return model.model_validate(value)


class NumericStop(Closed):
    schema_version: Literal["mineagent.numeric-process-stop.v2"]
    launch_sha256: SHA
    identity_sha256: SHA
    container_id: SHA
    confirmed_absent: Literal[True]
    kernel_identity_absent: Literal[True]
    observed_at: datetime
    deadline_bound_met: bool

    @model_validator(mode="after")
    def aware_observation(self):
        if (
            self.observed_at.tzinfo is None
            or self.observed_at.utcoffset() != UTC.utcoffset(self.observed_at)
        ):
            raise ValueError("Numeric stop observation must be UTC")
        return self


def validate_numeric_journal(launch, identity, stop, extraction=None):
    """Cross-record validation; does not turn caller records into runtime proof."""
    launch = NumericLaunch.model_validate(launch)
    identity = NumericIdentity.model_validate(identity)
    stop = NumericStop.model_validate(stop)
    declaration = launch.intent.admission
    launch_hash = digest(launch.model_dump(mode="json"))
    identity_hash = digest(identity.model_dump(mode="json"))
    if (
        identity.root_device != launch.root_device
        or identity.root_inode != launch.root_inode
        or identity.launch_sha256 != launch_hash
        or identity.admission_sha256 != digest(declaration.model_dump(mode="json"))
        or identity.image_id != declaration.image_id
        or stop.launch_sha256 != launch_hash
        or stop.identity_sha256 != identity_hash
        or stop.container_id != identity.container_id
    ):
        raise ProcessError("numeric_process_journal_identity")
    if (
        stop.deadline_bound_met
        and not launch.intent.issued_at
        <= stop.observed_at
        <= launch.intent.not_after + timedelta(seconds=1)
    ):
        raise ProcessError("numeric_process_deadline_observation")
    if extraction is not None:
        extraction = parse_numeric_extraction(extraction)
        if isinstance(extraction, NumericReportExtraction):
            files = {item.destination: item for item in launch.intent.inputs.files}
            if (
                declaration.role != "factor_normalizer"
                or extraction.input_manifest_sha256
                != files["/input/manifest.json"].sha256
                or extraction.listings_sha256 != files["/input/listings.json"].sha256
            ):
                raise ProcessError("numeric_report_input_identity")
        name = (
            "result.h5"
            if declaration.role == "factor_cutoff_candidate"
            else "values.parquet"
        )
        if (
            extraction.launch_sha256 != launch_hash
            or extraction.identity_sha256 != identity_hash
            or extraction.basename != name
        ):
            raise ProcessError("numeric_process_extraction_identity")
    return launch, identity, stop, extraction
