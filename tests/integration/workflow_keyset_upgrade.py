"""Same actual workflow graph before/after032g and an independent cold restore.

No admission, provider, child execution, release or note writes happen here.
The caller has already completed the exact031/032 restore and032f outbox gates.
"""

import hashlib

from sqlalchemy import text

PREDECESSOR = "032f_shared_validation_cost"
CURRENT = "032g_schema_keyset_cost"
BODY_HASHES = {
    PREDECESSOR: "fbb1e9e697d26ad5314816eb676226265061d5c82724a045cb66be09ff430208",
    CURRENT: "c4bb8d7b8744adbf4904b1782abfb5ec9aa39e75778fa148c0c566ec0eb70afe",
}


def catalog(connection):
    """All function properties plus unchanged relational and security metadata."""
    queries = {
        "functions": """SELECT p.proname,pg_get_function_identity_arguments(p.oid),
          to_jsonb(p)-'prosrc',p.prosrc,pg_get_expr(p.proargdefaults,0)
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname=current_schema() ORDER BY p.proname,2""",
        "relations": """SELECT r.oid,r.relname,r.relkind,r.relpersistence,r.relowner,
          r.relacl,r.reloptions,r.relrowsecurity,r.relforcerowsecurity,
          r.relispartition,r.relreplident,r.relchecks,r.relhasrules,r.relhastriggers
          FROM pg_class r JOIN pg_namespace n ON n.oid=r.relnamespace
          WHERE n.nspname=current_schema() ORDER BY r.relname""",
        "columns": """SELECT r.relname,a.attnum,to_jsonb(a),pg_get_expr(d.adbin,d.adrelid)
          FROM pg_attribute a JOIN pg_class r ON r.oid=a.attrelid
          JOIN pg_namespace n ON n.oid=r.relnamespace
          LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
          WHERE n.nspname=current_schema() AND a.attnum>0 ORDER BY r.relname,a.attnum""",
        "constraints": """SELECT r.relname,c.conname,to_jsonb(c),pg_get_constraintdef(c.oid)
          FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace
          LEFT JOIN pg_class r ON r.oid=c.conrelid
          WHERE n.nspname=current_schema() ORDER BY r.relname,c.conname""",
        "triggers": """SELECT r.relname,t.tgname,to_jsonb(t),pg_get_triggerdef(t.oid)
          FROM pg_trigger t JOIN pg_class r ON r.oid=t.tgrelid
          JOIN pg_namespace n ON n.oid=r.relnamespace
          WHERE n.nspname=current_schema() ORDER BY r.relname,t.tgname""",
        "indexes": """SELECT r.relname,to_jsonb(i),pg_get_indexdef(i.indexrelid)
          FROM pg_index i JOIN pg_class r ON r.oid=i.indexrelid
          JOIN pg_namespace n ON n.oid=r.relnamespace
          WHERE n.nspname=current_schema() ORDER BY r.relname""",
        "types": """SELECT t.typname,to_jsonb(t) FROM pg_type t
          JOIN pg_namespace n ON n.oid=t.typnamespace
          WHERE n.nspname=current_schema() ORDER BY t.typname""",
        "operators": """SELECT o.oprname,o.oprleft,o.oprright,to_jsonb(o)
          FROM pg_operator o JOIN pg_namespace n ON n.oid=o.oprnamespace
          WHERE n.nspname=current_schema() ORDER BY 1,2,3""",
        "policies": """SELECT r.relname,p.polname,to_jsonb(p)
          FROM pg_policy p JOIN pg_class r ON r.oid=p.polrelid
          JOIN pg_namespace n ON n.oid=r.relnamespace
          WHERE n.nspname=current_schema() ORDER BY 1,2""",
    }
    result = {
        name: connection.execute(text(sql)).all() for name, sql in queries.items()
    }
    assert all(len(rows) <= 16384 for rows in result.values())
    # The parser's pg_node_tree stores source :location offsets. A multi-function
    # predecessor batch and a single-function successor have different offsets
    # even for identical DEFAULT 0. Compare deparsed defaults, never those offsets.
    result["functions"] = [
        (name, arguments, metadata | {"proargdefaults": defaults}, body)
        for name, arguments, metadata, body, defaults in result["functions"]
    ]
    return result


