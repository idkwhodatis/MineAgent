"""Pinned inherited-body preservation; actual027 PostgreSQL replay remains required."""

import ast
import hashlib
import re

import pytest

from backend.app import oe_structured_shared_process_sql as module


def functions(sql):
    result = {}
    for match in re.finditer(r"CREATE (?:OR REPLACE )?FUNCTION @SCHEMA@\.(\w+)\(", sql):
        result[match[1]] = sql[
            match.start() : sql.index("END $$;", match.start()) + len("END $$;")
        ]
    return result


def test_exact_installed_function_source_hashes_and_latest_revision_provenance():
    for name, (revision, sha) in module.PINS.items():
        original = module.frozen_function(name)
        assert hashlib.sha256(original.encode()).hexdigest() == sha
        assert revision in {"025", "027c"}
        for path in module.ROOT.glob("*.py"):
            if (
                not path.stem[:3].isdigit()
                or path.stem.split("_")[0] <= revision
                or path.stem.split("_")[0] >= "028"
            ):
                continue
            for node in ast.parse(path.read_text()).body:
                if (
                    not isinstance(node, ast.Assign)
                    or not isinstance(node.value, ast.Constant)
                    or not isinstance(node.value.value, str)
                ):
                    continue
                if any(
                    isinstance(t, ast.Name) and t.id.isupper() and "DOWN" not in t.id
                    for t in node.targets
                ):
                    assert not re.search(
                        r"CREATE (?:OR REPLACE )?FUNCTION (?:@SCHEMA@\.)?"
                        + name
                        + r"\(",
                        node.value.value,
                    ), (path, name)


def test_historical_order_body_unchanged_after_removing_exact_new_branch():
    name = "mineagent_rd_controller_receipt_order"
    upgraded = functions(module.render_upgrade_sql())[name]
    assert upgraded.count(module.ORDER_EXTENSION) == 1
    assert upgraded.replace(module.ORDER_EXTENSION, "", 1) == module.frozen_function(
        name
    )


def test_historical_rd_semantics_byte_preserved_except_exact_global_child_union():
    name = "mineagent_rd_controller_consistent"
    upgraded = functions(module.render_upgrade_sql())[name]
    assert upgraded.count(module.NEW_CHILD_BRANCH) == 1
    assert upgraded.replace(
        module.NEW_CHILD_BRANCH, module.OLD_CHILD_BRANCH, 1
    ) == module.frozen_function(name)
    assert "mineagent.rdagent-operational-bootstrap.v1" in upgraded
    for schema in (
        "mineagent.rdagent-provider-operational-bootstrap.v1",
        "mineagent.rdagent-provider-seeded-bootstrap.v1",
        "mineagent.rdagent-seeded-operational-bootstrap.v1",
    ):
        assert schema in upgraded
    assert "bs.charge IS DISTINCT FROM br.amounts" in upgraded
    assert "Controller unresolved budget must remain held" in upgraded


def test_every_original025_loop_and_restriction_remains_exact():
    name = "mineagent_structured_consistent"
    upgraded = functions(module.render_upgrade_sql())[name]
    original = module._replaceable(module.frozen_function(name))
    assert upgraded.count(module.NATIVE_CHECK) == 1
    restored = upgraded.replace(module.NATIVE_CHECK, "", 1)
    restored = restored.replace(
        module.OLD_NATIVE_GATE + "\n AND NOT " + module.NATIVE_CONSUMER,
        module.OLD_NATIVE_GATE,
        1,
    )
    assert restored == original
    assert "Closed structured engine has no model or policy IPC" in upgraded
    assert "Structured source exposure cannot be orphaned" in upgraded


def test_downgrade_only_restores_reviewed_functions_without_dropping_evidence():
    restored = functions(module.render_downgrade_sql())
    assert set(restored) == set(module.PINS)
    for name in module.PINS:
        assert restored[name] == module._replaceable(module.frozen_function(name))
    assert "DROP TABLE" not in module.render_downgrade_sql()
    assert "CASCADE" not in module.render_downgrade_sql()


@pytest.mark.parametrize(
    "guard",
    [
        "NEW.kind='controller_completion' AND a.profile IS DISTINCT FROM 'mineagent.oe-structured-orchestrator.v1'",
        "a.schema_version,a.role,a.ipc_policy,a.generation,a.launch_ordinal",
        "oe.controller_admission_id=a.id AND oe.id=a.logical_operation_id",
        "oe.owner_id,oe.run_id,oe.attempt_id,oe.generation",
        "os.config->>'schema_version'='mineagent.oe-structured-provider-run.v1'",
        "mineagent_oes_controller_payload(NEW.kind,NEW.payload)",
        "NEW.kind<>'controller_completion'",
        "NEW.kind='controller_completion' AND",
        "ps.status='stopped'",
        "p.kind='controller_child'",
    ],
)
def test_new_completion_never_extends_other_roles_or_early_prefix(guard):
    assert guard in module.ORDER_EXTENSION


@pytest.mark.parametrize(
    "guard",
    [
        "oe.owner_id::text,oe.run_id::text,oe.attempt_id::text,oe.generation::text,oe.id::text",
        "value->>'owner_id',value->>'run_id',value->>'attempt_id',value->>'generation',value->>'logical_operation_id'",
        "rr.purpose,rr.engine",
        "value->>'ticket_sha256' IS NULL",
        "'mineagent.process-admission.v2','native_evaluator','none','1'",
    ],
)
def test_native_consumer_is_exact_scope_not_profile_or_same_owner_only(guard):
    assert guard in module.NATIVE_CONSUMER
    assert "mineagent_oes_native_check((value->>'id')::uuid)" in module.NATIVE_CHECK


def test_source_drift_fails_instead_of_silently_rebuilding_old_functions(
    monkeypatch, tmp_path
):
    path = tmp_path / "027c_changed.py"
    name = "mineagent_rd_controller_receipt_order"
    path.write_text("UPGRADE_SQL = " + repr(module.frozen_function(name) + "\n"))
    monkeypatch.setattr(module, "ROOT", tmp_path)
    # Surrounding whitespace is not a function change.
    assert module.frozen_function(name)
    path.write_text(
        "UPGRADE_SQL = "
        + repr(
            "CREATE OR REPLACE FUNCTION @SCHEMA@."
            + name
            + "() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$;"
        )
    )
    with pytest.raises(ValueError, match="changed"):
        module.frozen_function(name)


def test_fragment_is_function_only_and_preserves_closed_default():
    sql = module.render_upgrade_sql()
    assert sql == module.render_upgrade_sql()
    assert set(functions(sql)) == set(module.PINS)
    assert "ALTER TABLE" not in sql and "CREATE TRIGGER" not in sql
    assert "mineagent_neutral_process_consistent" not in sql
    assert "mineagent_rd_shared_consistent" not in sql
    assert "mineagent_structured_stopped" not in sql
    assert "IF NOT protected THEN RETURN NEW; END IF;" in sql


def test_new_controller_child_union_never_aliases_declared_records():
    # PostgreSQL resolves a record variable before same-named query aliases;
    # the inherited function declares a/rr/br/bs/ps before this global query.
    for name in ("a", "rr", "br", "bs", "ps"):
        assert not re.search(
            r"\b(?:FROM|JOIN)\s+[\w.@]+\s+" + name + r"\b", module.NEW_CHILD_BRANCH
        )
    assert "JOIN @SCHEMA@.research_runs normal_run" in module.NEW_CHILD_BRANCH
