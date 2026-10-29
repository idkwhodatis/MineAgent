"""Retained provider-seeded declaration/parent bytes; no real process/PG claim."""

import json
from uuid import UUID

import pytest
from test_rdagent_controller_backup import retained as controller_retained  # noqa: F401
from test_rdagent_seed_backup import (  # noqa: F401
    cold,
    queued_writer,
    request_body,
    retained,
    retained_parent,
    seeded_writer,
    setup_source,
)

from backend.app import rdagent_seed_backup as backup
from backend.app.rdagent_provider_profile import RDProviderServerRuntimeProfile
from backend.app.rdagent_provider_submission_contracts import (
    RDProviderSeedRequest,
    provider_commitment,
)
from packages.factors.contracts import canonical_bytes, digest
from packages.rdagent_adapter.provider_config import ProviderFactorResearchConfig
from packages.rdagent_adapter.steer import SteerSnapshot


@pytest.fixture
def provider_seed(cold):  # noqa: F811
    row = cold.closure["tables"]["factor_seeded_submissions"][0]
    rd = cold.kwargs["rd_source_closure"]
    run = rd["external_bindings"]["runs"][0]
    spec = rd["tables"]["factor_research_specs"][0]
    plan = cold.kwargs["direct_analysis_closure"]["tables"]["factor_analysis_plans"][0]
    snapshot = SteerSnapshot(owner_id=UUID(run["owner_id"]))
    raw = {
        **run["config"],
        "schema_version": "mineagent.factor-research.v2",
        "model_profile": "configured.rdagent.v1",
        "controller_profile": "mineagent.rdagent-orchestrator.v4",
        "max_iterations": 1,
        "model_binding": {
            "profile_id": "offline",
            "profile_sha256": "a" * 64,
            "chat_model": "chat",
            "embedding_model": "embed",
            "embedding_dimensions": 2,
            "max_output_tokens": 100,
            "request_deadline_ms": 5000,
            "synthetic": True,
            "pricing": {
                "status": "operator_declared",
                "chat_input_microusd_per_million": 0,
                "chat_output_microusd_per_million": 0,
                "embedding_microusd_per_million": 0,
            },
        },
        "steer_snapshot": snapshot.model_dump(mode="json"),
        "steer_snapshot_sha256": snapshot.sha256,
    }
    from backend.app.rdagent_provider_preflight import provider_budget_floor
    from packages.rdagent_provider.contracts import RDModelBinding

    raw["budget"] = provider_budget_floor(
        RDModelBinding.model_validate(raw["model_binding"]),
        profile="mineagent.rdagent-orchestrator.v4",
    ).model_dump(mode="json")
    config = ProviderFactorResearchConfig.model_validate(raw)
    request = RDProviderSeedRequest.model_validate(
        {
            **json.loads(row["request_canonical"]),
            "schema_version": "mineagent.rdagent-provider-seeded-submission.v1",
            "model_profile_id": "offline",
            "selected_steer_version_ids": [],
            "budget": config.budget.model_dump(mode="json"),
            "max_iterations": 1,
        }
    )
    profile = RDProviderServerRuntimeProfile(
        runtime=config.runtime,
        controller_profile="mineagent.rdagent-orchestrator.v3",
        seeded_controller_profile="mineagent.rdagent-orchestrator.v4",
        qlib_installed_parent_image_id=plan["qlib_installed_parent_image_id"],
    )
    row.update(
        request_canonical=canonical_bytes(request).decode(),
        request_sha256=request.sha256,
        config_sha256=config.sha256,
        runtime_profile_sha256=digest(profile),
    )
    row["preflight_sha256"] = provider_commitment(
        UUID(run["owner_id"]),
        request,
        dataset_sha256=config.input.dataset.content_sha256,
        input_sha256=config.input.sha256,
        profile_sha256=digest(profile),
        model_binding_sha256=config.model_binding.sha256,
        steer_snapshot_sha256=config.steer_snapshot_sha256,
        parent_binding_sha256=row["parent_binding_sha256"],
    )
    run.update(config=config.model_dump(mode="json"), request_sha256=config.sha256)
    spec.update(config=config.model_dump(mode="json"), config_sha256=config.sha256)
    plan["config_sha256"] = config.sha256
    cold.kwargs["research_closure"]["tables"]["research_budget_controls"][0][
        "limits"
    ] = config.budget.model_dump(mode="json")
    cold.kwargs["research_closure"]["schema_version"] = (
        "mineagent.research-restore-closure.v12"
    )
    rd["schema_version"] = "mineagent.rd-source-restore-closure.v7"
    return cold, config


