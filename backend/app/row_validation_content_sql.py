"""Separately selectable OE content group on top of row_validation_invariant_sql.

Ten fresh checks, five graph sites (one shared with the minimal group). This is
an isolated candidate, not a migration. Raw epoch content only: its relational
spec-selected schema CASE is deliberately unchanged. No checkpoint/prefix,
shared program, receipt, measurement-matching or population bypass is included.
The caller must use render_candidate_sql() in an explicit transaction.
"""

import ast
import hashlib
import re

from backend.app import row_validation_invariant_sql as base

SOURCES = {
    "mineagent_oes_control_check": "028_oe_structured_search.py",
    "mineagent_oes_candidate_check": "031_workflow_structured_stage.py",
    "mineagent_oes_execution_check": "028_oe_structured_search.py",
    "mineagent_oes_outcome_check": "028_oe_structured_search.py",
    "mineagent_oes_epoch_check": "031_workflow_structured_stage.py",
    "mineagent_structured_contract": "027a_contract_repairs.py",
    "mineagent_oes_graph_check": "031_workflow_structured_stage.py",
    "mineagent_oes_consistent": "031_workflow_structured_stage.py",
    "mineagent_oes_controller_check": "032e_rd_workflow_cost.py",
}
EXTRA_CHANGED = tuple(SOURCES)[:5]
CHANGED = base.CHANGED + tuple(n for n in EXTRA_CHANGED if n not in base.CHANGED)
PURE = base.PURE + ("mineagent_structured_contract",)
CONSTRAINTS = (
    (
        "oe_structured_control_states",
        "ck_rvi_oec_control_raw",
        "@SCHEMA@.mineagent_structured_raw_commitment(state_canonical,state_canonical::jsonb,state_sha256,786432) IS TRUE",
    ),
    (
        "oe_structured_candidates",
        "ck_rvi_oec_candidate",
        "@SCHEMA@.mineagent_oes_contract('candidate',candidate_canonical::jsonb) IS TRUE",
    ),
    (
        "oe_structured_candidates",
        "ck_rvi_oec_candidate_raw",
        "@SCHEMA@.mineagent_structured_raw_commitment(candidate_canonical,candidate_canonical::jsonb,candidate_sha256,32768) IS TRUE",
    ),
    (
        "oe_structured_executions",
        "ck_rvi_oec_input",
        "@SCHEMA@.mineagent_structured_contract('input',strategy_input) IS TRUE",
    ),
    (
        "oe_structured_executions",
        "ck_rvi_oec_input_raw",
        "@SCHEMA@.mineagent_structured_raw_commitment(strategy_input_canonical,strategy_input,strategy_input_sha256,1048576) IS TRUE",
    ),
    (
        "oe_structured_engine_outcomes",
        "ck_rvi_oec_result",
        "@SCHEMA@.mineagent_structured_contract('result',result_canonical::jsonb) IS TRUE",
    ),
    (
        "oe_structured_engine_outcomes",
        "ck_rvi_oec_measurement",
        "@SCHEMA@.mineagent_oes_contract('measurement',measurement_canonical::jsonb) IS TRUE",
    ),
    (
        "oe_structured_engine_outcomes",
        "ck_rvi_oec_result_raw",
        "@SCHEMA@.mineagent_structured_raw_commitment(result_canonical,result_canonical::jsonb,result_sha256,16777216) IS TRUE",
    ),
    (
        "oe_structured_engine_outcomes",
        "ck_rvi_oec_measurement_raw",
        "@SCHEMA@.mineagent_structured_raw_commitment(measurement_canonical,measurement_canonical::jsonb,measurement_sha256,65536) IS TRUE",
    ),
    (
        "oe_structured_epochs",
        "ck_rvi_oec_epoch_raw",
        "@SCHEMA@.mineagent_structured_raw_commitment(bootstrap_canonical,bootstrap_canonical::jsonb,bootstrap_sha256,262144) IS TRUE",
    ),
)
REQUIRED_COLUMNS = {
    "oe_structured_control_states": ("state_canonical", "state_sha256"),
    "oe_structured_candidates": ("candidate_canonical", "candidate_sha256"),
    "oe_structured_executions": (
        "strategy_input",
        "strategy_input_canonical",
        "strategy_input_sha256",
    ),
    "oe_structured_engine_outcomes": (
        "result_canonical",
        "result_sha256",
        "measurement_canonical",
        "measurement_sha256",
    ),
    "oe_structured_epochs": ("bootstrap_canonical", "bootstrap_sha256"),
}


