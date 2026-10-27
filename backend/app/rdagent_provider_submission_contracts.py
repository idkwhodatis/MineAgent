"""Explicit user model selection; endpoint/credentials remain operator-owned."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from backend.app.engine_run_submission_schemas import (
    RDPreflight,
    RDSubmissionReceipt,
    RDSubmissionRequest,
    ReasonCode,
)
from backend.app.rdagent_seed_contracts import RDSeededSubmissionReceipt
from packages.factors.contracts import Contract, Sha256
from packages.rdagent_adapter.seed_parent import SeedParentReference
from packages.rdagent_adapter.steer import SteerSnapshot
from packages.rdagent_provider.contracts import RDModelBinding

CATALOG = "configured_rd_model_single_output_session_v1"


class RDProviderSubmissionRequest(RDSubmissionRequest):
    schema_version: Literal["mineagent.rdagent-provider-submission.v1"] = (
        "mineagent.rdagent-provider-submission.v1"
    )
    model_profile_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    selected_steer_version_ids: tuple[UUID, ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def distinct_selection(self):
        if len(set(self.selected_steer_version_ids)) != len(
            self.selected_steer_version_ids
        ):
            raise ValueError("Selected steer versions must be distinct and ordered")
        return self

    def research_declaration(self):
        return RDSubmissionRequest.model_validate(
            {
                **self.model_dump(
                    exclude={
                        "schema_version",
                        "model_profile_id",
                        "selected_steer_version_ids",
                        "parent_factor_version_id",
                    }
                ),
                "schema_version": "mineagent.rdagent-submission.v1",
            }
        )


class RDProviderSeedRequest(RDProviderSubmissionRequest):
    schema_version: Literal["mineagent.rdagent-provider-seeded-submission.v1"] = (
        "mineagent.rdagent-provider-seeded-submission.v1"
    )
    parent_factor_version_id: UUID


class RDProviderSubmit(RDProviderSubmissionRequest):
    preflight_sha256: Sha256

    def request(self):
        return RDProviderSubmissionRequest.model_validate(
            self.model_dump(exclude={"preflight_sha256"})
        )


class RDProviderSeedSubmit(RDProviderSeedRequest):
    preflight_sha256: Sha256

    def request(self):
        return RDProviderSeedRequest.model_validate(
            self.model_dump(exclude={"preflight_sha256"})
        )


class ProviderPreflightReason(Contract):
    code: (
        ReasonCode
        | Literal[
            "rd_model_unavailable",
            "rd_provider_disabled",
            "rd_model_pricing_unavailable",
            "model_prompt_unsupported",
            "seed_parent_unsupported",
        ]
    )
    message: str = Field(min_length=1, max_length=320)


class RDProviderPreflight(RDPreflight):
    schema_version: Literal["mineagent.rdagent-provider-preflight.v1"] = (
        "mineagent.rdagent-provider-preflight.v1"
    )
    normalized_request: RDProviderSubmissionRequest | RDProviderSeedRequest
    model_profile: Literal["configured.rdagent.v1"] = "configured.rdagent.v1"
    runtime_profile: Literal["configured_rd_model_single_output_session_v1"] = CATALOG
    model_binding: RDModelBinding | None
    model_binding_sha256: Sha256 | None
    steer_snapshot: SteerSnapshot
    steer_snapshot_sha256: Sha256
    parent: SeedParentReference | None
    parent_binding_sha256: Sha256 | None
    reasons: tuple[ProviderPreflightReason, ...]

    @model_validator(mode="after")
    def exact_frozen_bindings(self):
        expected = None if self.model_binding is None else self.model_binding.sha256
        if self.model_binding_sha256 != expected:
            raise ValueError("Preflight model binding digest differs")
        if self.steer_snapshot_sha256 != self.steer_snapshot.sha256:
            raise ValueError("Preflight steer snapshot digest differs")
        return self


class RDProviderSubmissionReceipt(RDSubmissionReceipt):
    schema_version: Literal["mineagent.rdagent-provider-submission-receipt.v1"] = (
        "mineagent.rdagent-provider-submission-receipt.v1"
    )
    model_binding_sha256: Sha256


class RDProviderSeedReceipt(RDSeededSubmissionReceipt):
    schema_version: Literal[
        "mineagent.rdagent-provider-seeded-submission-receipt.v1"
    ] = "mineagent.rdagent-provider-seeded-submission-receipt.v1"
    model_binding_sha256: Sha256


def parse_provider_request(document):
    if type(document) is not dict:
        raise ValueError("Exact provider request object required")
    classes = {
        "mineagent.rdagent-provider-submission.v1": RDProviderSubmissionRequest,
        "mineagent.rdagent-provider-seeded-submission.v1": RDProviderSeedRequest,
    }
    version = document.get("schema_version")
    if type(version) is not str or version not in classes:
        raise ValueError("Unknown provider submission version")
    return classes[version].model_validate(document)


def provider_commitment(
    owner_id,
    request,
    *,
    dataset_sha256,
    input_sha256,
    profile_sha256,
    model_binding_sha256,
    steer_snapshot_sha256,
    parent_binding_sha256=None,
):
    from packages.factors.contracts import digest

    return digest(
        {
            "schema_version": "mineagent.rdagent-provider-preflight-binding.v1",
            "owner_id": str(owner_id),
            "request_sha256": request.sha256,
            "dataset_content_sha256": dataset_sha256,
            "frozen_input_sha256": input_sha256,
            "catalog_revision": CATALOG,
            "runtime_profile_sha256": profile_sha256,
            "model_binding_sha256": model_binding_sha256,
            "steer_snapshot_sha256": steer_snapshot_sha256,
            "parent_binding_sha256": parent_binding_sha256,
        }
    )
