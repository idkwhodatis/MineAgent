"""Bound complete model-stage envelopes before either child writer allocates work."""

from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes
from packages.research_workflow.model_inputs import (
    MAX_MODEL_INPUT_BYTES,
    ModelStageInputSnapshot,
)


def bounded_model_inputs(*, binding, selection, inputs):
    # The fresh resolver bounds the resolved list, not the complete snapshot,
    # whose immutable selection/binding add bytes. Only that size failure is a
    # recoverable capacity wait. Other contract failures retain their semantics.
    envelope = {
        "schema_version": ModelStageInputSnapshot.model_fields[
            "schema_version"
        ].default,
        "qualification": ModelStageInputSnapshot.model_fields["qualification"].default,
        "binding": binding.model_dump(mode="json"),
        "selection": selection.model_dump(mode="json"),
        "inputs": [value.model_dump(mode="json") for value in inputs],
    }
    if len(canonical_bytes(envelope)) > MAX_MODEL_INPUT_BYTES:
        raise DomainError(
            "workflow_input_capacity_exceeded",
            "Workflow model inputs exceed their bound",
            409,
        )
    return ModelStageInputSnapshot(binding=binding, selection=selection, inputs=inputs)
