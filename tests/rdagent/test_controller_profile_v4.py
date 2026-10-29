"""Seed-only controller selection; old canonical and fresh authority remain strict."""
# ruff: noqa: F811

import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_rdagent_seed_bootstrap import bootstrap  # noqa: F401
from test_rdagent_seed_parent import retained_parent  # noqa: F401

from backend.app.rdagent_provider_preflight import provider_budget_floor
from backend.app.rdagent_provider_profile import (
    RDProviderServerRuntimeProfile,
    provider_runtime_selection_fields,
    provider_seed_controller_profile,
    resolve_provider_runtime,
)
from backend.app.rdagent_server_profile import ROLE_LOCKS
from backend.app.rdagent_snapshot_config import parse_snapshot_config
from packages.factors.contracts import canonical_bytes, digest
from packages.rdagent_adapter.contracts import FactorResearchConfig
from packages.rdagent_adapter.controller_profile import (
    COMPOSED_CONTROLLER_PROFILE as V3,
)
from packages.rdagent_adapter.controller_profile import (
    SEEDED_CONTROLLER_PROFILE as V4,
)
from packages.rdagent_adapter.controller_profile import (
    composed_wall_floor,
    controller_wall_milliseconds,
)
from packages.rdagent_adapter.provider_bootstrap import build_operational_bootstrap
from packages.rdagent_adapter.provider_config import ProviderFactorResearchConfig
from packages.rdagent_adapter.steer import SteerSnapshot
from packages.rdagent_provider.contracts import RDModelBinding
from runtimes.rdagent.probe_fixture import research_dict


def binding():
    return RDModelBinding(
        profile_id="fixture",
        profile_sha256="a" * 64,
        chat_model="chat",
        embedding_model="embed",
        embedding_dimensions=3,
        max_output_tokens=128,
        request_deadline_ms=8000,
        synthetic=True,
        pricing={
            "status": "operator_declared",
            "chat_input_microusd_per_million": 0,
            "chat_output_microusd_per_million": 0,
            "embedding_microusd_per_million": 0,
        },
    )


def configured(owner, selected=V4):
    snapshot = SteerSnapshot(owner_id=owner, versions=())
    return ProviderFactorResearchConfig.model_validate(
        {
            **research_dict(),
            "max_iterations": 1,
            "schema_version": "mineagent.factor-research.v2",
            "model_profile": "configured.rdagent.v1",
            "controller_profile": selected,
            "model_binding": binding().model_dump(mode="json"),
            "steer_snapshot": snapshot.model_dump(mode="json"),
            "steer_snapshot_sha256": snapshot.sha256,
            "budget": provider_budget_floor(binding(), profile=selected).model_dump(
                mode="json"
            ),
        }
    )


def server(**selection):
    runtime = research_dict()["runtime"]
    runtime["dependency_locks"] = [
        {"role": k, "sha256": v} for k, v in sorted(ROLE_LOCKS.items())
    ]
    return RDProviderServerRuntimeProfile(
        runtime=runtime,
        qlib_installed_parent_image_id="sha256:" + "e" * 64,
        **selection,
    )


def test_effective_fresh_hash_unchanged_and_seed_hash_exact():
    old = server(controller_profile=V3)
    combined = server(controller_profile=V3, seeded_controller_profile=V4)
    settings = SimpleNamespace(
        rdagent_research_enabled=True,
        rdagent_provider_runtime_profile=combined.model_dump(mode="json"),
        rdagent_provider_python=sys.executable,
    )
    fresh = resolve_provider_runtime(settings)
    seeded = resolve_provider_runtime(settings, seeded=True)
    assert canonical_bytes(fresh) == canonical_bytes(old)
    assert digest(seeded) == digest(combined) != digest(old)
    assert provider_seed_controller_profile(seeded) == V4
    assert provider_runtime_selection_fields(configured(uuid4())) == {
        "controller_profile": V3,
        "seeded_controller_profile": V4,
    }
    assert provider_runtime_selection_fields(configured(uuid4(), V3)) == {
        "controller_profile": V3
    }


