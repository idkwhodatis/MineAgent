"""Host-only frozen run/bootstrap binding; never copied into the controller."""

from .provider_bootstrap import ProviderStructuredBootstrap, runtime_profile_sha256
from .workflow_bootstrap import (
    WorkflowStructuredBootstrap,
    workflow_runtime_profile_sha256,
)


def runtime_profile_for(config):
    from packages.factors.contracts import canonical_bytes

    from .workflow_run import WorkflowStructuredRunConfig, parse_provider_run

    checked = parse_provider_run(canonical_bytes(config))
    return (
        workflow_runtime_profile_sha256()
        if type(checked) is WorkflowStructuredRunConfig
        else runtime_profile_sha256()
    )


def require_bootstrap_context(config, bootstrap):
    from .workflow_run import WorkflowStructuredRunConfig

    if type(config) is WorkflowStructuredRunConfig:
        if (
            type(bootstrap) is not WorkflowStructuredBootstrap
            or bootstrap.workflow_context != config.workflow_context
        ):
            raise ValueError("Exact workflow bootstrap/run context required")
    elif type(bootstrap) is not ProviderStructuredBootstrap:
        raise ValueError("Legacy run cannot acquire workflow bootstrap semantics")
