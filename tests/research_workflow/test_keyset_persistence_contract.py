"""Portable proof wiring and catalog rejection tests, not actual PG acceptance."""

import ast
import copy
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from backend.app.rdagent_provider_revision import function
from tests.integration import sql_candidate_predecessor as predecessor
from tests.integration import workflow_keyset_upgrade as proof

ROOT = Path(__file__).resolve().parents[2]


def test_historical_gates_precede_same_graph_upgrade_and_third_independent_restore():
    workflow = (
        ROOT / ".github/workflows/mineagent-configured-analysis.yml"
    ).read_text()
    step = workflow.split(
        "      - name: Require complete workflow and input-source contracts\n", 1
    )[1].split("      - name:", 1)[0]
    assert (
        "python -m pytest tests/research_workflow tests/research_inputs tests/workflow_oe -q -p no:cacheprovider"
        in step
    )
    assert "assert len(cases)==972" in step
    assert "('failure','error','skipped')" in step
    assert 'test "$status" = 0' in step
    source = (
        ROOT / "tests/integration/test_workflow_structured_stage_flow.py"
    ).read_text()
    stages = (
        'legacy._upgrade(source, "031_workflow_structured_stage")',
        "completed = run_bridge(env, source, tmp_path)",
        "assert_restore(drill_databases",
        'legacy._upgrade(source, "032_normal_oe_note_pins")',
        "notes = create_released_notes(env, completed)",
        "assert_notes_restore(\n",
        'legacy._upgrade(source, "032f_shared_validation_cost")',
        "assert_native_workflow_outbox_retained(source, completed.oe_run_id)",
        "assert_upgrade_and_restore(\n",
    )
    positions = [source.index(stage) for stage in stages]
    assert positions == sorted(positions)
    assert source.count("run_bridge(env, source, tmp_path)") == 1
    assert source.count("yield from drill_databases.__wrapped__()") == 2
    assert "keyset_restore_databases.restored" in source
    assert "note_restore_databases.restored" in source
    assert "drill_databases.restored" in source
    assert ")\n                == 3" in source
    helper = (ROOT / "tests/integration/workflow_keyset_upgrade.py").read_text()
    assert 'CURRENT = "032g_schema_keyset_cost"' in helper
    assert 'PREDECESSOR = "032f_shared_validation_cost"' in helper
    assert (
        helper.index("prior_catalog = catalog(connection)")
        < helper.index("legacy._upgrade(source, CURRENT)")
        < helper.index("assert_only_schema_body_changed(prior_catalog")
    )
    assert "expected_revision=CURRENT" in helper
    assert "restored_check=restored_reads" in helper
    assert helper.count("== reads") == 2
    assert "all(before[key] == after[key] for key in closures)" in helper
    assert helper.count("_inventory(env.factory) == inventory") == 2
    assert helper.count("_files(env.settings.storage_root) == files") == 2
    tree = ast.parse(helper)
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"post", "put", "patch", "delete"}
        for node in ast.walk(tree)
    )
    note = (ROOT / "tests/integration/normal_oe_note_flow.py").read_text()
    assert 'expected_revision="032_normal_oe_note_pins"' in note
    assert 'manifest["schema_revision"] == expected_revision' in note
    assert "restored_check(factory, settings, client, headers, foreign)" in note


def catalog_pair():
    rows = []
    for prefix in ("032f_shared_validation_cost", "032g_schema_keyset_cost"):
        path = ROOT / "backend/alembic/versions" / (prefix + ".py")
        frozen = next(
            ast.literal_eval(node.value)
            for node in ast.parse(path.read_text()).body
            if isinstance(node, ast.Assign)
            and any(
                getattr(target, "id", "") == "UPGRADE_SQL" for target in node.targets
            )
        )
        sql = function(frozen, "mineagent_rd_schema")
        body = sql.split("AS $$", 1)[1].removesuffix("$$;")
        rows.append(
            {
                "functions": [
                    (
                        "mineagent_rd_schema",
                        "v jsonb, rule jsonb, defs jsonb, depth integer",
                        {"proacl": None, "provolatile": "i"},
                        body.replace("@SCHEMA@", '"test_schema"'),
                    ),
                    ("untouched", "", {"proacl": None}, "exact historical body"),
                ],
                "constraints": [
                    ("historical", {"conenforced": True, "convalidated": True})
                ],
                "triggers": [("historical", {"tgenabled": "O"})],
            }
        )
    return rows


