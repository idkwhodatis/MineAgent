"""Disposable current-head RD-v3 proof; NOT a migration or installed-schema gate.

One fresh row CHECK replaces three repeated pure validations on the same tuple.
All relational reads, payload checks, exception order and commit boundaries stay.
Run the entire batch in a caller-owned rollback-capable maintenance transaction.
No production acceptance or native deadline improvement is established here.
"""

import ast
import hashlib

from backend.app import row_validation_invariant_sql as prior

PREDECESSOR = "032g_schema_keyset_cost"
CHANGED = prior.CHANGED[:3]
CONSTRAINT_NAME = "ck_probe_rd_v3_config"
EXPRESSION = """(CASE WHEN purpose='factor_research' AND engine='rdagent'
 AND config->>'schema_version'='mineagent.factor-research.v3'
 THEN @SCHEMA@.mineagent_rd_workflow_config(config) IS TRUE ELSE true END) IS TRUE"""
KEYSET_SHA256 = "958439dff325fb5d7d81f4b7becc311ab672653bdb159a428eada027e4a5bf8e"


def original(name):
    if name != "mineagent_rd_schema":
        return prior.original(name)
    tree = ast.parse((prior.VERSIONS / "032g_schema_keyset_cost.py").read_text())
    values = [
        ast.literal_eval(n.value)
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "UPGRADE_SQL" for t in n.targets)
    ]
    if (
        len(values) != 1
        or hashlib.sha256(values[0].encode()).hexdigest() != KEYSET_SHA256
    ):
        raise ValueError("Exact reviewed032g main source required")
    return values[0]


def edits(name):
    if name not in CHANGED:
        raise ValueError("Only three RD readers belong to this probe")
    # Provider graph's SECOND edit removes payload validation. Never take it.
    before, after = prior.edits()[name][0]
    after = after.replace(
        "IN ('mineagent.factor-research.v2','mineagent.factor-research.v3')",
        "='mineagent.factor-research.v3'",
    )
    return before, after


def candidate(name):
    before, after = edits(name)
    return prior.replace_once(original(name), before, after)


def render_preflight_sql(direction="upgrade"):
    if direction not in {"upgrade", "downgrade"}:
        raise ValueError("Expected upgrade or downgrade")
    checks = [
        f"""IF (SELECT array_agg(version_num ORDER BY version_num) FROM @SCHEMA@.alembic_version)
 IS DISTINCT FROM ARRAY['{PREDECESSOR}']::varchar[]
 THEN RAISE EXCEPTION 'Exact032g RD probe schema required'; END IF;
 IF current_setting('server_encoding')<>'UTF8'
 OR current_setting('server_version_num')::integer NOT BETWEEN 180000 AND 189999
 THEN RAISE EXCEPTION 'UTF8 PostgreSQL18 probe required'; END IF;"""
    ]
    # Superset closure retained from the reviewed candidate, no dynamic validator.
    for name in prior.SOURCES:
        sql = (
            candidate(name)
            if direction == "downgrade" and name in CHANGED
            else original(name)
        )
        checks.append(prior.function_assertion(sql))
    for column in prior.REQUIRED_COLUMNS["research_runs"]:
        checks.append(f"""IF NOT EXISTS(SELECT 1 FROM pg_attribute
 WHERE attrelid='@SCHEMA@.research_runs'::regclass AND attname='{column}'
 AND attnotnull AND NOT attisdropped)
 THEN RAISE EXCEPTION 'RD invariant NOT NULL differs: {column}'; END IF;""")
    # Earlier rejection is only committed-acceptance-equivalent when inputs
    # cannot be repaired later in the transaction. Pin the immutable trigger.
    checks.append("""IF NOT EXISTS(SELECT 1 FROM pg_trigger
 WHERE tgrelid='@SCHEMA@.research_runs'::regclass AND tgname='immutable_research_inputs'
 AND tgfoid='@SCHEMA@.protect_research_inputs()'::regprocedure
 AND tgenabled IN ('O','A') AND tgtype=19 AND NOT tgisinternal
 AND tgnargs=0 AND tgqual IS NULL AND tgattr::text='')
 THEN RAISE EXCEPTION 'RD input immutability trigger differs'; END IF;""")
    checks.append(immutable_function_assertion())
    if direction == "upgrade":
        checks.append(f"""IF EXISTS(SELECT 1 FROM pg_constraint
 WHERE conrelid='@SCHEMA@.research_runs'::regclass AND conname='{CONSTRAINT_NAME}')
 THEN RAISE EXCEPTION 'Fresh RD probe invariant already exists'; END IF;""")
    else:
        checks.append(f"""IF NOT EXISTS(SELECT 1 FROM pg_constraint
 WHERE conrelid='@SCHEMA@.research_runs'::regclass AND conname='{CONSTRAINT_NAME}'
 AND contype='c' AND convalidated AND NOT condeferrable)
 THEN RAISE EXCEPTION 'RD probe invariant missing'; END IF;""")
    return "DO $rd_probe$ BEGIN\n" + "\n".join(checks) + "\nEND $rd_probe$;"


# Frozen014 body. No import/execution of a historical migration is necessary.
IMMUTABLE_BODY = """
      BEGIN
        IF NEW.config IS DISTINCT FROM OLD.config OR NEW.owner_id IS DISTINCT FROM OLD.owner_id
          OR NEW.dataset_version_id IS DISTINCT FROM OLD.dataset_version_id OR NEW.strategy_version_id IS DISTINCT FROM OLD.strategy_version_id
          OR NEW.request_sha256 IS DISTINCT FROM OLD.request_sha256 OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
          OR (OLD.research_image_digest IS NOT NULL AND NEW.research_image_digest IS DISTINCT FROM OLD.research_image_digest)
          OR NEW.purpose IS DISTINCT FROM OLD.purpose OR NEW.engine IS DISTINCT FROM OLD.engine
        THEN RAISE EXCEPTION 'Run input provenance is immutable'; END IF;
        RETURN NEW;
      END """


def immutable_function_assertion():
    return f"""IF NOT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang
 WHERE p.oid='@SCHEMA@.protect_research_inputs()'::regprocedure
 AND p.prosrc=$rd_immutable${IMMUTABLE_BODY}$rd_immutable$
 AND p.prorettype='trigger'::regtype AND p.prokind='f' AND p.pronargs=0
 AND p.pronargdefaults=0 AND p.proargnames IS NULL AND p.proargmodes IS NULL
 AND p.provariadic=0 AND NOT p.proretset AND NOT p.prosecdef AND NOT p.proisstrict
 AND NOT p.proleakproof AND p.prosupport=0 AND p.proparallel='u'
 AND p.provolatile='v' AND l.lanname='plpgsql' AND p.proconfig IS NULL)
 THEN RAISE EXCEPTION 'RD input immutability body differs'; END IF;"""


def render_candidate_sql(direction="upgrade"):
    preflight = render_preflight_sql(direction)
    if direction == "upgrade":
        payload = f"ALTER TABLE @SCHEMA@.research_runs ADD CONSTRAINT {CONSTRAINT_NAME} CHECK ({EXPRESSION});\n"
        payload += "\n".join(candidate(n) for n in CHANGED)
    else:
        payload = "\n".join(original(n) for n in CHANGED)
        payload += (
            f"\nALTER TABLE @SCHEMA@.research_runs DROP CONSTRAINT {CONSTRAINT_NAME};"
        )
    return f"{prior.render_maintenance_sql()}\n{preflight}\n{payload}"
