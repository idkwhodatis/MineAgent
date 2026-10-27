"""Immutable accepted-request replay, independent of current runtime availability.

No byte-source revalidation or runtime lookup occurs on an exact accepted replay.
This is a receipt reader, not authority to create an attempt or launch a process.
"""

from hashlib import sha256

from sqlalchemy import select

from backend.app.engine_run_submission_schemas import (
    RDSubmissionReceipt,
    RDSubmissionRequest,
)
from backend.app.factor_analysis_models import FactorAnalysisPlan
from backend.app.models import ResearchRun
from backend.app.rdagent_submission_models import FactorResearchSubmission
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.rdagent_adapter.persisted_identity import require_source_identity
from packages.strategy_sdk import JsonLimits, parse_json


def validate_key(key):
    if (
        type(key) is not str
        or not 1 <= len(key) <= 128
        or any(not 33 <= ord(c) <= 126 for c in key)
    ):
        raise DomainError(
            "invalid_idempotency_key",
            "Idempotency-Key must be 1 to 128 printable ASCII characters without spaces",
            422,
        )
    return key


def read_request(row):
    """Parse the retained canonical bytes, preserving signed zero/float spelling."""
    try:
        raw = row.request_canonical.encode("utf-8")
        parsed = parse_json(
            raw, limits=JsonLimits(max_bytes=131072, max_nodes=10000, max_items=256)
        )
        if parsed.get("schema_version") == "mineagent.rdagent-provider-submission.v1":
            from backend.app.rdagent_provider_submission_contracts import (
                RDProviderSubmissionRequest,
            )

            request = RDProviderSubmissionRequest.model_validate(parsed)
        else:
            request = RDSubmissionRequest.model_validate(parsed)
        if (
            canonical_bytes(request) != raw
            or sha256(raw).hexdigest() != row.request_sha256
        ):
            raise ValueError("request commitment")
        return request
    except (ValueError, TypeError, AttributeError, UnicodeError):
        raise DomainError(
            "submission_receipt_drift", "Retained submission commitment is invalid", 409
        ) from None


def existing_receipt(session, owner_id, key, submitted):
    """Call before a current catalog gate; exact replay remains readable after stop."""
    key = validate_key(key)
    row = session.scalar(
        select(FactorResearchSubmission).filter_by(
            owner_id=owner_id, idempotency_key=key
        )
    )
    if row is None:
        # ResearchRun's shared owner/key namespace cannot be reused by another
        # engine or a historical acceptance fixture lacking a submission receipt.
        previous = session.scalar(
            select(ResearchRun).filter_by(owner_id=owner_id, idempotency_key=key)
        )
        if previous is not None:
            raise DomainError(
                "idempotency_conflict",
                "This key already identifies another request",
                409,
            )
        return None
    saved = read_request(row)
    if (
        canonical_bytes(saved) != canonical_bytes(submitted.request())
        or row.preflight_sha256 != submitted.preflight_sha256
    ):
        raise DomainError(
            "idempotency_conflict",
            "This key already identifies a different request or preflight",
            409,
        )
    return validate_saved_binding(
        row,
        session.get(ResearchRun, row.run_id),
        session.get(FactorAnalysisPlan, row.plan_id),
        owner_id,
        key,
    )