def test_catalog_parity_accepts_only_exact_main_body_hash_change():
    before, after = catalog_pair()
    proof.assert_only_schema_body_changed(before, after, "test_schema")

    class Connection:
        def __init__(self, functions, location, default="0"):
            self.functions, self.location, self.default = functions, location, default

        def execute(self, statement):
            sql = str(statement)
            if "FROM pg_proc p" not in sql:
                return NS(all=list)
            assert "pg_get_expr(p.proargdefaults,0)" in sql
            rows = [
                (
                    name,
                    arguments,
                    metadata
                    | {
                        "proargdefaults": "{CONST :location " + str(self.location) + "}"
                    },
                    body,
                    self.default,
                )
                for name, arguments, metadata, body in self.functions
            ]
            return NS(all=lambda: rows)

    # Raw catalog parse locations differ between032f's SQL batch and032g's
    # standalone CREATE, without a semantic DEFAULT change.
    old = proof.catalog(Connection(before["functions"], 28142))
    new = proof.catalog(Connection(after["functions"], 18))
    assert old["functions"][0][2]["proargdefaults"] == "0"
    assert new["functions"][0][2]["proargdefaults"] == "0"
    proof.assert_only_schema_body_changed(old, new, "test_schema")
    changed_default = proof.catalog(Connection(after["functions"], 18, "1"))
    with pytest.raises(AssertionError):
        proof.assert_only_schema_body_changed(old, changed_default, "test_schema")


@pytest.mark.parametrize(
    "change",
    [
        "same",
        "main_body",
        "properties",
        "other_body",
        "constraint",
        "trigger",
        "missing",
    ],
)
def test_catalog_parity_rejects_any_additional_or_inexact_change(change):
    before, after = catalog_pair()
    after = copy.deepcopy(after)
    if change == "same":
        after = before
    elif change == "missing":
        after["functions"].pop()
    elif change == "main_body":
        row = after["functions"][0]
        after["functions"][0] = (*row[:3], row[3] + " ")
    elif change == "properties":
        after["functions"][0][2]["proacl"] = ["unreviewed"]
    elif change == "other_body":
        row = after["functions"][1]
        after["functions"][1] = (*row[:3], "changed")
    elif change == "constraint":
        after["constraints"][0][1]["convalidated"] = False
    else:
        after["triggers"][0][1]["tgenabled"] = "D"
    with pytest.raises(AssertionError):
        proof.assert_only_schema_body_changed(before, after, "test_schema")


@pytest.mark.parametrize("revision", [predecessor.PREDECESSOR, predecessor.SUCCESSOR])
def test_disposable_predecessor_selection_is_exact_and_asserted(monkeypatch, revision):
    class Connection:
        def __init__(self):
            self.revisions = [(revision,)]

        def exec_driver_sql(self, sql):
            assert sql == "SELECT version_num FROM alembic_version"
            return NS(all=lambda: self.revisions)

    connection = Connection()
    calls = []

    def downgrade(config, target):
        assert config.attributes["connection"] is connection
        assert target == predecessor.PREDECESSOR
        calls.append(target)
        connection.revisions = [(target,)]

    monkeypatch.setattr(predecessor.command, "downgrade", downgrade)
    predecessor.select_predecessor(connection)
    assert calls == (
        [predecessor.PREDECESSOR] if revision == predecessor.SUCCESSOR else []
    )
    assert connection.revisions == [(predecessor.PREDECESSOR,)]


@pytest.mark.parametrize(
    "rows", [[], [("unknown",)], [(predecessor.PREDECESSOR,), (predecessor.SUCCESSOR,)]]
)
def test_disposable_predecessor_selection_rejects_unknown_revision_without_change(
    monkeypatch, rows
):
    connection = NS(exec_driver_sql=lambda sql: NS(all=lambda: rows))
    monkeypatch.setattr(
        predecessor.command, "downgrade", lambda *a: pytest.fail("Unexpected downgrade")
    )
    with pytest.raises(AssertionError):
        predecessor.select_predecessor(connection)
