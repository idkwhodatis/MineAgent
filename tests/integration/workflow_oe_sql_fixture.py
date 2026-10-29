"""031 strict Python/actual PostgreSQL contract parity, not physical proof."""

from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

from backend.app.research_workflow_structured_schemas import CreateStructuredWorkflow
from packages.factors.contracts import canonical_bytes, digest
from packages.openevolve_structured.candidate import CandidateDocument
from packages.openevolve_structured.workflow_prompt import SYSTEM, _block
from packages.openevolve_structured.workflow_run import WorkflowStructuredRunConfig
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_workflow.structured_plan import RDStructuredGuidancePlan
from tests.openevolve.test_workflow_run import config as config_raw
from tests.research_workflow.test_plan import plan


def combined_plan():
    old = plan()
    raw = old.model_dump(mode="json")
    budget = BudgetVector(calls=2, process_slots=4, wall_milliseconds=600000)
    raw.update(
        schema_version="mineagent.rd-structured-guidance-workflow-plan.v1",
        profile="rd_guidance_then_structured_oe.v1",
        budget_ceiling=old.budget_ceiling.add(budget).model_dump(mode="json"),
    )
    end = old.research_template.input.window.start + timedelta(days=1)
    raw["research_template"]["input"]["window"]["end"] = str(end)
    raw["objective"]["window"]["end"] = str(end)
    oe = WorkflowStructuredRunConfig.model_validate_json(canonical_bytes(config_raw()))
    raw["structured_template"] = {
        "seed_parameters": {
            "threshold": 0.0,
            "top_k": 1,
            "maximum_weight": 0.5,
            "maximum_gross_weight": 1.0,
        },
        "objective": {
            "window": {
                "start": str(old.research_template.input.window.start),
                "end": str(end + timedelta(days=1)),
            },
            "initial_cash": 10000.0,
        },
        "model_binding": oe.model_binding.model_dump(mode="json"),
        "runtime": oe.runtime.model_dump(mode="json"),
        "budget": budget.model_dump(mode="json"),
        "maximum_mutation_slots": 1,
    }
    raw["structured_template"]["model_binding"]["pricing"] = {
        "status": "operator_declared",
        "input_microusd_per_million": 0,
        "output_microusd_per_million": 0,
    }
    return RDStructuredGuidancePlan.model_validate_json(canonical_bytes(raw))


def literal(value):
    return "'" + canonical_bytes(value).decode().replace("'", "''") + "'::jsonb"


