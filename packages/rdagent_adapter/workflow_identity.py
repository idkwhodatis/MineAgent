"""028-only persisted workflow child grammar; not admission or live authority."""

from packages.factors.contracts import canonical_bytes
from packages.rdagent_adapter.persisted_identity import LEGACY_ONLY_FIELDS
from packages.rdagent_adapter.workflow_config import WorkflowFactorResearchConfig


def require_workflow_source_identity(run):
    """Call only from explicit028/research-v12/RD-v7 version branches."""
    if (run.purpose, run.engine) != ("factor_research", "rdagent"):
        raise ValueError("Exact workflow RD persisted identity required")
    if type(run.config) is not dict or any(
        getattr(run, field) is not None for field in LEGACY_ONLY_FIELDS
    ):
        raise ValueError("Legacy fields cannot stand in for workflow RD identity")
    config = WorkflowFactorResearchConfig.model_validate_json(
        canonical_bytes(run.config)
    )
    if (
        config.workflow_child_run_id != run.id
        or config.workflow_inputs.binding.owner_id != run.owner_id
        or config.model_dump(mode="json") != run.config
        or run.dataset_version_id != config.input.dataset.dataset_version_id
        or run.request_sha256 != config.sha256
        or run.research_image_digest not in (None, config.runtime.orchestrator_image)
    ):
        raise ValueError("Workflow child immutable identity differs")
    return config
