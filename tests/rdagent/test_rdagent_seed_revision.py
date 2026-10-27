"""Literal delta structure, not PostgreSQL execution acceptance."""

# ruff: noqa: F811

import hashlib

import pytest
from test_rdagent_seed_parent import retained_parent  # noqa: F401

from backend.app import rdagent_seed_revision as revision


def test_seeded_controller_delta_keeps_original_checks_and_explicit_prior_restore():
    previous = revision.previous_shared_sql()
    upgrade = revision.bootstrap_delta_sql()
    restore = revision.bootstrap_restore_sql()
    assert restore.endswith(previous)
    assert "seeded" not in previous
    assert "openevolve" in previous
    assert "'mineagent.rdagent-seeded-operational-bootstrap.v1'" in upgrade
    assert "s.owner_id=cl.owner_id" in upgrade
    assert "pa.profile IS DISTINCT FROM 'mineagent.rdagent-orchestrator.v2'" in upgrade
    for protected in (
        "ab.resumed_checkpoint_id IS NOT NULL",
        "cl.bootstrap IS DISTINCT FROM expected",
        "cl.bootstrap_sha256<>encode(sha256",
        "pa.schema_version,pa.ipc_policy,pa.role,pa.logical_operation_id,pa.input_sha256,pa.image_id",
        "RD mutation slot requires exact start consumer",
    ):
        assert protected in previous and protected in upgrade
    assert "mineagent_rd_seed_reference(s.id)" in upgrade
    assert "'definition',v.definition,'source',v.code" in upgrade


def test_literal_drift_cannot_regenerate_old_history(monkeypatch):
    from backend.app import rdagent_provider_revision

    monkeypatch.setitem(
        rdagent_provider_revision.PINS,
        ("024_openevolve_initial.py", "UPGRADE_SQL"),
        "0" * 64,
    )
    with pytest.raises(ValueError, match="Historical predicate bytes changed"):
        revision.bootstrap_delta_sql()


def test_exact_pinned_previous_literal():
    sql = revision._literal("020_rd_controller.py", "UPGRADE_SQL")
    assert (
        hashlib.sha256(sql.encode()).hexdigest() == revision.FROZEN_020_UPGRADE_SHA256
    )


def test_controller_delta_retains_every_physical_check_and_restores_exact024():
    previous = revision.previous_controller_sql()
    inserted = revision._bootstrap_version_sql(
        expected="expected_bootstrap", spec="sp", admission="a"
    )
    upgraded = revision.controller_bootstrap_delta_sql()
    assert upgraded.count(inserted) == 1
    assert upgraded.replace(inserted, "") == previous
    assert previous in revision.bootstrap_restore_sql()
    assert upgraded in revision.render_upgrade_sql()
    assert "profile='mineagent.rdagent-orchestrator.v2' LOOP" in upgraded
    for check in (
        "ab.resumed_checkpoint_id IS NOT NULL",
        "Controller bootstrap/image commitment differs",
        "Controller physical reservation floor/scope differs",
        "Controller immutable intent differs",
        "interval '60 seconds'",
        "Protected leader physical identity differs",
        "Exact trusted child/peer/NSpid identity differs",
        "Controller positive stop metadata differs",
        "Controller settlement requires stopped full conservative charge",
    ):
        assert check in previous and check in upgraded
    # The protected profile is shared by all four actual bootstrap grammars;
    # filtering away provider admissions would silently drop physical checks.
    assert "AND sp.config" not in upgraded


def test_frozen026_contains_both_independent_bootstrap_replacements():
    from backend.app.rdagent_provider_revision import function

    frozen = revision._literal("026_rd_seeded_submissions.py", "UPGRADE_SQL")
    from backend.app.rdagent_steer_sql import UPGRADE as steer_sql

    # NEW is a different row type on the two real trigger tables. A SQL CASE
    # still resolves both field references; only a PL/pgSQL IF is safe here.
    steer = function(frozen, "mineagent_steer_graph_check")
    assert steer == function(steer_sql, "mineagent_steer_graph_check")
    assert (
        "IF TG_TABLE_NAME='rd_steers' THEN\n  sid:=NEW.id;\n ELSE\n  sid:=NEW.steer_id;\n END IF;"
        in steer
    )
    assert "sid:=CASE" not in steer
    assert "head.current_version_id IS DISTINCT FROM vid" in steer
    assert "n<>hi" in steer and "head.revision<hi" in steer
    actual = function(frozen, "mineagent_rd_controller_consistent")
    assert actual == revision.controller_bootstrap_delta_sql()
    shared = function(frozen, "mineagent_rd_shared_consistent")
    assert (
        revision._bootstrap_version_sql(
            expected="expected", spec="spec", admission="pa"
        )
        in shared
    )
    for value in (actual, shared):
        assert "mineagent.rdagent-provider-operational-bootstrap.v1" in value
        assert "mineagent.rdagent-provider-seeded-bootstrap.v1" in value
        assert "mineagent.rdagent-seeded-operational-bootstrap.v1" in value
        assert "s.owner_id=cl.owner_id" in value


