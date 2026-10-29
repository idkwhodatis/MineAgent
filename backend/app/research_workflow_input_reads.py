"""Fixed input readers: feedback uses a retained native fence, never new release."""

from contextlib import contextmanager

from backend.app.factor_native_io import (
    _NATIVE_GUARD,
    native_storage_guard,
    require_native_storage_guard,
)
from backend.app.research_input_resolution import (
    resolve_model_input_versions,
    verify_model_input_versions,
)


@contextmanager
def retained_input_guard(settings):
    # Reuse only an actually established native fence. Publication callers must
    # enter native_guard_from_publication first; no implicit admission is minted.
    if _NATIVE_GUARD.get() is not None:
        require_native_storage_guard(settings.storage_root)
        yield
        require_native_storage_guard(settings.storage_root)
    else:
        with native_storage_guard(settings.storage_root):
            yield


def resolve_workflow_inputs(session, owner_id, refs, settings):
    if not any(ref.origin == "experiment_feedback_proposal" for ref in refs):
        return resolve_model_input_versions(session, owner_id, refs, settings)
    with retained_input_guard(settings):
        return resolve_model_input_versions(session, owner_id, refs, settings)


def verify_workflow_inputs(session, owner_id, refs, settings):
    if not any(ref.origin == "experiment_feedback_proposal" for ref in refs):
        return verify_model_input_versions(session, owner_id, refs, settings)
    with retained_input_guard(settings):
        return verify_model_input_versions(session, owner_id, refs, settings)