def statements():
    cases = []
    config = WorkflowStructuredRunConfig.model_validate_json(
        canonical_bytes(config_raw())
    )
    p = combined_plan()
    for kind, model, base, predicate in (
        (
            "config",
            WorkflowStructuredRunConfig,
            config.model_dump(mode="json"),
            "mineagent_oes_contract",
        ),
        (
            "plan",
            RDStructuredGuidancePlan,
            p.model_dump(mode="json"),
            "mineagent_workflow_payload",
        ),
        (
            "create",
            CreateStructuredWorkflow,
            CreateStructuredWorkflow(idempotency_key="sql-new-plan", plan=p).model_dump(
                mode="json"
            ),
            "mineagent_workflow_payload",
        ),
    ):
        variants = [base]
        for field in ("schema_version", "unknown"):
            for value in (None, False, 7, "unknown"):
                v = deepcopy(base)
                v[field] = value
                variants.append(v)
        if kind == "config":
            for field in (
                "child_run_id",
                "snapshot_id",
                "factor_version_id",
                "release_exposure_id",
                "definition_sha256",
            ):
                v = deepcopy(base)
                v["workflow_context"][field] = (
                    "f" * 64 if field.endswith("sha256") else str(uuid4())
                )
                variants.append(v)
            for value in (0, 0.5, 1, 1.01, False, None):
                v = deepcopy(base)
                v["search"]["domain"]["seed_program"]["maximum_weight"] = value
                try:
                    candidate = CandidateDocument.model_validate_json(
                        canonical_bytes(
                            {"program": v["search"]["domain"]["seed_program"]}
                        )
                    )
                    v["search"]["seed_candidate_sha256"] = digest(candidate)
                except ValueError:
                    pass
                variants.append(v)
        elif kind == "plan":
            for value in (0, 0.5, 1, 1.01, False, None):
                v = deepcopy(base)
                v["structured_template"]["seed_parameters"]["maximum_weight"] = value
                variants.append(v)
            for value in (0, 1, 2, 3):
                v = deepcopy(base)
                v["structured_template"]["maximum_mutation_slots"] = value
                variants.append(v)
        if kind in ("plan", "create"):
            for days in (0, 1, 2, 3):
                v = deepcopy(base)
                target = v if kind == "plan" else v["plan"]
                start = p.research_template.input.window.start
                end = start + timedelta(days=days)
                target["research_template"]["input"]["window"]["end"] = str(end)
                target["objective"]["window"]["end"] = str(end)
                target["structured_template"]["objective"]["window"]["end"] = str(
                    end + timedelta(days=1)
                )
                variants.append(v)
        for v in variants:
            try:
                model.model_validate_json(canonical_bytes(v))
                expected = True
            except ValueError:
                expected = False
            cases.append(
                f"DO $$ BEGIN IF {predicate}('{kind}',{literal(v)}) IS DISTINCT FROM {'true' if expected else 'false'} THEN RAISE EXCEPTION '031 contract parity case {len(cases)}'; END IF; END $$;"
            )
    ctx = config.workflow_context
    valid = {"system": SYSTEM, "user": "Actual native task" + _block(ctx)}
    prompts = [(valid, True)]
    for key in ("system", "user"):
        for value in ("", "untrusted override", None):
            changed = dict(valid)
            changed[key] = value
            prompts.append((changed, False))
    changed = dict(valid)
    changed["user"] += "trailing"
    prompts.append((changed, False))
    changed = dict(valid)
    changed["user"] += _block(ctx)
    prompts.append((changed, False))
    for prompt, expected in prompts:
        cases.append(
            f"DO $$ BEGIN IF mineagent_oe_workflow_rendered({literal(ctx)},{literal(prompt)}) IS DISTINCT FROM {'true' if expected else 'false'} THEN RAISE EXCEPTION '031 actual prompt case {len(cases)}'; END IF; END $$;"
        )
    cases.append("SELECT 'OE031_CONTRACT_MATRIX_COMPLETE';")
    return cases


def declaration_statements():
    """Actual deferred root metadata only; no fabricated completed child graph."""
    import json

    from sqlalchemy import insert
    from sqlalchemy.dialects.postgresql import dialect

    from backend.app.research_workflow_structured_schemas import (
        CreateStructuredWorkflow,
    )
    from tests.integration.workflow_declaration_sql_fixture import declaration_rows

    rows = deepcopy(declaration_rows(200))
    root = next(values for table, values in rows if table.name == "research_workflows")
    old_plan = json.loads(root["plan_canonical"])
    plan = combined_plan().model_dump(mode="json")
    plan["owner_id"] = old_plan["owner_id"]
    config = plan["research_template"]
    config["steer_snapshot"]["owner_id"] = old_plan["owner_id"]
    config["steer_snapshot_sha256"] = digest(config["steer_snapshot"])
    config["budget"] = old_plan["research_template"]["budget"]
    plan["budget_ceiling"] = (
        BudgetVector.model_validate(old_plan["budget_ceiling"])
        .add(BudgetVector.model_validate(plan["structured_template"]["budget"]))
        .model_dump(mode="json")
    )
    typed = RDStructuredGuidancePlan.model_validate_json(canonical_bytes(plan))
    request = json.loads(root["creation_canonical"])
    request["schema_version"] = "mineagent.workflow-create.v2"
    request["plan"] = typed.model_dump(mode="json")
    checked = CreateStructuredWorkflow.model_validate_json(canonical_bytes(request))
    root.update(
        plan_canonical=canonical_bytes(typed).decode(),
        plan_sha256=typed.sha256,
        budget_canonical=canonical_bytes(typed.budget_ceiling).decode(),
        budget_sha256=digest(typed.budget_ceiling),
        creation_canonical=canonical_bytes(checked).decode(),
        creation_sha256=digest(checked),
    )
    result = json.loads(root["creation_result_canonical"])
    result["request_sha256"] = digest(checked)
    root["creation_result_canonical"] = canonical_bytes(result).decode()
    sql = ["SET CONSTRAINTS ALL DEFERRED;"]
    for table, values in rows:
        sql.append(
            str(
                insert(table)
                .values(**values)
                .compile(dialect=dialect(), compile_kwargs={"literal_binds": True})
            )
            + ";"
        )
    sql.append("SET CONSTRAINTS ALL IMMEDIATE;")
    sql.append(
        f"DO $$ BEGIN IF mineagent_workflow_consistent('{root['owner_id']}'::uuid,'{root['id']}'::uuid) IS NOT TRUE THEN RAISE EXCEPTION 'New finite plan declaration failed'; END IF; END $$;"
    )
    sql.append("SELECT 'OE031_DECLARATION_COMPLETE';")
    return sql


