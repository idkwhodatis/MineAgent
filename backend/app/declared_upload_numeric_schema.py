"""030 correction of numeric bounds accidentally rounded by the old RD renderer.

Historical028 literals remain unchanged. New predicates use the existing025
number-aware grammar; only schema bounds change, never budgets or runtime caps.
"""

import json
import re

from backend.app.oe_structured_provider_submission import (
    ProviderStructuredSearchRequest,
)
from backend.app.structured_process_ddl import _require_serialized_keys
from backend.app.structured_schema_sql import structured_schema
from packages.openevolve_structured.candidate import CandidateDocument
from packages.openevolve_structured.objective import StructuredMeasurement
from packages.openevolve_structured.provider_bootstrap import (
    ProviderStructuredBootstrap,
)
from packages.openevolve_structured.provider_run import ProviderStructuredRunConfig

MODELS = {
    "request": ProviderStructuredSearchRequest,
    "candidate": CandidateDocument,
    "measurement": StructuredMeasurement,
    "config": ProviderStructuredRunConfig,
}


def corrected_rule(model, *, legacy_input=False):
    rule = structured_schema(model)
    if legacy_input:
        props = rule["$defs"]["FixedExecutionInputs"]["properties"]
        for field, old, new in [
            (
                "schema_version",
                "mineagent.oe-structured-retained-inputs.v1",
                "mineagent.oe-structured-retained-inputs.v2",
            ),
            (
                "market_qualification",
                "synthetic_by_construction",
                "user_declaration_unverified",
            ),
        ]:
            if props[field].get("enum") != [old, new]:
                raise ValueError("Exact retained input pair required")
            del props[field]["enum"]
            props[field]["const"] = old
    _require_serialized_keys(rule)
    return rule


def only_numeric_bounds(old, new, path=()):
    """Fail closed if current models introduce any unrelated contract drift."""
    if old == new:
        return []
    if isinstance(old, dict) and isinstance(new, dict):
        if old.get("type") in {"number", "integer"} and new.get("type") == old["type"]:
            keys = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"}
            if {k: v for k, v in old.items() if k not in keys} == {
                k: v for k, v in new.items() if k not in keys
            }:
                return [
                    (
                        path,
                        {k: v for k, v in old.items() if k in keys},
                        {k: v for k, v in new.items() if k in keys},
                    )
                ]
        if old.keys() != new.keys():
            raise ValueError("Unexpected030 schema shape change: " + ".".join(path))
        return [
            change
            for key in old
            for change in only_numeric_bounds(old[key], new[key], path + (key,))
        ]
    raise ValueError("Unexpected030 nonnumeric schema change: " + ".".join(path))


def require_reviewed_float_changes(changes, expected):
    observed = set()
    for path, prior, current in changes:
        if len(path) != 4 or path[0] != "$defs" or path[2] != "properties":
            raise ValueError("Unreviewed numeric correction path")
        key = (path[1], path[3])
        maximum = 1000000000.0 if key == ("StructuredObjective", "initial_cash") else 1
        if prior != {"minimum": 1, "maximum": maximum} or current != {
            "exclusiveMinimum": 0,
            "maximum": maximum,
        }:
            raise ValueError("Unreviewed numeric correction bounds")
        observed.add(key)
    if observed != set(expected) or len(changes) != len(expected):
        raise ValueError("Exact reviewed numeric correction inventory required")


def contract_sql(old):
    result = old.replace(
        "mineagent_oes_contract(", "mineagent_oes_contract_v030_shape(", 1
    )
    changes = []
    for kind, model in MODELS.items():
        pattern = "WHEN '" + kind + "' THEN rule := '(.*?)'::jsonb;"
        found = re.findall(pattern, result)
        if len(found) != 1:
            raise ValueError("Unique pinned contract case required")
        prior = json.loads(found[0].replace("''", "'"))
        current = corrected_rule(model, legacy_input=kind == "config")
        delta = only_numeric_bounds(prior, current)
        program = (
            "StructuredSeedParameters" if kind == "request" else "StructuredProgram"
        )
        expected = (
            []
            if kind == "measurement"
            else [(program, "maximum_weight"), (program, "maximum_gross_weight")]
        )
        if kind in {"request", "config"}:
            expected.append(("StructuredObjective", "initial_cash"))
        require_reviewed_float_changes(delta, expected)
        changes.extend((kind, *entry) for entry in delta)
        raw = json.dumps(current, sort_keys=True, separators=(",", ":")).replace(
            "'", "''"
        )
        result = result.replace(found[0], raw, 1)
    if not changes:
        raise ValueError("Expected reviewed numeric correction absent")
    return result, changes


def epoch_sql(old):
    pattern = r"rule jsonb:='(.*?)'::jsonb;"
    found = re.findall(pattern, old)
    if len(found) != 1:
        raise ValueError("Unique pinned bootstrap schema required")
    prior = json.loads(found[0].replace("''", "'"))
    current = corrected_rule(ProviderStructuredBootstrap)
    changes = only_numeric_bounds(prior, current)
    require_reviewed_float_changes(
        changes,
        [
            ("StructuredObjective", "initial_cash"),
            ("StructuredProgram", "maximum_weight"),
            ("StructuredProgram", "maximum_gross_weight"),
        ],
    )
    raw = json.dumps(current, sort_keys=True, separators=(",", ":")).replace("'", "''")
    return old.replace(found[0], raw, 1), changes


def workflow_claim_sql(old):
    from packages.research_workflow.rd_claim import WorkflowRDClaim

    found = re.findall("WHEN 'claim' THEN rule := '(.*?)'::jsonb;", old)
    if len(found) != 1:
        raise ValueError("Unique pinned workflow claim schema required")
    prior = json.loads(found[0].replace("''", "'"))
    current = corrected_rule(WorkflowRDClaim)
    changes = only_numeric_bounds(prior, current)
    if changes != [
        (
            ("properties", "expected_revision"),
            {"minimum": 1, "exclusiveMaximum": 1000000},
            {"minimum": 1, "maximum": 999999},
        )
    ]:
        raise ValueError("Exact workflow revision numeric correction required")
    raw = json.dumps(current, sort_keys=True, separators=(",", ":")).replace("'", "''")
    return old.replace(found[0], raw, 1), changes