@pytest.mark.parametrize("value", [None, V3, "unknown", True, 180000])
def test_seed_selection_rejects_null_and_unknown(value):
    with pytest.raises(ValueError):
        server(controller_profile=V3, seeded_controller_profile=value)


def test_seed_selection_requires_explicit_fresh_v3():
    with pytest.raises(ValueError):
        server(seeded_controller_profile=V4)
    with pytest.raises(ValueError):
        server(controller_profile=V4)
    assert "seeded_controller_profile" not in server().model_dump()
    with pytest.raises(ValueError):
        RDProviderServerRuntimeProfile.model_validate(
            server().model_copy(update={"seeded_controller_profile": None})
        )


def test_only_wall_floor_changes_and_old_profiles_unchanged():
    old = provider_budget_floor(binding(), profile=V3).model_dump()
    new = provider_budget_floor(binding(), profile=V4).model_dump()
    assert new.pop("wall_milliseconds") - old.pop("wall_milliseconds") == 60000
    assert old == new
    assert controller_wall_milliseconds(V4) == 180000
    assert controller_wall_milliseconds(V3) == 120000
    assert composed_wall_floor(V4) == 360000
    assert provider_budget_floor(binding(), profile=V4).wall_milliseconds == 616000
    raw = configured(uuid4()).model_dump(mode="json")
    raw["budget"]["wall_milliseconds"] -= 1
    with pytest.raises(ValueError):
        ProviderFactorResearchConfig.model_validate(raw)


def test_v4_cannot_be_fixture_or_fresh_bootstrap(bootstrap):
    with pytest.raises(ValueError):
        FactorResearchConfig.model_validate(
            {**research_dict(), "controller_profile": V4}
        )
    config = configured(bootstrap.owner_id)
    scope = {
        key: getattr(bootstrap, key)
        for key in (
            "owner_id",
            "run_id",
            "attempt_id",
            "generation",
            "spec_id",
            "attempt_binding_id",
        )
    }
    scope.update(config_sha256=config.sha256, input_sha256=config.input.sha256)
    with pytest.raises(ValueError, match="retained parent"):
        build_operational_bootstrap(config=config, **scope)
    accepted = build_operational_bootstrap(
        config=config,
        seed=bootstrap.seed,
        seeded_submission_id=bootstrap.seeded_submission_id,
        **scope,
    )
    assert accepted.config.controller_profile == V4
    scope["owner_id"] = uuid4()
    with pytest.raises(ValueError):
        build_operational_bootstrap(
            config=config,
            seed=bootstrap.seed,
            seeded_submission_id=bootstrap.seeded_submission_id,
            **scope,
        )


def test_v4_snapshot_requires_explicit_new_capability():
    config = configured(uuid4())
    for flags in ({}, {"composed_controller": True}, {"seeded_controller": True}):
        with pytest.raises(ValueError):
            parse_snapshot_config(
                config.model_dump(mode="json"),
                owner_id=config.steer_snapshot.owner_id,
                provider_config=True,
                **flags,
            )
    assert (
        parse_snapshot_config(
            config.model_dump(mode="json"),
            owner_id=config.steer_snapshot.owner_id,
            provider_config=True,
            composed_controller=True,
            seeded_controller=True,
        )
        == config
    )


@pytest.mark.parametrize("selected", [V3, V4])
def test_legacy_admission_cannot_bind_new_profile(monkeypatch, selected):
    import backend.app.rdagent_controller_services as service
    import backend.app.rdagent_services as domain
    from backend.app.services import DomainError

    admission = SimpleNamespace(profile="mineagent.rdagent-orchestrator.v1")
    bootstrap = SimpleNamespace(config=SimpleNamespace(controller_profile=selected))
    monkeypatch.setattr(
        service, "expected_operational_bootstrap", lambda *a: (object(), bootstrap)
    )
    monkeypatch.setattr(domain, "_scope_row", lambda *a: admission)
    with pytest.raises(DomainError, match="immutable config"):
        service.bind_controller_launch(object(), object(), admission_id=uuid4())


