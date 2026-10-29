"""Actual029/030 optional-selector grammar and persisted root declarations.

No child/provider/process is fabricated; positive roots retain exact canonical
bytes and deferred constraints, while invalid selectors fail before admission.
"""

import json
from copy import deepcopy

from sqlalchemy import insert
from sqlalchemy.dialects.postgresql import dialect

from packages.factors.contracts import canonical_bytes, digest
from packages.research_workflow.admission import CreateWorkflow
from packages.research_workflow.plan import RDGuidancePlan
from tests.integration.workflow_declaration_sql_fixture import declaration_rows

V3 = "mineagent.rdagent-orchestrator.v3"


def variant_rows(index, selector="missing"):
    rows = deepcopy(declaration_rows(index))
    root = next(values for table, values in rows if table.name == "research_workflows")
    plan = json.loads(root["plan_canonical"])
    if selector != "missing":
        plan["research_template"]["controller_profile"] = selector
    if selector == V3:
        config = plan["research_template"]
        config["budget"]["wall_milliseconds"] = (
            300000 + 32 * config["model_binding"]["request_deadline_ms"]
        )
        plan["budget_ceiling"]["wall_milliseconds"] = (
            config["budget"]["wall_milliseconds"] * plan["maximum_iterations"]
        )
    request = json.loads(root["creation_canonical"])
    request["plan"] = plan
    root["plan_canonical"] = canonical_bytes(plan).decode()
    root["plan_sha256"] = digest(plan)
    root["budget_canonical"] = canonical_bytes(plan["budget_ceiling"]).decode()
    root["budget_sha256"] = digest(plan["budget_ceiling"])
    root["creation_canonical"] = canonical_bytes(request).decode()
    root["creation_sha256"] = digest(request)
    response = json.loads(root["creation_result_canonical"])
    response["request_sha256"] = digest(request)
    root["creation_result_canonical"] = canonical_bytes(response).decode()
    return rows, plan, request


def literal(value):
    return "'" + canonical_bytes(value).decode().replace("'", "''") + "'::jsonb"


def statements():
    cases = []
    for selector in (
        "missing",
        V3,
        None,
        False,
        60,
        {},
        "mineagent.rdagent-orchestrator.v2",
        "unknown",
    ):
        _, plan, request = variant_rows(90, selector)
        for kind, model, value in (
            ("plan", RDGuidancePlan, plan),
            ("create", CreateWorkflow, request),
        ):
            try:
                model.model_validate_json(canonical_bytes(value))
                expected = True
            except ValueError:
                expected = False
            cases.append(
                f"DO $$ BEGIN IF mineagent_workflow_payload('{kind}',{literal(value)}) IS DISTINCT FROM {'true' if expected else 'false'} THEN RAISE EXCEPTION 'Controller selector parity {len(cases)}'; END IF; END $$;"
            )
    _, plan, _ = variant_rows(90, V3)
    for amount in (0, 300000, 555999, 556000):
        value = deepcopy(plan)
        value["research_template"]["budget"]["wall_milliseconds"] = amount
        try:
            RDGuidancePlan.model_validate_json(canonical_bytes(value))
            expected = True
        except ValueError:
            expected = False
        cases.append(
            f"DO $$ BEGIN IF mineagent_workflow_payload('plan',{literal(value)}) IS DISTINCT FROM {'true' if expected else 'false'} THEN RAISE EXCEPTION 'Controller budget parity {len(cases)}'; END IF; END $$;"
        )
    for index, selector in ((91, "missing"), (92, V3)):
        rows, plan, request = variant_rows(index, selector)
        typed = RDGuidancePlan.model_validate_json(canonical_bytes(plan))
        assert canonical_bytes(typed) == canonical_bytes(plan)
        assert canonical_bytes(
            CreateWorkflow.model_validate_json(canonical_bytes(request))
        ) == canonical_bytes(request)
        cases.append("SET CONSTRAINTS ALL DEFERRED;")
        for table, values in rows:
            cases.append(
                str(
                    insert(table)
                    .values(**values)
                    .compile(dialect=dialect(), compile_kwargs={"literal_binds": True})
                )
                + ";"
            )
        cases.append("SET CONSTRAINTS ALL IMMEDIATE;")
        root = next(
            values for table, values in rows if table.name == "research_workflows"
        )
        raw = root["plan_canonical"].replace("'", "''")
        cases.append(
            f"DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM research_workflows WHERE id='{root['id']}'::uuid AND plan_canonical='{raw}' AND plan_sha256='{root['plan_sha256']}' AND mineagent_workflow_consistent(owner_id,id) IS TRUE) THEN RAISE EXCEPTION 'Controller retained bytes/root closure changed'; END IF; END $$;"
        )
    cases.append("SELECT 'WORKFLOW_CONTROLLER_SELECTOR_COMPLETE';")
    return cases
