"""Installed030 schema scalar bounds vs the actual strict Python field types.

These tests do not replace cross-field, lifecycle or physical execution guards.
"""

import json
from typing import Annotated

from pydantic import TypeAdapter, ValidationError

from backend.app.oe_structured_submission_schemas import StructuredSeedParameters
from packages.openevolve_structured.objective import (
    StructuredMeasurement,
    StructuredObjective,
)
from packages.research_workflow.rd_claim import WorkflowRDClaim
from packages.structured_factor_strategy.contracts import StructuredProgram


def literal(value):
    return "'" + json.dumps(value, allow_nan=False).replace("'", "''") + "'::jsonb"


def numeric_statements():
    rules = []
    for kind in ("request", "candidate", "measurement", "config"):
        rules.append(
            f"SELECT '{kind}'::text kind, replace((regexp_match(pg_get_functiondef('mineagent_oes_contract_v030_shape(text,jsonb)'::regprocedure), $pattern$WHEN '{kind}' THEN rule := '(.*?)'::jsonb;$pattern$))[1], '''''', '''')::jsonb AS rule"
        )
    rules.append(
        "SELECT 'bootstrap',replace((regexp_match(pg_get_functiondef('mineagent_oes_epoch_check(uuid)'::regprocedure), $pattern$rule jsonb:='(.*?)'::jsonb;$pattern$))[1], '''''', '''')::jsonb"
    )
    rules.append(
        "SELECT 'claim',replace((regexp_match(pg_get_functiondef(coalesce(to_regprocedure('mineagent_workflow_payload_before_oe(text,jsonb)'), 'mineagent_workflow_payload(text,jsonb)'::regprocedure)), $pattern$WHEN 'claim' THEN rule := '(.*?)'::jsonb;$pattern$))[1], '''''', '''')::jsonb"
    )
    statements = [
        "CREATE TEMP TABLE upload030_rules ON COMMIT DROP AS "
        + " UNION ALL ".join(rules)
        + ";",
        "DO $$ BEGIN IF EXISTS(SELECT 1 FROM upload030_rules WHERE rule IS NULL) OR (SELECT count(*) FROM upload030_rules)<>6 THEN RAISE EXCEPTION 'Installed rules absent'; END IF; END $$;",
    ]
    bindings = [
        ("request", StructuredSeedParameters),
        ("request", StructuredObjective),
        ("candidate", StructuredProgram),
        ("config", StructuredProgram),
        ("config", StructuredObjective),
        ("bootstrap", StructuredProgram),
        ("bootstrap", StructuredObjective),
        ("measurement", StructuredMeasurement),
        ("claim", WorkflowRDClaim),
    ]
    fields = {
        "StructuredSeedParameters": (
            "threshold",
            "maximum_weight",
            "maximum_gross_weight",
        ),
        "StructuredObjective": ("initial_cash",),
        "StructuredProgram": ("threshold", "maximum_weight", "maximum_gross_weight"),
        "StructuredMeasurement": (
            "total_return_pct",
            "max_drawdown_pct",
            "combined_score",
        ),
        "WorkflowRDClaim": ("expected_revision",),
    }
    values = [
        -1000001,
        -1,
        0,
        0.000001,
        0.5,
        1,
        1.000001,
        999999,
        1000000,
        1000001,
        1000000000,
        1000000001,
        True,
        False,
        "0.5",
        None,
    ]
    for kind, model in bindings:
        for field in fields[model.__name__]:
            info = model.model_fields[field]
            annotation = (
                Annotated[info.annotation, *info.metadata]
                if info.metadata
                else info.annotation
            )
            adapter = TypeAdapter(annotation, config=model.model_config)
            path = (
                ["properties", field]
                if kind in {"measurement", "claim"}
                else ["$defs", model.__name__, "properties", field]
            )
            path_sql = "ARRAY[" + ",".join("'" + p + "'" for p in path) + "]"
            for value in values:
                try:
                    adapter.validate_json(json.dumps(value))
                    expected = True
                except ValidationError:
                    expected = False
                statements.append(
                    f"DO $$ DECLARE rule jsonb; BEGIN SELECT r.rule INTO STRICT rule FROM upload030_rules r WHERE kind='{kind}'; IF mineagent_rd_schema({literal(value)},rule#>{path_sql},coalesce(rule->'$defs','{{}}'::jsonb),0) IS DISTINCT FROM {str(expected).lower()} THEN RAISE EXCEPTION 'Numeric field mismatch {kind} {field} {json.dumps(value)}'; END IF; END $$;"
                )
    statements.append("SELECT 'DECLARED_UPLOAD_NUMERIC_MATRIX_COMPLETE';")
    return statements
