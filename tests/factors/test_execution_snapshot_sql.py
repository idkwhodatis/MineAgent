"""Offline predicate/graph checks, not PostgreSQL execution evidence."""

from backend.app.execution_snapshot_sql import (
    EVALUATION_CLAIM_SQL,
    NORMALIZER_DOWNGRADE_SQL,
    NORMALIZER_SQL,
    SNAPSHOT_SQL,
    shared_claim_sql,
)


def test_normalizer_snapshot_binding_covers_actual_report_chain():
    for fragment in (
        "mineagent.numeric-process-extraction.v3",
        "MANU2-report-v1",
        "root_device",
        "root_inode",
        "mineagent-numeric-",
        "launch_sha256",
        "identity_sha256",
        "container_id",
        "deadline_bound_met",
        "observed_at",
        "primitive_report_sha256",
        "input_manifest_sha256",
        "listings_sha256",
        "normalizer_input_sha256",
        "conservative_unknown",
    ):
        assert fragment in NORMALIZER_SQL
    assert (
        "count(*) FROM @SCHEMA@.research_process_receipts WHERE admission_id=aid)<>4"
        in NORMALIZER_SQL
    )


def test_snapshot_closure_checks_whole_inventory_and_keeps_pending_charge():
    for fragment in (
        "expected_cutoffs",
        "factor_cutoff_executions",
        "part.ordinal<>n",
        "source_read_witness",
        "factor_execution_snapshot_objects",
        "Pending execution snapshot cannot settle",
        "Unresolved execution snapshot prevents",
        "research_process_states",
        "research_process_receipts",
        "research_budget_settlements",
    ):
        assert fragment in SNAPSHOT_SQL
    assert "feature_snapshots" not in SNAPSHOT_SQL
    assert "verified_source" not in SNAPSHOT_SQL
    broken = "(obj->>'sha256')||CASE WHEN obj->>'kind'='normalized_values' THEN '.parquet' ELSE '.json' END"
    fixed = "(obj->>'sha256')||(CASE WHEN obj->>'kind'='normalized_values' THEN '.parquet' ELSE '.json' END)"
    assert fixed in SNAPSHOT_SQL and broken not in SNAPSHOT_SQL
    assert _top_level_case_in_if(SNAPSHOT_SQL.replace(fixed, broken))
    assert not _top_level_case_in_if(SNAPSHOT_SQL)
    # Audit the complete frozen020–026 SQL literals in both directions, not only
    # the edited source. Ordinary CHECK/SELECT/assignment CASE remains untouched.
    import ast
    from pathlib import Path

    root = Path(__file__).parents[2] / "backend/alembic/versions"
    for path in sorted(root.glob("02*.py")):
        if path.name[:3] > "026":
            continue
        for node in ast.parse(path.read_text()).body:
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                assert not _top_level_case_in_if(node.value.value), (
                    path.name,
                    [getattr(t, "id", None) for t in node.targets],
                )


def test_storage_claim_extends_same_immediate_cross_family_registry():
    sql = shared_claim_sql()
    assert (
        "('research_object','factor_source','native_hdf','execution_snapshot')" in sql
    )
    assert "TG_TABLE_NAME='factor_execution_snapshot_publications'" in sql
    assert "UNION ALL SELECT 'execution_snapshot'" in sql
    assert "FOR UPDATE" in sql
    assert "INSERT INTO @SCHEMA@.engine_object_reservation_consumers" in sql
    assert "CREATE TRIGGER claim_execution_snapshot_reservation" in sql
    assert "CREATE TRIGGER claim_native_reservation" not in sql


def test_evaluation_claim_is_cross_family_not_parallel_uniqueness():
    assert "FOR UPDATE" in EVALUATION_CLAIM_SQL
    assert "factor_evaluation_result_consumers" in EVALUATION_CLAIM_SQL
    assert "UNION ALL" in EVALUATION_CLAIM_SQL
    assert "factor_evaluation_outcomes" in EVALUATION_CLAIM_SQL
    assert "factor_execution_evaluation_outcomes" in EVALUATION_CLAIM_SQL


def test_normalizer_predicate_rejects_permissive_cast_grammar_before_hashes():
    for fragment in (
        "mineagent.numeric-process-intent.v2",
        "not closed typed UTC records",
        "interval '10 seconds'",
        "jsonb_typeof(ident.payload->k) IS DISTINCT FROM 'number'",
        "jsonb_typeof(ex.payload->k) IS DISTINCT FROM 'number'",
        "jsonb_typeof(ident.payload->'boot_id') IS DISTINCT FROM 'string'",
        "NOT st.payload ?& ARRAY",
        "NOT ident.payload ?& ARRAY",
        "NOT li.payload ?& ARRAY",
    ):
        assert fragment in NORMALIZER_SQL
    assert NORMALIZER_SQL.index("not closed typed UTC records") < NORMALIZER_SQL.index(
        "li.payload_sha256<>claim.intent_sha256"
    )


def test_downgrade_classifies_all_normalizer_intents_before_restoring_v1():
    assert "research_process_admissions a" in NORMALIZER_DOWNGRADE_SQL
    assert "LEFT JOIN @SCHEMA@.factor_cutoff_executions" in NORMALIZER_DOWNGRADE_SQL
    assert "factor_native_normalizer_inputs" not in NORMALIZER_DOWNGRADE_SQL
    assert "INTO STRICT hdf" in NORMALIZER_DOWNGRADE_SQL
    assert "mineagent.factor-normalization.v1" in NORMALIZER_DOWNGRADE_SQL
    assert "IS DISTINCT FROM expected" in NORMALIZER_DOWNGRADE_SQL
    assert (
        "jsonb_typeof(hdf->'size_bytes') IS DISTINCT FROM 'number'"
        in NORMALIZER_DOWNGRADE_SQL
    )


def _top_level_case_in_if(sql):
    """Lexical delimiter regression only, not a substitute for the hosted PG gate."""
    import re

    # Ignore SQL quoted strings/identifiers and comments, retaining function-body
    # tokens. PL/pgSQL reads an IF expression until THEN at parenthesis depth0;
    # an ungrouped SQL CASE's own THEN prematurely terminates that expression.
    tokens = re.findall(
        r"--[^\n]*|/\*[\s\S]*?\*/|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|[A-Za-z_][A-Za-z_0-9]*|[();]",
        sql,
    )
    active, depth, previous = False, 0, None
    failures = []
    for ordinal, token in enumerate(tokens):
        if token.startswith(("'", '"', "--", "/*")):
            continue
        token = token.upper()
        if token in ("IF", "ELSIF") and previous != "END":
            active, depth = True, 0
        elif active:
            if token == "(":
                depth += 1
            elif token == ")":
                depth -= 1
            elif token == "CASE" and depth == 0:
                failures.append(ordinal)
            elif token == ";" or (token == "THEN" and depth == 0):
                active = False
        previous = token
    return failures
