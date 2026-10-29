"""Actual workflow receipt→controller bootstrap, separated from physical reads."""

from sqlalchemy import select

from backend.app.factor_models import Factor, FactorObject, FactorVersion
from backend.app.rdagent_workflow_models import FactorWorkflowSubmission
from backend.app.rdagent_workflow_saved import (
    verify_workflow_submission,
    verify_workflow_submission_metadata,
)
from backend.app.research_lifecycle import require_neutral_transaction
from backend.app.services import DomainError
from packages.factors.contracts import FactorDefinition, canonical_bytes, digest
from packages.rdagent_adapter.seed_parent import ResolvedSeedParent
from packages.rdagent_adapter.workflow_bootstrap import RDWorkflowOperationalBootstrap
from packages.rdagent_adapter.workflow_config import WorkflowFactorResearchConfig


def fail():
    raise DomainError(
        "workflow_bootstrap_changed", "Workflow controller binding differs", 409
    )


def workflow_parent_metadata(session, owner_id, reference):
    """DB reconstruction only. Fresh byte reader remains mandatory before launch."""
    if reference is None:
        return None
    if reference.owner_id != owner_id:
        fail()
    version = session.scalar(
        select(FactorVersion).filter_by(
            owner_id=owner_id, id=reference.factor_version_id
        )
    )
    factor = session.scalar(
        select(Factor).filter_by(owner_id=owner_id, id=reference.factor_id)
    )
    obj = session.scalar(
        select(FactorObject).filter_by(owner_id=owner_id, id=reference.code_object_id)
    )
    if version is None or factor is None or obj is None:
        fail()
    if (
        (version.owner_id, factor.owner_id, obj.owner_id) != (owner_id,) * 3
        or (
            version.id,
            version.factor_id,
            version.ordinal,
            version.code_object_id,
            version.code_sha256,
            version.definition_sha256,
            digest(version.source_provenance),
        )
        != (
            reference.factor_version_id,
            reference.factor_id,
            reference.ordinal,
            reference.code_object_id,
            reference.code_sha256,
            reference.definition_sha256,
            reference.source_provenance_sha256,
        )
        or factor.id != reference.factor_id
        or (
            obj.id,
            obj.sha256,
            obj.size_bytes,
            obj.object_kind,
            obj.media_type,
            obj.storage_key,
        )
        != (
            reference.code_object_id,
            reference.code_sha256,
            reference.code_size_bytes,
            "code",
            "text/plain",
            f"factors/{owner_id}/objects/{reference.code_sha256}.py",
        )
    ):
        fail()
    try:
        return ResolvedSeedParent(
            reference=reference,
            definition=FactorDefinition.model_validate_json(
                canonical_bytes(version.definition)
            ),
            source=version.code,
        )
    except (ValueError, TypeError):
        fail()


def workflow_bootstrap_if_declared(session, fence, *, binding, spec, config):
    if type(config) is not WorkflowFactorResearchConfig:
        return None
    from backend.app.rdagent_workflow_capability import require_workflow_revision

    require_workflow_revision(session)
    require_neutral_transaction(session, fence)
    if (
        binding.owner_id,
        binding.run_id,
        binding.attempt_id,
        binding.generation,
        binding.spec_id,
    ) != (
        fence.owner_id,
        fence.run_id,
        fence.attempt_id,
        fence.generation,
        spec.id,
    ) or (spec.owner_id, spec.run_id) != (fence.owner_id, fence.run_id):
        fail()
    row = session.scalar(
        select(FactorWorkflowSubmission).filter_by(
            owner_id=fence.owner_id, run_id=fence.run_id
        )
    )
    if row is None:
        fail()
    verified = verify_workflow_submission_metadata(session, fence.owner_id, row.id)
    if (verified.run_id, verified.spec_id, verified.config_sha256) != (
        fence.run_id,
        spec.id,
        config.sha256,
    ):
        fail()
    seed = workflow_parent_metadata(session, fence.owner_id, config.workflow_parent)
    return RDWorkflowOperationalBootstrap(
        owner_id=fence.owner_id,
        run_id=fence.run_id,
        attempt_id=fence.attempt_id,
        generation=fence.generation,
        spec_id=spec.id,
        attempt_binding_id=binding.id,
        config=config,
        config_sha256=spec.config_sha256,
        input_sha256=spec.input_sha256,
        seed=seed,
    )


def reread_workflow_bootstrap_inputs(session, fence, bootstrap, settings):
    if type(bootstrap) is not RDWorkflowOperationalBootstrap:
        raise ValueError("Explicit workflow bootstrap required")
    from backend.app.rdagent_seed_services import resolve_seed_parent
    from backend.app.rdagent_workflow_capability import require_workflow_revision

    require_workflow_revision(session)
    require_neutral_transaction(session, fence, resource_scope="factor_numeric")
    if (
        bootstrap.owner_id,
        bootstrap.run_id,
        bootstrap.attempt_id,
        bootstrap.generation,
    ) != (fence.owner_id, fence.run_id, fence.attempt_id, fence.generation):
        fail()
    row = session.scalar(
        select(FactorWorkflowSubmission).filter_by(
            owner_id=fence.owner_id, run_id=fence.run_id
        )
    )
    if row is None:
        fail()
    verified = verify_workflow_submission(session, fence.owner_id, row.id, settings)
    if (verified.config_sha256, verified.spec_id) != (
        bootstrap.config_sha256,
        bootstrap.spec_id,
    ):
        fail()
    parent = bootstrap.config.workflow_parent
    actual = (
        None
        if parent is None
        else resolve_seed_parent(
            session, fence.owner_id, parent.factor_version_id, settings
        )
    )
    if actual != bootstrap.seed:
        fail()


def require_workflow_initial_generation(
    session, fence, *, submission_id, parent_factor_version_id
):
    """Actual immutable workflow receipt is distinct from a seeded-user receipt."""
    from backend.app.rdagent_workflow_capability import require_workflow_revision

    require_workflow_revision(session)
    require_neutral_transaction(session, fence)
    row = verify_workflow_submission_metadata(session, fence.owner_id, submission_id)
    if row.run_id != fence.run_id:
        fail()
    from backend.app.models import ResearchRun
    from packages.rdagent_adapter.workflow_identity import (
        require_workflow_source_identity,
    )

    config = require_workflow_source_identity(session.get(ResearchRun, fence.run_id))
    expected_parent = (
        None
        if config.workflow_parent is None
        else config.workflow_parent.factor_version_id
    )
    if parent_factor_version_id != expected_parent:
        fail()
    return row