@pytest.mark.parametrize(
    "provider,seeded", [(False, False), (False, True), (True, False), (True, True)]
)
def test_controller_expected_bytes_follow_actual_four_producer_classes(
    retained_parent, provider, seeded
):
    from uuid import UUID, uuid4

    from packages.factors.contracts import canonical_bytes
    from packages.rdagent_adapter.contracts import FactorResearchConfig
    from packages.rdagent_adapter.provider_bootstrap import build_operational_bootstrap
    from packages.rdagent_adapter.provider_config import ProviderFactorResearchConfig
    from packages.rdagent_adapter.seeded_bootstrap import parse_bootstrap
    from packages.rdagent_adapter.steer import SteerSnapshot
    from runtimes.rdagent.probe_fixture import research_dict
    from tests.rdagent_provider.test_steer_prompt import configuration

    base = FactorResearchConfig.model_validate({**research_dict(), "max_iterations": 1})
    if provider:
        snapshot = SteerSnapshot(owner_id=retained_parent.owner)
        template = configuration().model_dump(mode="json")
        template.update(
            steer_snapshot=snapshot.model_dump(mode="json"),
            steer_snapshot_sha256=snapshot.sha256,
        )
        base = ProviderFactorResearchConfig.model_validate_json(
            canonical_bytes(template)
        )
    from test_rdagent_seed_parent import resolve

    scope = {
        "owner_id": retained_parent.owner,
        "run_id": uuid4(),
        "attempt_id": uuid4(),
        "generation": 1,
        "spec_id": uuid4(),
        "attempt_binding_id": uuid4(),
        "config_sha256": base.sha256,
        "input_sha256": base.input.sha256,
    }
    seed = resolve(retained_parent) if seeded else None
    receipt_id = uuid4() if seeded else None
    actual = build_operational_bootstrap(
        config=base, seed=seed, seeded_submission_id=receipt_id, **scope
    )
    schema = {
        (False, False): "mineagent.rdagent-operational-bootstrap.v1",
        (False, True): "mineagent.rdagent-seeded-operational-bootstrap.v1",
        (True, False): "mineagent.rdagent-provider-operational-bootstrap.v1",
        (True, True): "mineagent.rdagent-provider-seeded-bootstrap.v1",
    }[provider, seeded]
    # Same base JSON fields and explicit delta as the installed SQL body; the
    # original runtime parser independently rejects changed identity/grammar.
    expected = {
        **{
            key: str(value) if isinstance(value, UUID) else value
            for key, value in scope.items()
        },
        "schema_version": schema,
        "config": base.model_dump(mode="json"),
        "continuation": "seed_parent_first_generation" if seeded else "fresh_only",
        "input_layout": "single_bootstrap_json_no_other_mounts",
    }
    if seeded:
        expected.update(
            seed=seed.model_dump(mode="json"), seeded_submission_id=str(receipt_id)
        )
    assert canonical_bytes(expected) == canonical_bytes(actual)
    assert parse_bootstrap(canonical_bytes(expected)) == actual
    assert schema in revision.controller_bootstrap_delta_sql()
    wrong = {**actual.model_dump(mode="json"), "config_sha256": "f" * 64}
    with pytest.raises(ValueError):
        parse_bootstrap(canonical_bytes(wrong))
    if seeded:
        wrong = actual.model_dump(mode="json")
        wrong["seed"]["source"] += "# mismatched source\n"
        with pytest.raises(ValueError):
            parse_bootstrap(canonical_bytes(wrong))