def original(name):
    if name in base.SOURCES:
        return base.original(name)
    filename = SOURCES[name]
    if name == "mineagent_structured_contract":
        tree = ast.parse((base.VERSIONS / filename).read_text())
        sql = next(
            ast.literal_eval(n.value)
            for n in tree.body
            if isinstance(n, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "UPGRADE_SQL" for t in n.targets
            )
        )
        if (
            hashlib.sha256(sql.encode()).hexdigest()
            != "bb5492b3a451330bb6317352dd400cf937c4b4219def083d0146d4d5dd403ba6"
        ):
            raise ValueError("Exact frozen027a structured contract required")
    else:
        sql = base.literal(filename)
    matches = list(
        re.finditer(
            r"CREATE(?: OR REPLACE)? FUNCTION (?:@SCHEMA@\.)?"
            + re.escape(name)
            + r"\(",
            sql,
        )
    )
    if len(matches) != 1:
        raise ValueError("Exact frozen content function required: " + name)
    start = matches[0].start()
    return sql[start : sql.index("$$;", start) + 3].replace(
        "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
    )


def edits():
    return {
        "mineagent_oes_control_check": (
            " OR @SCHEMA@.mineagent_structured_raw_commitment(c.state_canonical,c.state_canonical::jsonb,c.state_sha256,786432) IS NOT TRUE\n",
        ),
        "mineagent_oes_candidate_check": (
            " OR @SCHEMA@.mineagent_oes_contract('candidate',j) IS NOT TRUE\n",
            " OR @SCHEMA@.mineagent_structured_raw_commitment(c.candidate_canonical,j,c.candidate_sha256,32768) IS NOT TRUE\n",
        ),
        "mineagent_oes_execution_check": (
            " OR @SCHEMA@.mineagent_structured_contract('input',j) IS NOT TRUE\n",
            " OR @SCHEMA@.mineagent_structured_raw_commitment(e.strategy_input_canonical,j,e.strategy_input_sha256,1048576) IS NOT TRUE\n",
        ),
        "mineagent_oes_outcome_check": (
            " OR @SCHEMA@.mineagent_structured_contract('result',result) IS NOT TRUE\n",
            " OR @SCHEMA@.mineagent_oes_contract('measurement',m) IS NOT TRUE\n",
            " OR @SCHEMA@.mineagent_structured_raw_commitment(o.result_canonical,result,o.result_sha256,16777216) IS NOT TRUE\n",
            " OR @SCHEMA@.mineagent_structured_raw_commitment(o.measurement_canonical,m,o.measurement_sha256,65536) IS NOT TRUE\n",
        ),
        "mineagent_oes_epoch_check": (
            " OR @SCHEMA@.mineagent_structured_raw_commitment(e.bootstrap_canonical,j,e.bootstrap_sha256,262144) IS NOT TRUE\n",
        ),
    }


def candidate(name):
    sql = base.candidate(name) if name in base.CHANGED else original(name)
    for expression in edits().get(name, ()):
        sql = base.replace_once(sql, expression, "")
    return sql


def render_upgrade_sql():
    checks = "\n".join(
        f"ALTER TABLE @SCHEMA@.{table} ADD CONSTRAINT {name} CHECK ({expression});"
        for table, name, expression in base.CONSTRAINTS + CONSTRAINTS
    )
    return checks + "\n" + "\n".join(candidate(name) for name in CHANGED)


def render_downgrade_sql():
    return (
        "\n".join(original(name) for name in CHANGED)
        + "\n"
        + "\n".join(
            f"ALTER TABLE @SCHEMA@.{table} DROP CONSTRAINT {name};"
            for table, name, _ in reversed(base.CONSTRAINTS + CONSTRAINTS)
        )
    )