def test_nonempty_provider_seeded180_closure_rebuilds_exact_parent(provider_seed):
    case, config = provider_seed
    context = backup.validate_closure(case.closure, **case.kwargs)
    row = case.closure["tables"]["factor_seeded_submissions"][0]
    assert (
        context[row["run_id"]].parent.source.encode() == case.parent.path.read_bytes()
    )
    assert config.controller_profile == "mineagent.rdagent-orchestrator.v4"


@pytest.mark.parametrize(
    "fault",
    [
        "owner",
        "parent_hash",
        "request_hash",
        "runtime_hash",
        "budget",
        "budget_null",
        "budget_unknown",
        "profile_null",
        "profile_unknown",
        "missing_seed",
        "old_schema",
        "wrong_pair",
    ],
)
def test_provider_seeded180_closed_identity_negatives(provider_seed, fault):
    case, _ = provider_seed
    row = case.closure["tables"]["factor_seeded_submissions"][0]
    if fault == "owner":
        row["owner_id"] = "00000000-0000-0000-0000-000000000001"
    elif fault == "parent_hash":
        row["parent_code_sha256"] = "f" * 64
    elif fault == "request_hash":
        row["request_sha256"] = "f" * 64
    elif fault == "runtime_hash":
        row["runtime_profile_sha256"] = "f" * 64
    elif fault == "budget":
        case.kwargs["research_closure"]["tables"]["research_budget_controls"][0][
            "limits"
        ]["wall_milliseconds"] -= 60000
    elif fault == "budget_null":
        case.kwargs["research_closure"]["tables"]["research_budget_controls"][0][
            "limits"
        ]["wall_milliseconds"] = None
    elif fault == "budget_unknown":
        case.kwargs["research_closure"]["tables"]["research_budget_controls"][0][
            "limits"
        ]["unrecognized"] = 1
    elif fault in {"profile_null", "profile_unknown"}:
        case.kwargs["rd_source_closure"]["external_bindings"]["runs"][0]["config"][
            "controller_profile"
        ] = None if fault == "profile_null" else "unknown"
    elif fault == "missing_seed":
        case.closure["tables"] = {k: [] for k in backup.FIELDS}
    elif fault == "old_schema":
        case.kwargs["research_closure"]["schema_version"] = (
            "mineagent.research-restore-closure.v11"
        )
        case.kwargs["rd_source_closure"]["schema_version"] = (
            "mineagent.rd-source-restore-closure.v6"
        )
    else:
        case.kwargs["rd_source_closure"]["schema_version"] = (
            "mineagent.rd-source-restore-closure.v6"
        )
    with pytest.raises((ValueError, KeyError)):
        backup.validate_closure(case.closure, **case.kwargs)


@pytest.mark.parametrize("controller_retained", ["v4"], indirect=True)
def test_seeded180_complete_retained_control_receipts(controller_retained):  # noqa: F811
    value = controller_retained
    value["validate"]()
    reservation = next(iter(value["indexes"]["research_budget_reservations"].values()))
    assert reservation["amounts"]["wall_milliseconds"] == 180000
    reservation["amounts"]["wall_milliseconds"] = 120000
    with pytest.raises(ValueError, match="reservation floor"):
        value["validate"]()


@pytest.mark.parametrize("controller_retained", ["v4"], indirect=True)
@pytest.mark.parametrize(
    "fault", ["wrong_schema", "missing_seed", "receipt_hash", "stop_state", "charge"]
)
def test_seeded180_control_receipts_reject_substitution(controller_retained, fault):  # noqa: F811
    value = controller_retained
    if fault == "wrong_schema":
        with pytest.raises(ValueError):
            value["validate"](research_schema="mineagent.research-restore-closure.v11")
        return
    if fault == "missing_seed":
        with pytest.raises(ValueError, match="lacks retained parent"):
            value["validate"](seed_context={})
        return
    indexes = value["indexes"]
    if fault == "receipt_hash":
        next(iter(indexes["research_process_receipts"].values()))["payload_sha256"] = (
            "f" * 64
        )
    elif fault == "stop_state":
        next(iter(indexes["research_process_states"].values()))["status"] = "running"
    else:
        next(iter(indexes["research_budget_settlements"].values()))["charge"][
            "wall_milliseconds"
        ] = 120000
    with pytest.raises(ValueError):
        value["validate"]()
