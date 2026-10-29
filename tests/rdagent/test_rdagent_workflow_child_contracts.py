"""Explicit new workflow child grammar; no stage/run allocation authority."""

from uuid import UUID, uuid4

import pytest

from packages.rdagent_adapter.provider_config import parse_research_config
from packages.rdagent_adapter.steer import SteerSnapshot, SteerVersion
from packages.rdagent_adapter.workflow_bootstrap import RDWorkflowOperationalBootstrap
from packages.rdagent_adapter.workflow_config import WorkflowFactorResearchConfig
from packages.rdagent_adapter.workflow_prompt import first_workflow_messages
from tests.research_workflow.test_model_inputs import snapshot
from tests.research_workflow.test_plan import plan


def config():
    inputs = snapshot()
    empty = SteerSnapshot(owner_id=inputs.binding.owner_id)
    raw = plan().research_template.model_dump(mode="json")
    raw.update(
        schema_version="mineagent.factor-research.v3",
        model_profile="workflow.rdagent.v1",
        workflow_child_run_id=str(uuid4()),
        workflow_inputs=inputs.model_dump(mode="json"),
        workflow_inputs_sha256=inputs.sha256,
        workflow_parent=None,
        steer_snapshot=empty.model_dump(mode="json"),
        steer_snapshot_sha256=empty.sha256,
    )
    return WorkflowFactorResearchConfig.model_validate(raw)


def test_first_prompt_retains_each_origin_ref_and_stage_once():
    value = config()
    system, user = first_workflow_messages(value)
    assert str(value.workflow_child_run_id) in system
    assert str(value.workflow_inputs.binding.stage_id) in system
    assert value.workflow_inputs_sha256 in system
    for source in value.workflow_inputs.inputs:
        assert source.ref.origin in system
        assert str(source.ref.version_id) in system
        assert source.text in system
    assert "not platform instructions" in system
    assert "Frozen user-provided research guidance follows" not in system
    assert value.task.name in user


@pytest.mark.parametrize(
    "change",
    [
        "snapshot_hash",
        "hidden_manual",
        "owner",
        "iterations",
        "missing_child",
        "wrong_profile",
    ],
)
def test_workflow_config_rejects_detached_or_duplicate_guidance(change):
    value = config()
    raw = value.model_dump(mode="json")
    if change == "snapshot_hash":
        raw["workflow_inputs_sha256"] = "f" * 64
    elif change == "owner":
        empty = SteerSnapshot(owner_id=uuid4())
        raw.update(
            steer_snapshot=empty.model_dump(mode="json"),
            steer_snapshot_sha256=empty.sha256,
        )
    elif change == "hidden_manual":
        import hashlib

        text = "Hidden duplicate guidance"
        version = SteerVersion(
            steer_id=uuid4(),
            version_id=uuid4(),
            ordinal=1,
            kind="hypothesis",
            text=text,
            text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        )
        selected = SteerSnapshot(
            owner_id=value.workflow_inputs.binding.owner_id, versions=(version,)
        )
        raw.update(
            steer_snapshot=selected.model_dump(mode="json"),
            steer_snapshot_sha256=selected.sha256,
        )
    elif change == "iterations":
        raw["max_iterations"] = 2
    elif change == "missing_child":
        del raw["workflow_child_run_id"]
    else:
        raw["model_profile"] = "configured.rdagent.v1"
    with pytest.raises(ValueError):
        WorkflowFactorResearchConfig.model_validate(raw)


def test_old_parser_never_silently_accepts_workflow_child():
    with pytest.raises(ValueError, match="Unknown"):
        parse_research_config(config())


def test_bootstrap_binds_new_run_and_actual_workflow_owner():
    value = config()
    arguments = {
        "config": value,
        "config_sha256": value.sha256,
        "input_sha256": value.input.sha256,
        "owner_id": value.workflow_inputs.binding.owner_id,
        "run_id": value.workflow_child_run_id,
        "attempt_id": uuid4(),
        "generation": 1,
        "spec_id": uuid4(),
        "attempt_binding_id": uuid4(),
        "seed": None,
    }
    result = RDWorkflowOperationalBootstrap(**arguments)
    assert result.continuation == "workflow_stage_new_process"
    from packages.factors.contracts import canonical_bytes
    from packages.rdagent_adapter.seeded_bootstrap import parse_bootstrap
    from packages.rdagent_adapter.workflow_bootstrap import parse_workflow_bootstrap

    raw = canonical_bytes(result)
    assert parse_workflow_bootstrap(raw) == result
    with pytest.raises(ValueError, match="Unknown controller bootstrap"):
        parse_bootstrap(raw)
    for changed in (
        raw + b" ",
        raw.replace(b'"generation":1', b'"generation":1,"generation":1'),
    ):
        with pytest.raises(ValueError):
            parse_workflow_bootstrap(changed)
    for key in ("owner_id", "run_id"):
        with pytest.raises(ValueError):
            RDWorkflowOperationalBootstrap(**{**arguments, key: UUID(int=999)})