def render_preflight_sql(direction="upgrade"):
    sql = base.render_preflight_sql(direction)
    if direction == "downgrade":
        shared = "mineagent_oes_candidate_check"
        sql = base.replace_once(
            sql,
            base.function_assertion(base.candidate(shared)),
            base.function_assertion(candidate(shared)),
        )
    assertions = []
    for name in SOURCES:
        if name in base.SOURCES:
            continue
        source = (
            candidate(name)
            if direction == "downgrade" and name in EXTRA_CHANGED
            else original(name)
        )
        assertions.append(base.function_assertion(source))
    for table, columns in REQUIRED_COLUMNS.items():
        for column in ("owner_id", "run_id", *columns):
            assertions.append(f"""IF NOT EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid='@SCHEMA@.{table}'::regclass
 AND attname='{column}' AND attnotnull AND NOT attisdropped)
 THEN RAISE EXCEPTION 'Content invariant NOT NULL differs: {table}.{column}'; END IF;""")
        assertions.append(f"""IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='@SCHEMA@.{table}'::regclass
 AND tgfoid='@SCHEMA@.mineagent_oes_append()'::regprocedure
 AND tgenabled IN ('O','A') AND tgtype=31 AND NOT tgisinternal
 AND tgnargs=0 AND tgqual IS NULL AND tgattr::text='')
 OR NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='@SCHEMA@.{table}'::regclass
 AND tgfoid='@SCHEMA@.mineagent_oes_consistent()'::regprocedure
 AND tgenabled IN ('O','A') AND tgtype=29 AND tgdeferrable AND tginitdeferred AND NOT tgisinternal
 AND tgnargs=0 AND tgqual IS NULL AND tgattr::text='')
 THEN RAISE EXCEPTION 'Content invariant append/commit routing differs: {table}'; END IF;""")
    for table, name in scope_foreign_keys().items():
        assertions.append(f"""IF NOT EXISTS(SELECT 1 FROM pg_constraint k
 WHERE k.conrelid='@SCHEMA@.{table}'::regclass AND k.conname='{name}'
 AND k.contype='f' AND k.confrelid='@SCHEMA@.oe_structured_specs'::regclass
 AND k.convalidated AND k.condeferrable AND k.condeferred
 AND k.confmatchtype='s' AND k.confupdtype='a' AND k.confdeltype='a'
 AND ARRAY(SELECT a.attname::text FROM unnest(k.conkey) WITH ORDINALITY t(n,i)
   JOIN pg_attribute a ON a.attrelid=k.conrelid AND a.attnum=t.n ORDER BY t.i)=ARRAY['owner_id','run_id']
 AND ARRAY(SELECT a.attname::text FROM unnest(k.confkey) WITH ORDINALITY t(n,i)
   JOIN pg_attribute a ON a.attrelid=k.confrelid AND a.attnum=t.n ORDER BY t.i)=ARRAY['owner_id','run_id'])
 THEN RAISE EXCEPTION 'Content invariant committed spec scope differs: {table}'; END IF;""")
    for table, name, _ in CONSTRAINTS:
        if direction == "upgrade":
            assertions.append(f"""IF EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='@SCHEMA@.{table}'::regclass AND conname='{name}')
 THEN RAISE EXCEPTION 'Fresh content invariant already exists: {name}'; END IF;""")
        else:
            assertions.append(f"""IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='@SCHEMA@.{table}'::regclass AND conname='{name}' AND contype='c' AND convalidated AND NOT condeferrable)
 THEN RAISE EXCEPTION 'Validated content invariant missing: {name}'; END IF;""")
    return sql + "\nDO $rvi$ BEGIN\n" + "\n".join(assertions) + "\nEND $rvi$;"


def render_candidate_sql(direction="upgrade"):
    if direction not in ("upgrade", "downgrade"):
        raise ValueError("Expected upgrade or downgrade")
    return "\n".join(
        (
            base.render_maintenance_sql(),
            render_preflight_sql(direction),
            render_upgrade_sql() if direction == "upgrade" else render_downgrade_sql(),
        )
    )


def scope_foreign_keys():
    """Exact frozen deferred non-null scope bridges, unchanged by this candidate."""
    sql = base.literal("028_oe_structured_search.py", "TABLE_SQL")
    result = {}
    for table in REQUIRED_COLUMNS:
        match = re.findall(
            r"ALTER TABLE @SCHEMA@\."
            + table
            + r" ADD CONSTRAINT (\w+) FOREIGN KEY \(owner_id,run_id\) REFERENCES @SCHEMA@\.oe_structured_specs \(owner_id,run_id\) DEFERRABLE INITIALLY DEFERRED;",
            sql,
        )
        if len(match) != 1:
            raise ValueError("Exact frozen spec scope required: " + table)
        result[table] = match[0]
    return result
