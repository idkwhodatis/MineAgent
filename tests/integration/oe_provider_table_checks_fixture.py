"""Real PG three-CHECK projection matrix; not the full FK/deferred graph."""

import json
from uuid import uuid4

from backend.app import oe_provider_table_checks_sql as repair
from tests.openevolve.test_structured_provider_contracts import declared

SCHEMA = "oe032c_provider_check_probe"


def local(sql):
    # Tables are isolated projections; use the unchanged installed validator.
    return (
        sql.replace(
            "@SCHEMA@.mineagent_oes_iteration_contract",
            "mineagent_oes_iteration_contract",
        )
        .replace("@SCHEMA@.mineagent_rd_source_context", "mineagent_rd_source_context")
        .replace("@SCHEMA@.mineagent_oe_context", "mineagent_oe_context")
        .replace("@SCHEMA@", SCHEMA)
    )


def row_check(table, row, accepted):
    raw = json.dumps(row).replace("'", "''")
    return f"""DO $probe$ DECLARE rejected boolean:=false; BEGIN
 BEGIN INSERT INTO {SCHEMA}.{table} SELECT * FROM jsonb_populate_record(NULL::{SCHEMA}.{table},'{raw}'::jsonb);
 EXCEPTION WHEN check_violation THEN rejected:=true; END;
 IF rejected IS DISTINCT FROM {"false" if accepted else "true"} THEN
 RAISE EXCEPTION '032c physical CHECK expectation differs'; END IF;
 END $probe$; TRUNCATE {SCHEMA}.{table};"""


def documents():
    context = declared()[6].model_dump(mode="json")
    request = {
        "protocol": repair.PROTOCOL,
        "purpose": "chat",
        "ordinal": 0,
        "request_object_kind": "oe_structured_provider_request",
    }
    outcome = {
        "outcome": "completed",
        "response_object_id": str(uuid4()),
        "response_object_kind": "oe_structured_provider_response",
        "response_sha256": "a" * 64,
    }
    return context, request, outcome


def statements():
    context, request, outcome = documents()
    rows = [
        ("research_operation_contexts", {"context": context}),
        ("research_model_operations", request),
        ("research_model_outcomes", outcome),
    ]
    result = [f"CREATE SCHEMA {SCHEMA}"]
    result.extend(
        f"CREATE TABLE {SCHEMA}.{table} AS SELECT * FROM {table} WITH NO DATA"
        for table, _, _ in repair.TABLE_CHECKS
    )
    for table, name, old in repair.TABLE_CHECKS:
        result.append(
            local(f"ALTER TABLE @SCHEMA@.{table} ADD CONSTRAINT {name} CHECK ({old})")
        )
    result.extend(row_check(table, row, False) for table, row in rows)
    result.append(local(repair.render_upgrade_sql()))
    result.extend(row_check(table, row, True) for table, row in rows)
    for key, value in (
        ("schema_version", "unknown"),
        ("owner_id", None),
        ("native_iteration", "1"),
        ("private_extra", "not_allowed"),
    ):
        result.append(
            row_check(rows[0][0], {"context": {**context, key: value}}, False)
        )
    for key, value in (
        ("protocol", "unknown"),
        ("purpose", "embedding"),
        ("ordinal", 1),
        ("request_object_kind", "model_request"),
    ):
        result.append(row_check(rows[1][0], {**request, key: value}, False))
    for key, value in (
        ("response_object_id", None),
        ("response_sha256", None),
        ("response_sha256", "bad"),
        ("outcome", "unknown"),
        ("response_object_kind", "unknown"),
    ):
        result.append(row_check(rows[2][0], {**outcome, key: value}, False))
    for key in ("protocol", "purpose", "ordinal", "request_object_kind"):
        result.append(row_check(rows[1][0], {**request, key: None}, False))
    for value in (None, [], "not-an-object", 1):
        result.append(row_check(rows[0][0], {"context": value}, False))
    result.append(
        row_check(rows[2][0], {**outcome, "response_object_kind": None}, False)
    )
    result.append(
        row_check(
            rows[2][0],
            {
                **outcome,
                "response_object_kind": "model_response",
                "response_sha256": None,
            },
            False,
        )
    )
    from packages.openevolve_adapter.contracts import (
        SearchOperationContext as LegacyOEContext,
    )

    legacy_oe = LegacyOEContext(
        owner_id=uuid4(),
        run_id=uuid4(),
        epoch_id=uuid4(),
        iteration_intent_id=uuid4(),
        native_iteration=1,
        operation_ordinal=0,
        parent_candidate_id=uuid4(),
        parent_source_sha256="a" * 64,
        selection_snapshot_sha256="b" * 64,
        rendered_context_sha256="c" * 64,
    ).model_dump(mode="json")
    legacy_contexts = (
        {
            "schema_version": "mineagent.neutral-fixture-context.v1",
            "scenario": "chat",
            "ordinal": 0,
        },
        {
            "schema_version": "mineagent.rdagent-context.v1",
            "logical_step": 0,
            "task_index": 0,
            "input_sha256": "a" * 64,
            "spec_sha256": "b" * 64,
        },
        legacy_oe,
    )
    for legacy in legacy_contexts:
        result.append(row_check(rows[0][0], {"context": legacy}, True))
    # Existing legacy request/response and unknown-without-response stay valid.
    result.append(
        row_check(
            rows[1][0],
            {
                **request,
                "protocol": "mineagent.neutral-model.v1",
                "request_object_kind": "model_request",
            },
            True,
        )
    )
    result.append(
        row_check(
            rows[2][0], {**outcome, "response_object_kind": "model_response"}, True
        )
    )
    result.append(row_check(rows[2][0], {"outcome": "unknown"}, True))
    for table, row in rows:
        raw = json.dumps(row).replace("'", "''")
        result.append(
            f"INSERT INTO {SCHEMA}.{table} SELECT * FROM jsonb_populate_record(NULL::{SCHEMA}.{table},'{raw}'::jsonb)"
        )
        result.append(
            "DO $probe$ DECLARE refused boolean:=false; BEGIN BEGIN "
            + local(repair.render_downgrade_sql())
            + " EXCEPTION WHEN raise_exception THEN IF SQLERRM IS DISTINCT FROM 'Structured provider retained rows prevent downgrade' THEN RAISE; END IF; refused:=true; END; IF NOT refused THEN RAISE EXCEPTION 'Retained032c rows must prevent downgrade'; END IF; END $probe$;"
        )
        result.append(f"TRUNCATE {SCHEMA}.{table}")
    result.append(local(repair.render_downgrade_sql()))
    result.extend(row_check(table, row, False) for table, row in rows)
    result.append(local(repair.render_upgrade_sql()))
    result.extend(row_check(table, row, True) for table, row in rows)
    result.append("SELECT 'OE032C_PROVIDER_CHECK_MATRIX_COMPLETE'")
    return result