def assert_only_schema_body_changed(before, after, schema):
    assert before.keys() == after.keys()
    assert {key: rows for key, rows in before.items() if key != "functions"} == {
        key: rows for key, rows in after.items() if key != "functions"
    }
    old = {(row[0], row[1]): row for row in before["functions"]}
    new = {(row[0], row[1]): row for row in after["functions"]}
    assert old.keys() == new.keys()
    changed = {key for key in old if old[key] != new[key]}
    assert len(changed) == 1
    key = changed.pop()
    assert key[0] == "mineagent_rd_schema"
    assert old[key][:3] == new[key][:3]
    for revision, row in ((PREDECESSOR, old[key]), (CURRENT, new[key])):
        normalized = row[3].replace('"' + schema + '"', "@SCHEMA@")
        assert hashlib.sha256(normalized.encode()).hexdigest() == BODY_HASHES[revision]


def graph_reads(factory, settings, completed, client, headers, foreign, owner):
    """Compare workflow metadata, every retained result, and terminal identity."""
    from workflow_restore_replay import _paths

    from backend.app.execution_snapshot_history import historical_session
    from backend.app.oe_structured_result_history import read_result
    from backend.app.research_workflow_oe_terminal import read_oe_terminal_in_session
    from packages.factors.contracts import canonical_bytes

    paths = list(_paths(factory, owner, completed.workflow_id))
    normal = f"/api/v1/engine-runs/structured-search/runs/{completed.oe_run_id}"
    paths.append(normal)
    paths.extend(normal + f"/executions/{eid}" for eid in completed.execution_ids)
    metadata = {}
    for path in paths:
        response = client.get(path, headers=headers)
        assert response.status_code in {200, 409}, (path, response.text)
        assert client.get(path, headers=foreign).status_code == 404
        metadata[path] = (response.status_code, canonical_bytes(response.json()))
    with historical_session(factory, owner, settings) as session:
        terminal = read_oe_terminal_in_session(
            session, owner, completed.workflow_id, completed.oe_stage_id, settings
        )
    assert canonical_bytes(terminal) == canonical_bytes(completed.terminal)
    results = tuple(
        read_result(factory, settings, owner_id=owner, execution_id=eid)
        for eid in completed.execution_ids
    )
    return metadata, canonical_bytes(terminal), results


def assert_upgrade_and_restore(databases, tmp_path, source, env, completed, notes):
    import test_backup_restore as legacy
    from normal_oe_note_flow import assert_note_reads, assert_notes_restore
    from oe_structured_restore_replay import _inventory
    from test_factor_restore import _files

    from backend.app.operations_backup import BackupLimits, PostgresDatabase

    owner = env.account.user.id
    counts = (len(env.rd_calls), len(env.oe_calls))
    inventory = _inventory(env.factory)
    files = _files(env.settings.storage_root)
    before = PostgresDatabase(
        source.url.render_as_string(hide_password=False)
    ).source_snapshot(BackupLimits())
    reads = graph_reads(
        env.factory,
        env.settings,
        completed,
        env.client,
        env.headers,
        env.foreign,
        owner,
    )
    assert_note_reads(env.client, env.headers, env.foreign, notes)
    with source.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).all() == [(PREDECESSOR,)]
        schema = connection.exec_driver_sql("SELECT current_schema()").scalar_one()
        prior_catalog = catalog(connection)
    legacy._upgrade(source, CURRENT)
    with source.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).all() == [(CURRENT,)]
        assert_only_schema_body_changed(prior_catalog, catalog(connection), schema)
    after = PostgresDatabase(
        source.url.render_as_string(hide_password=False)
    ).source_snapshot(BackupLimits())
    closures = {key for key in before if key.endswith("_closure")}
    assert closures == {key for key in after if key.endswith("_closure")}
    assert all(before[key] == after[key] for key in closures)
    assert (
        graph_reads(
            env.factory,
            env.settings,
            completed,
            env.client,
            env.headers,
            env.foreign,
            owner,
        )
        == reads
    )
    assert_note_reads(env.client, env.headers, env.foreign, notes)
    assert _inventory(env.factory) == inventory
    assert _files(env.settings.storage_root) == files

    def restored_reads(factory, settings, client, headers, foreign):
        assert (
            graph_reads(factory, settings, completed, client, headers, foreign, owner)
            == reads
        )

    assert_notes_restore(
        databases,
        tmp_path,
        source,
        env,
        completed,
        notes,
        expected_revision=CURRENT,
        restored_check=restored_reads,
    )
    assert _inventory(env.factory) == inventory
    assert _files(env.settings.storage_root) == files
    assert (len(env.rd_calls), len(env.oe_calls)) == counts
