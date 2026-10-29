"""Typed declaration-only SQL fixture for the installed028 PostgreSQL probe.

No Session double, run, stage, model operation, process, output or release is
created. This proves only the real deferred metadata constraints on a manual
selection and a finite initial workflow declaration. Run inside a rollback-only
transaction after actual028 installation, never on an application database.
"""

from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from sqlalchemy import insert
from sqlalchemy.dialects.postgresql import dialect

from backend.app.models import User
from backend.app.rdagent_provider_preflight import provider_budget_floor
from backend.app.rdagent_steer_models import RDSteer, RDSteerVersion
from backend.app.research_workflow_models import (
    ResearchWorkflow,
    ResearchWorkflowRevision,
)
from packages.factors.contracts import canonical_bytes, digest
from packages.rdagent_adapter.steer import SteerVersion
from packages.research_inputs.contracts import InputVersionRef
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_workflow.admission import CreateWorkflow, WorkflowCreated
from packages.research_workflow.contracts import WorkflowInputSnapshot
from packages.research_workflow.plan import RDGuidancePlan
from tests.research_workflow.test_plan import plan

NOW = datetime(2026, 10, 27, tzinfo=UTC)


def declaration_rows(index=0, *, mutation=None):
    def uid(offset):
        return UUID(int=280000 + index * 100 + offset)

    owner, steer_id, version_id, workflow_id, revision_id = map(uid, range(1, 6))
    text = "Retain this manual hypothesis as untrusted research guidance."
    version = SteerVersion(
        steer_id=steer_id,
        version_id=version_id,
        ordinal=1,
        kind="hypothesis",
        text=text,
        text_sha256=sha256(text.encode()).hexdigest(),
    )
    ref = InputVersionRef(
        origin="manual_user_submission",
        record_id=steer_id,
        version_id=version_id,
        content_sha256=version.sha256,
    )
    raw = plan().model_dump(mode="json")
    raw["owner_id"] = str(owner)
    template = raw["research_template"]
    template["steer_snapshot"]["owner_id"] = str(owner)
    template["steer_snapshot_sha256"] = digest(template["steer_snapshot"])
    from packages.rdagent_provider.contracts import RDModelBinding

    binding = RDModelBinding.model_validate_json(
        canonical_bytes(template["model_binding"])
    )
    floor = provider_budget_floor(binding)
    template["budget"] = floor.model_dump(mode="json")
    cap = BudgetVector(**{k: getattr(floor, k) * 2 for k in BudgetVector.model_fields})
    raw["budget_ceiling"] = cap.add(BudgetVector(storage_bytes=65536)).model_dump(
        mode="json"
    )
    typed_plan = RDGuidancePlan.model_validate_json(canonical_bytes(raw))
    if mutation == "foreign_selection":
        ref = ref.model_copy(update={"version_id": UUID(int=280003)})
    elif mutation == "selection_hash":
        ref = ref.model_copy(update={"content_sha256": "f" * 64})
    request = CreateWorkflow(
        idempotency_key=f"sql-declaration-{index}", plan=typed_plan, refs=(ref,)
    )
    selection = WorkflowInputSnapshot(
        owner_id=owner, workflow_id=workflow_id, revision_id=revision_id, refs=(ref,)
    )
    created = WorkflowCreated(
        workflow_id=workflow_id,
        input_revision_id=revision_id,
        request_sha256=digest(request),
        status="blocked",
        current_stage_id=None,
    )
    root = {
        "id": workflow_id,
        "owner_id": owner,
        "revision": 1,
        "creation_key": request.idempotency_key,
        "creation_canonical": canonical_bytes(request).decode(),
        "creation_sha256": digest(request),
        "creation_result_canonical": canonical_bytes(created).decode(),
        "plan_canonical": canonical_bytes(typed_plan).decode(),
        "plan_sha256": typed_plan.sha256,
        "budget_canonical": canonical_bytes(typed_plan.budget_ceiling).decode(),
        "budget_sha256": digest(typed_plan.budget_ceiling),
        "mode": typed_plan.mode,
        "status": "blocked",
        "blocked_reason": "workflow_child_adapter_unavailable",
        "current_input_revision_id": revision_id,
        "current_stage_id": None,
        "latest_checkpoint_id": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    if mutation == "plan_hash":
        root["plan_sha256"] = "f" * 64
    if mutation not in {None, "plan_hash", "selection_hash", "foreign_selection"}:
        raise ValueError("Closed negative fixture inventory")
    return (
        (
            User.__table__,
            {
                "id": owner,
                "email": f"workflow-sql-{index}@example.invalid",
                "hashed_password": "not-an-authentication-fixture",
                "full_name": "SQL declaration probe",
                "is_active": False,
                "is_superuser": False,
                "created_at": NOW,
            },
        ),
        (
            RDSteer.__table__,
            {
                "id": steer_id,
                "owner_id": owner,
                "current_version_id": version_id,
                "disabled": False,
                "revision": 1,
                "created_at": NOW,
                "updated_at": NOW,
            },
        ),
        (
            RDSteerVersion.__table__,
            {
                "id": version_id,
                "owner_id": owner,
                "steer_id": steer_id,
                "ordinal": 1,
                "kind": version.kind,
                "text": version.text,
                "text_sha256": version.text_sha256,
                "content_canonical": canonical_bytes(version).decode(),
                "content_sha256": version.sha256,
                "created_at": NOW,
            },
        ),
        (ResearchWorkflow.__table__, root),
        (
            ResearchWorkflowRevision.__table__,
            {
                "id": revision_id,
                "owner_id": owner,
                "workflow_id": workflow_id,
                "ordinal": 1,
                "inputs_canonical": canonical_bytes(selection).decode(),
                "inputs_sha256": selection.sha256,
                "created_at": NOW,
            },
        ),
    )


def inserts(index=0, *, mutation=None):
    return [
        str(
            insert(table)
            .values(**values)
            .compile(dialect=dialect(), compile_kwargs={"literal_binds": True})
        )
        + ";"
        for table, values in declaration_rows(index, mutation=mutation)
    ]


def probe_statements():
    parts = [
        "SET CONSTRAINTS ALL DEFERRED;",
        *inserts(),
        "SET CONSTRAINTS ALL IMMEDIATE;",
        "DO $$ BEGIN IF mineagent_workflow_consistent('00000000-0000-0000-0000-0000000445c1'::uuid,'00000000-0000-0000-0000-0000000445c4'::uuid) IS NOT TRUE THEN RAISE EXCEPTION 'Valid declaration graph failed'; END IF; END $$;",
    ]
    # Waiting diagnostics are real root mutations: they must use the same CAS
    # increment as control transitions, without fabricating a child or checkpoint.
    parts.extend(
        [
            "UPDATE research_workflows SET blocked_reason='workflow_child_pending',revision=revision+1 WHERE id='00000000-0000-0000-0000-0000000445c4'::uuid AND revision=1;",
            "DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM research_workflows WHERE id='00000000-0000-0000-0000-0000000445c4'::uuid AND revision=2 AND blocked_reason='workflow_child_pending' AND current_stage_id IS NULL AND latest_checkpoint_id IS NULL) THEN RAISE EXCEPTION 'Diagnostic CAS update failed'; END IF; END $$;",
            "DO $$ BEGIN BEGIN UPDATE research_workflows SET blocked_reason='workflow_another_wait' WHERE id='00000000-0000-0000-0000-0000000445c4'::uuid; RAISE EXCEPTION 'Diagnostic without CAS accepted' USING ERRCODE='XX000'; EXCEPTION WHEN raise_exception THEN IF SQLERRM <> 'Invalid immutable workflow/CAS transition' THEN RAISE; END IF; END; IF NOT EXISTS(SELECT 1 FROM research_workflows WHERE id='00000000-0000-0000-0000-0000000445c4'::uuid AND revision=2 AND blocked_reason='workflow_child_pending') THEN RAISE EXCEPTION 'Rejected diagnostic mutated root'; END IF; END $$;",
        ]
    )
    for index, mutation in enumerate(
        ("plan_hash", "selection_hash", "foreign_selection"), 1
    ):
        parts.append("SET CONSTRAINTS ALL DEFERRED;")
        parts.append(
            "DO $$ BEGIN BEGIN\n"
            + "\n".join(inserts(index, mutation=mutation))
            + "\nSET CONSTRAINTS ALL IMMEDIATE;\nRAISE EXCEPTION 'Forged declaration accepted: "
            + mutation
            + "' USING ERRCODE='XX000';\nEXCEPTION WHEN raise_exception THEN\n IF SQLERRM <> 'Workflow immutable finite graph differs' THEN RAISE; END IF;\nEND; END $$;"
        )
    parts.extend(
        [
            "SET CONSTRAINTS ALL IMMEDIATE;",
            "DO $$ BEGIN IF (SELECT count(*) FROM research_workflows WHERE creation_key LIKE 'sql-declaration-%')<>1 OR EXISTS(SELECT 1 FROM factor_workflow_submissions WHERE owner_id='00000000-0000-0000-0000-0000000445c1'::uuid) THEN RAISE EXCEPTION 'Declaration-only inventory differs'; END IF; END $$;",
            "SELECT 'WORKFLOW_POPULATED_DECLARATION_MATRIX_COMPLETE' AS result;",
        ]
    )
    return tuple(parts)


def render_probe_sql():
    return "\n".join(probe_statements())