@pytest.mark.parametrize(
    "revision",
    [
        "027_rd_controller_v3",
        "027a_contract_repairs",
        "027b_provider_material",
        "028_unknown",
    ],
)
def test_v4_live_revision_never_inferred_from_old_capability(revision):
    from packages.research_runtime.process_contracts import ProcessError
    from workers.research.rdagent_configured import require_controller_revision

    session = SimpleNamespace(
        scalars=lambda *a: SimpleNamespace(all=lambda: [revision])
    )
    with pytest.raises(ProcessError):
        require_controller_revision(session, configured(uuid4()))
    session.scalars = lambda *a: SimpleNamespace(
        all=lambda: ["027c_seeded_controller_v4"]
    )
    require_controller_revision(session, configured(uuid4()))


def test_fresh_graph_cannot_select_v4_before_any_write():
    from backend.app.rdagent_submission_services import _persist_queued_graph
    from backend.app.services import DomainError

    with pytest.raises(DomainError, match="exact provider seed request"):
        _persist_queued_graph(
            object(),
            uuid4(),
            object(),
            "key",
            object(),
            object(),
            object(),
            expected_input_sha256="a" * 64,
            model_binding=binding(),
            controller_selection=V4,
        )


def test_v4_intent_and_absolute_deadline_do_not_renew(tmp_path):
    from datetime import timedelta

    from test_rdagent_controller_process import records

    from runtimes.rdagent_operational.deadline import remaining_seconds
    from runtimes.rdagent_operational.process_journal import ControllerIntent
    from runtimes.rdagent_operational.supervisor import controller_command

    value = records(tmp_path)[0].intent.model_dump(mode="python")
    value["admission"]["profile"] = V4
    value["not_after"] = value["issued_at"] + timedelta(milliseconds=180000)
    intent = ControllerIntent.model_validate(value)
    assert controller_command(intent) == [
        str(int(intent.not_after.timestamp() * 1000)),
        V4,
    ]
    assert remaining_seconds("181000", 1000, profile=V4) == 180
    assert remaining_seconds("181000", 2000, profile=V4) == 179
    value["not_after"] += timedelta(microseconds=1)
    with pytest.raises(ValueError):
        ControllerIntent.model_validate(value)
    with pytest.raises(ValueError):
        remaining_seconds("181001", 1000, profile=V4)


@pytest.mark.parametrize("wall,accepted", [(179999, False), (180000, True)])
def test_v4_actual_reservation_covers_exact_parent_floor(monkeypatch, wall, accepted):
    import backend.app.research_broker_services as broker
    from packages.research_runtime.budget_contracts import BudgetVector

    fence = SimpleNamespace(
        owner_id=uuid4(), run_id=uuid4(), attempt_id=uuid4(), generation=1
    )
    minimum = BudgetVector(
        wall_milliseconds=controller_wall_milliseconds(V4), process_slots=1
    )
    row = SimpleNamespace(
        **vars(fence),
        purpose="rd_controller",
        state="reserved",
        amounts=minimum.model_copy(update={"wall_milliseconds": wall}).model_dump(),
    )
    session = SimpleNamespace(scalar=lambda *a: row)
    monkeypatch.setattr(broker, "_budget", lambda *a: None)
    if accepted:
        assert (
            broker.require_reservation(
                session, fence, uuid4(), purpose="rd_controller", minimum=minimum
            )
            is row
        )
    else:
        with pytest.raises(broker.BrokerAbort, match="invalid_accounting"):
            broker.require_reservation(
                session, fence, uuid4(), purpose="rd_controller", minimum=minimum
            )
