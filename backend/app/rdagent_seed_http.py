"""Explicit request version dispatch for the existing authenticated RD routes.

Historical requests may omit their original default version. The new seeded
family requires its explicit version; a parent field never silently selects it.
Only the route's CurrentUser and transaction writer establish submission origin.
"""

from backend.app.engine_run_submission_schemas import RDSubmissionRequest, RDSubmit
from backend.app.rdagent_seed_contracts import RDSeededSubmissionRequest, RDSeededSubmit


def parse_submission(document, *, submitted):
    if type(document) is not dict or type(submitted) is not bool:
        raise ValueError("Exact submission object/version required")
    version = document.get("schema_version", "mineagent.rdagent-submission.v1")
    if version == "mineagent.rdagent-submission.v1":
        cls = RDSubmit if submitted else RDSubmissionRequest
    elif version == "mineagent.rdagent-seeded-submission.v1":
        cls = RDSeededSubmit if submitted else RDSeededSubmissionRequest
    elif version == "mineagent.rdagent-provider-submission.v1":
        from backend.app.rdagent_provider_submission_contracts import (
            RDProviderSubmissionRequest,
            RDProviderSubmit,
        )

        cls = RDProviderSubmit if submitted else RDProviderSubmissionRequest
    elif version == "mineagent.rdagent-provider-seeded-submission.v1":
        from backend.app.rdagent_provider_submission_contracts import (
            RDProviderSeedRequest,
            RDProviderSeedSubmit,
        )

        cls = RDProviderSeedSubmit if submitted else RDProviderSeedRequest
    else:
        raise ValueError("Unknown RD submission version")
    return cls.model_validate(document)