def orphan_claim_statements():
    """Real legacy-root relations cannot accept a detached new OE claim."""
    from sqlalchemy import insert
    from sqlalchemy.dialects.postgresql import dialect

    from backend.app.research_workflow_models import ResearchWorkflowCommand
    from packages.research_workflow.oe_stage import (
        WorkflowOEClaim,
        WorkflowOEClaimReceipt,
    )
    from tests.integration.workflow_declaration_sql_fixture import NOW, declaration_rows

    rows = declaration_rows(201)
    root = next(values for table, values in rows if table.name == "research_workflows")
    command = WorkflowOEClaim(
        workflow_id=root["id"],
        expected_revision=1,
        input_revision_id=root["current_input_revision_id"],
        idempotency_key="orphan-oe-claim",
    )
    command_id = uuid4()
    receipt = WorkflowOEClaimReceipt(
        workflow_id=root["id"],
        command_id=command_id,
        command_sha256=digest(command),
        stage_id=uuid4(),
        child_run_id=uuid4(),
        submission_id=uuid4(),
        revision=2,
    )
    values = {
        "id": command_id,
        "owner_id": root["owner_id"],
        "workflow_id": root["id"],
        "idempotency_key": command.idempotency_key,
        "command_canonical": canonical_bytes(command).decode(),
        "command_sha256": digest(command),
        "result_canonical": canonical_bytes(receipt).decode(),
        "created_at": NOW,
    }

    def statement(table, values):
        return (
            str(
                insert(table)
                .values(**values)
                .compile(dialect=dialect(), compile_kwargs={"literal_binds": True})
            )
            + ";"
        )

    sql = [
        "SET CONSTRAINTS ALL DEFERRED;",
        *[statement(table, values) for table, values in rows],
        "SET CONSTRAINTS ALL IMMEDIATE;",
        "SET CONSTRAINTS ALL DEFERRED;",
    ]
    sql.append(f"""DO $test$ BEGIN
 BEGIN
  UPDATE research_workflows SET revision=2 WHERE id='{root["id"]}'::uuid;
  {statement(ResearchWorkflowCommand.__table__, values)}
  IF mineagent_workflow_consistent('{root["owner_id"]}'::uuid,'{root["id"]}'::uuid) IS NOT FALSE THEN RAISE EXCEPTION 'Detached OE claim was accepted'; END IF;
  BEGIN
   SET CONSTRAINTS ALL IMMEDIATE;
  EXCEPTION WHEN raise_exception THEN
   IF SQLERRM <> 'Workflow immutable finite graph differs' THEN RAISE; END IF;
   RAISE EXCEPTION 'EXPECTED_ORPHAN_OE_CLAIM_REJECTION';
  END;
  RAISE EXCEPTION 'Detached OE claim committed';
 EXCEPTION WHEN raise_exception THEN
  IF SQLERRM <> 'EXPECTED_ORPHAN_OE_CLAIM_REJECTION' THEN RAISE; END IF;
 END;
 IF mineagent_workflow_consistent('{root["owner_id"]}'::uuid,'{root["id"]}'::uuid) IS NOT TRUE THEN RAISE EXCEPTION 'Rejected claim damaged legacy root'; END IF;
END $test$;""")
    sql.append("SET CONSTRAINTS ALL IMMEDIATE;")
    sql.append("SELECT 'OE031_ORPHAN_CLAIM_REJECTED';")
    return sql
