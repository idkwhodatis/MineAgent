"""Exact successor dependency dispatch; no physical restore acceptance claim."""
# ruff: noqa: F811

import pytest
from test_rdagent_controller_backup import retained  # noqa: F401

from backend.app import research_backup as backup


def test_v13_keeps_successor_shape_but_requires_its_own_final_pass():
    value = {"schema_version": backup.SCHEMA_V14}
    options = {
        "inventory": [],
        "oe_structured_closure": {
            "schema_version": "mineagent.oe-structured-restore-closure.v1"
        },
        "openevolve_closure": {},
        "seed_context": {},
        "structured_links_pending": True,
        "successor_links_pending": True,
    }
    with pytest.raises(ValueError, match="workflow final physical links"):
        backup.validate_closure(value, None, None, 100, **options)
    with pytest.raises(ValueError, match="workflow context"):
        backup.validate_closure(
            value, None, None, 100, workflow_links_pending=True, **options
        )


def test_old_v12_cannot_acquire_workflow_finalization():
    options = {
        "inventory": [],
        "oe_structured_closure": {
            "schema_version": "mineagent.oe-structured-restore-closure.v1"
        },
        "openevolve_closure": {},
        "seed_context": {},
        "structured_links_pending": True,
        "successor_links_pending": True,
        "workflow_links_pending": True,
        "workflow_context": {},
    }
    with pytest.raises(ValueError, match="workflow final physical links"):
        backup.validate_closure(
            {"schema_version": backup.SCHEMA_V13}, None, None, 100, **options
        )


def test_v13_controller_requires_workflow_context_and_preserves_old_actual_bytes(
    retained,
):
    retained["rd"]["schema_version"] = "mineagent.rd-source-restore-closure.v9"
    options = {"research_schema": backup.SCHEMA_V14, "seed_context": {}}
    with pytest.raises(ValueError, match="workflow context"):
        retained["validate"](**options)
    assert retained["validate"](**options, workflow_context={}) is True


@pytest.mark.parametrize("mutation", [None, "missing_context", "bootstrap", "physical"])
def test_actual_v3_controller_bytes_are_not_a_legacy_seed_alias(retained, mutation):
    from uuid import UUID

    from test_rdagent_workflow_child_contracts import config as workflow_config

    from backend.app.research_workflow_backup import WorkflowColdContext
    from packages.factors.contracts import canonical_bytes
    from packages.rdagent_adapter.workflow_bootstrap import (
        RDWorkflowOperationalBootstrap,
    )
    from packages.rdagent_adapter.workflow_config import WorkflowFactorResearchConfig
    from packages.research_runtime.process_contracts import digest
    from runtimes.rdagent_operational.process_journal import (
        ControllerChild,
        ControllerIdentity,
        ControllerLaunch,
        ControllerStop,
    )

    f = retained
    row = f["row"]
    rd = f["rd"]
    tables = rd["tables"]
    # Reparse the complete declaration after rebinding the actual retained scope.
    base = workflow_config()
    selection = base.workflow_inputs.selection.model_copy(
        update={"owner_id": UUID(row["owner_id"])}
    )
    binding = base.workflow_inputs.binding.model_copy(
        update={"owner_id": UUID(row["owner_id"]), "input_sha256": selection.sha256}
    )
    inputs = base.workflow_inputs.model_copy(
        update={"binding": binding, "selection": selection}
    )
    steer = base.steer_snapshot.model_copy(update={"owner_id": UUID(row["owner_id"])})
    raw = base.model_dump(mode="python")
    raw.update(
        workflow_child_run_id=UUID(row["run_id"]),
        workflow_inputs=inputs,
        workflow_inputs_sha256=inputs.sha256,
        steer_snapshot=steer,
        steer_snapshot_sha256=steer.sha256,
        runtime=base.runtime.model_copy(update={"orchestrator_image": row["image_id"]}),
    )
    config = WorkflowFactorResearchConfig.model_validate(raw)
    run = rd["external_bindings"]["runs"][0]
    run.update(
        config=config.model_dump(mode="json"),
        request_sha256=config.sha256,
        dataset_version_id=str(config.input.dataset.dataset_version_id),
    )
    spec = tables["factor_research_specs"][0]
    spec.update(
        config=run["config"],
        config_sha256=config.sha256,
        input_sha256=config.input.sha256,
    )
    bootstrap = RDWorkflowOperationalBootstrap(
        owner_id=UUID(row["owner_id"]),
        run_id=UUID(row["run_id"]),
        attempt_id=UUID(row["attempt_id"]),
        generation=row["generation"],
        spec_id=UUID(spec["id"]),
        attempt_binding_id=UUID(row["logical_operation_id"]),
        config=config,
        config_sha256=config.sha256,
        input_sha256=config.input.sha256,
        seed=None,
    )
    tables["factor_controller_launches"][0].update(
        bootstrap=bootstrap.model_dump(mode="json"), bootstrap_sha256=bootstrap.sha256
    )
    launch = ControllerLaunch.model_validate_json(
        (f["root"] / (f["prefix"] + "launch.json")).read_bytes()
    )
    admission = launch.intent.admission.model_copy(
        update={"input_sha256": bootstrap.sha256}
    )
    row["input_sha256"] = bootstrap.sha256
    intent = launch.intent.model_copy(
        update={
            "admission": admission,
            "bootstrap_sha256": bootstrap.sha256,
            "bootstrap_size_bytes": len(canonical_bytes(bootstrap)),
        }
    )
    launch = launch.model_copy(update={"intent": intent})
    receipts = {
        r["kind"]: r for r in f["indexes"]["research_process_receipts"].values()
    }
    identity = ControllerIdentity.model_validate_json(
        canonical_bytes(receipts["launch_identity"]["payload"])
    ).model_copy(
        update={
            "launch_sha256": digest(launch.model_dump(mode="json")),
            "admission_sha256": digest(admission.model_dump(mode="json")),
        }
    )
    child = ControllerChild.model_validate_json(
        canonical_bytes(receipts["controller_child"]["payload"])
    ).model_copy(
        update={
            "identity_sha256": digest(identity.model_dump(mode="json")),
            "admission_sha256": identity.admission_sha256,
        }
    )
    stop = ControllerStop.model_validate_json(
        canonical_bytes(receipts["stop"]["payload"])
    ).model_copy(
        update={
            "launch_sha256": identity.launch_sha256,
            "identity_sha256": child.identity_sha256,
            "child_sha256": digest(child.model_dump(mode="json")),
        }
    )
    for kind, name, value in [
        ("launch_intent", "launch", intent),
        ("launch_identity", "identity", identity),
        ("controller_child", "child", child),
        ("stop", "stop", stop),
    ]:
        receipts[kind].update(
            payload=value.model_dump(mode="json"),
            payload_sha256=digest(value.model_dump(mode="json")),
        )
        f["write"](name, launch if name == "launch" else value)
    rd["schema_version"] = "mineagent.rd-source-restore-closure.v9"
    context = {row["run_id"]: WorkflowColdContext(config=config, parent=None)}
    if mutation == "missing_context":
        context = {}
    elif mutation == "bootstrap":
        tables["factor_controller_launches"][0]["bootstrap"]["schema_version"] = (
            "mineagent.rd-operational-bootstrap.v1"
        )
    elif mutation == "physical":
        (f["root"] / (f["prefix"] + "child.json")).write_bytes(b"{}")
    options = {
        "research_schema": backup.SCHEMA_V14,
        "seed_context": {},
        "workflow_context": context,
    }
    if mutation is None:
        assert f["validate"](**options) is True
    else:
        with pytest.raises(ValueError):
            f["validate"](**options)