def validate_saved_binding(row, run, plan, owner_id, key):
    """Data-only exact reader shared with cold closure; never grants execution."""
    saved = read_request(row)
    try:
        config = require_source_identity(run)
        if (
            saved.dataset_version_id,
            saved.window,
            saved.input_fields,
            saved.lookback_sessions,
            saved.lag_sessions,
            saved.task,
            saved.budget,
            saved.max_iterations,
        ) != (
            config.input.dataset.dataset_version_id,
            config.input.window,
            tuple(field.name for field in config.input.input_fields),
            config.input.lookback_sessions,
            config.input.lag_sessions,
            config.task,
            config.budget,
            config.max_iterations,
        ):
            raise ValueError("request/config binding")
        if plan is None or (
            plan.owner_id,
            plan.run_id,
            plan.config_sha256,
            plan.input_sha256,
            plan.objective_sha256,
            plan.source_policy_sha256,
        ) != (
            owner_id,
            row.run_id,
            config.sha256,
            config.input.sha256,
            saved.objective.sha256,
            digest(saved.source_policy),
        ):
            raise ValueError("plan binding")
        if (row.owner_id, run.owner_id, run.idempotency_key, row.config_sha256) != (
            owner_id,
            owner_id,
            key,
            config.sha256,
        ):
            raise ValueError("run binding")
        from packages.rdagent_adapter.provider_config import (
            ProviderFactorResearchConfig,
        )

        receipt_type = RDSubmissionReceipt
        extra = {}
        if isinstance(config, ProviderFactorResearchConfig):
            from backend.app.rdagent_provider_profile import (
                RDProviderServerRuntimeProfile,
            )
            from backend.app.rdagent_provider_submission_contracts import (
                CATALOG,
                RDProviderSubmissionReceipt,
                RDProviderSubmissionRequest,
                provider_commitment,
            )

            if type(saved) is not RDProviderSubmissionRequest or (
                saved.model_profile_id != config.model_binding.profile_id
                or saved.selected_steer_version_ids
                != tuple(v.version_id for v in config.steer_snapshot.versions)
                or config.steer_snapshot.owner_id != owner_id
            ):
                raise ValueError("Provider model/steer declaration drift")
            profile_sha = digest(
                RDProviderServerRuntimeProfile(
                    runtime=config.runtime,
                    qlib_installed_parent_image_id=plan.qlib_installed_parent_image_id,
                )
            )
            catalog = CATALOG
            expected_preflight = provider_commitment(
                owner_id,
                saved,
                dataset_sha256=config.input.dataset.content_sha256,
                input_sha256=config.input.sha256,
                profile_sha256=profile_sha,
                model_binding_sha256=config.model_binding.sha256,
                steer_snapshot_sha256=config.steer_snapshot_sha256,
            )
            receipt_type = RDProviderSubmissionReceipt
            extra = {"model_binding_sha256": config.model_binding.sha256}
        else:
            if type(saved) is not RDSubmissionRequest:
                raise ValueError(
                    "Historical fixture cannot bind configured provider request"
                )
            from backend.app.rdagent_server_profile import RDServerRuntimeProfile

            profile_sha = digest(
                RDServerRuntimeProfile(
                    runtime=config.runtime,
                    qlib_installed_parent_image_id=plan.qlib_installed_parent_image_id,
                )
            )
            catalog = "configured_rd_single_output_session_v1"
            expected_preflight = digest(
                {
                    "schema_version": "mineagent.rdagent-preflight-binding.v1",
                    "owner_id": str(owner_id),
                    "request_sha256": saved.sha256,
                    "dataset_content_sha256": config.input.dataset.content_sha256,
                    "frozen_input_sha256": config.input.sha256,
                    "catalog_revision": row.catalog_revision,
                    "runtime_profile_sha256": profile_sha,
                }
            )
        if (
            row.run_id != run.id
            or row.plan_id != plan.id
            or row.catalog_revision != catalog
            or row.runtime_profile_sha256 != profile_sha
        ):
            raise ValueError("profile/row binding")
        if row.preflight_sha256 != expected_preflight:
            raise ValueError("preflight binding")
        return receipt_type(
            **extra,
            run_id=run.id,
            plan_id=row.plan_id,
            request_sha256=row.request_sha256,
            preflight_sha256=row.preflight_sha256,
            replayed=True,
            status=run.status,
        )
    except (ValueError, TypeError, AttributeError):
        raise DomainError(
            "submission_receipt_drift", "Submission run binding is invalid", 409
        ) from None