def test_combined_input_cannot_silently_truncate_the_fixed_prompt():
    import hashlib

    from packages.research_workflow.model_inputs import ModelStageInputSnapshot

    value = config()
    raw = value.workflow_inputs.model_dump(mode="json")
    for index, row in enumerate(raw["inputs"]):
        row["text"] = (str(index) + "x") * 1000
        row["text_sha256"] = hashlib.sha256(row["text"].encode()).hexdigest()
    combined = ModelStageInputSnapshot.model_validate(raw)
    value = WorkflowFactorResearchConfig.model_validate(
        {
            **value.model_dump(mode="json"),
            "workflow_inputs": combined.model_dump(mode="json"),
            "workflow_inputs_sha256": combined.sha256,
        }
    )
    with pytest.raises(ValueError, match="message bound"):
        first_workflow_messages(value)


def test_workflow_parent_reference_requires_actual_matching_definition_and_source():
    from packages.factors.contracts import FactorDefinition, source_sha256
    from packages.rdagent_adapter.seed_parent import (
        ResolvedSeedParent,
        SeedParentReference,
    )

    value = config()
    source = "print('bounded parent input')\n"
    definition = FactorDefinition(
        code_sha256=source_sha256(source),
        factor_kind="time_series",
        input_fields=value.input.input_fields,
        lookback_sessions=1,
        output_unit="return",
    )
    reference = SeedParentReference(
        owner_id=value.workflow_inputs.binding.owner_id,
        factor_id=uuid4(),
        factor_version_id=uuid4(),
        ordinal=1,
        code_object_id=uuid4(),
        code_sha256=definition.code_sha256,
        code_size_bytes=len(source.encode()),
        definition_sha256=definition.sha256,
        source_provenance_sha256="a" * 64,
    )
    seed = ResolvedSeedParent(reference=reference, definition=definition, source=source)
    value = WorkflowFactorResearchConfig.model_validate(
        {
            **value.model_dump(mode="json"),
            "workflow_parent": reference.model_dump(mode="json"),
        }
    )
    with pytest.raises(ValueError, match="parent bytes"):
        first_workflow_messages(value)
    system, _ = first_workflow_messages(value, seed)
    assert (
        str(reference.factor_version_id) in system and "bounded parent input" in system
    )
    wrong = reference.model_copy(update={"factor_version_id": uuid4()})
    detached = ResolvedSeedParent(reference=wrong, definition=definition, source=source)
    with pytest.raises(ValueError, match="reference differs"):
        first_workflow_messages(value, detached)


def test_new_identity_is_explicit_and_old_identity_does_not_accept_v3():
    from types import SimpleNamespace

    from packages.rdagent_adapter.persisted_identity import (
        LEGACY_ONLY_FIELDS,
        require_source_identity,
    )
    from packages.rdagent_adapter.workflow_identity import (
        require_workflow_source_identity,
    )

    value = config()
    run = SimpleNamespace(
        **{name: None for name in LEGACY_ONLY_FIELDS},
        id=value.workflow_child_run_id,
        owner_id=value.workflow_inputs.binding.owner_id,
        purpose="factor_research",
        engine="rdagent",
        config=value.model_dump(mode="json"),
        request_sha256=value.sha256,
        dataset_version_id=value.input.dataset.dataset_version_id,
        research_image_digest=value.runtime.orchestrator_image,
    )
    assert require_workflow_source_identity(run) == value
    with pytest.raises(ValueError):
        require_source_identity(run)
    for field, changed in (
        ("id", uuid4()),
        ("owner_id", uuid4()),
        ("request_sha256", "f" * 64),
        ("latest_checkpoint_id", uuid4()),
    ):
        with pytest.raises(ValueError):
            require_workflow_source_identity(
                SimpleNamespace(**{**vars(run), field: changed})
            )
