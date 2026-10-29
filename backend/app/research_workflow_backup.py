"""029 retained finite workflow graph; metadata alone never proves completion.

The top-level successor must separately validate actual child/source physical
closures before these references may be treated as a terminal boundary.
"""

import json
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text

from packages.factors.contracts import canonical_bytes, digest
from packages.rdagent_adapter.seed_parent import ResolvedSeedParent
from packages.rdagent_adapter.workflow_config import WorkflowFactorResearchConfig

SCHEMA = "mineagent.research-workflow-restore-closure.v1"
FIELDS = {
    "factor_workflow_submissions": "id owner_id workflow_id stage_id input_revision_id run_id spec_id plan_id request_canonical request_sha256 preflight_sha256 runtime_profile_sha256 config_sha256 selection_sha256 resolved_inputs_sha256 definition_sha256 submission_origin created_at",
    "research_workflow_checkpoints": "id owner_id workflow_id stage_id terminal_canonical terminal_sha256 created_at",
    "research_workflow_commands": "id owner_id workflow_id idempotency_key command_canonical command_sha256 result_canonical created_at",
    "research_workflow_model_consumption": "id owner_id workflow_id stage_id input_revision_id input_sha256 run_id attempt_id generation operation_id transport_id request_sha256 created_at",
    "research_workflow_revisions": "id owner_id workflow_id ordinal inputs_canonical inputs_sha256 created_at",
    "research_workflow_stages": "id owner_id workflow_id ordinal input_revision_id kind child_run_id definition_canonical definition_sha256 allocation_canonical allocation_sha256 created_at",
    "research_workflows": "id owner_id revision creation_key creation_canonical creation_sha256 creation_result_canonical plan_canonical plan_sha256 budget_canonical budget_sha256 mode status blocked_reason current_input_revision_id current_stage_id latest_checkpoint_id created_at updated_at",
}


SCHEMA_V2 = "mineagent.research-workflow-restore-closure.v2"
FIELDS_V2 = {
    **FIELDS,
    "oe_workflow_submissions": "id owner_id workflow_id stage_id input_revision_id run_id submission_id rd_stage_id rd_checkpoint_id source_run_id source_attempt_id source_generation snapshot_id factor_version_id analysis_id release_exposure_id request_sha256 config_sha256 preflight_sha256 runtime_profile_sha256 definition_sha256 selection_sha256 resolved_inputs_sha256 rd_checkpoint_sha256 fixed_inputs_sha256 submission_origin created_at",
}


def closure_fields(schema):
    if schema == SCHEMA:
        return FIELDS
    if schema == SCHEMA_V2:
        return FIELDS_V2
    raise ValueError("Exact workflow closure schema required")


def read_closure(connection, maximum_rows, *, maximum_bytes=32_000_000, schema=SCHEMA):
    if (
        type(maximum_rows) is not int
        or not 0 < maximum_rows <= 100000
        or type(maximum_bytes) is not int
        or not 0 < maximum_bytes <= 512 * 1024**2
    ):
        raise ValueError("Workflow closure requires explicit bounds")
    tables, count, size = {}, 0, 0
    for name, fields in closure_fields(schema).items():
        cursor = connection.execute(
            text(
                f"SELECT to_jsonb(t) FROM (SELECT {','.join(fields.split())} FROM {name} ORDER BY id LIMIT :limit) t"
            ).execution_options(stream_results=True, yield_per=1),
            {"limit": maximum_rows + 1},
        )
        rows = []
        try:
            for row in cursor.scalars():
                count += 1
                size += len(canonical_bytes(row)) + 1
                if count > maximum_rows or size > maximum_bytes:
                    raise ValueError("Workflow closure exceeds declared bounds")
                rows.append(row)
        finally:
            cursor.close()
        tables[name] = rows
    result = {"schema_version": schema, "tables": tables}
    if len(canonical_bytes(result)) > maximum_bytes:
        raise ValueError("Workflow closure envelope exceeds byte bound")
    return result


def indexes(closure, maximum_rows=100000, *, schema=SCHEMA):
    if (
        type(maximum_rows) is not int
        or not 0 < maximum_rows <= 100000
        or type(closure) is not dict
        or set(closure) != {"schema_version", "tables"}
        or closure["schema_version"] != schema
        or type(closure["tables"]) is not dict
        or set(closure["tables"]) != set(closure_fields(schema))
    ):
        raise ValueError("Exact workflow closure shape required")
    result, count = {}, 0
    for name, fields in closure_fields(schema).items():
        rows = closure["tables"][name]
        if type(rows) is not list:
            raise ValueError("Exact workflow row inventory required")
        items = {}
        for row in rows:
            count += 1
            if (
                count > maximum_rows
                or type(row) is not dict
                or set(row) != set(fields.split())
            ):
                raise ValueError("Exact bounded workflow row shape required")
            key = row["id"]
            if type(key) is not str or str(UUID(key)) != key or key in items:
                raise ValueError("Unique canonical workflow row identity required")
            items[key] = row
        result[name] = items
    return result


def _parse(raw, expected, model, cap):
    if type(raw) is not str or not 0 < len(raw.encode()) <= cap:
        raise ValueError("Workflow immutable canonical byte bound")
    value = model.model_validate_json(raw)
    if canonical_bytes(value).decode() != raw or (
        expected is not None and digest(value) != expected
    ):
        raise ValueError("Workflow immutable canonical commitment differs")
    return value


def _scope(row, owner, workflow):
    if row is None or (row["owner_id"], row["workflow_id"]) != (owner, workflow):
        raise ValueError("Workflow immutable owner scope differs")
    return row


def validate_metadata(closure, maximum_rows=100000, *, schema=SCHEMA):
    """Structural commitments only; returned objects are not validated outputs."""
    from backend.app.research_workflow_structured_schemas import (
        CreateStructuredWorkflow,
    )
    from packages.research_inputs.derivation import ExternalNormalizationDefinition
    from packages.research_inputs.feedback import FeedbackProjectionDefinition
    from packages.research_runtime.budget_contracts import BudgetVector
    from packages.research_workflow.admission import CreateWorkflow, WorkflowCreated
    from packages.research_workflow.contracts import WorkflowInputSnapshot
    from packages.research_workflow.feedback_checkpoint import FeedbackCheckpoint
    from packages.research_workflow.local_checkpoint import LocalNormalizationCheckpoint
    from packages.research_workflow.oe_checkpoint import OEStageCheckpoint
    from packages.research_workflow.oe_stage import OEStageDefinition
    from packages.research_workflow.plan import RDGuidancePlan, stage_schedule
    from packages.research_workflow.rd_checkpoint import RDStageCheckpoint
    from packages.research_workflow.rd_stage import RDStageDefinition
    from packages.research_workflow.structured_plan import (
        RDStructuredGuidancePlan,
        guidance_stage_schedule,
    )

    successor = schema == SCHEMA_V2
    own = indexes(closure, maximum_rows, schema=schema)
    plans, selections, definitions, checkpoints = {}, {}, {}, {}
    roots = own["research_workflows"]
    for name, rows in own.items():
        if name == "research_workflows":
            continue
        for row in rows.values():
            root = roots.get(row["workflow_id"])
            if root is None or root["owner_id"] != row["owner_id"]:
                raise ValueError("Workflow orphan/foreign row")
    for root in roots.values():
        owner, wid = root["owner_id"], root["id"]
        structured = (
            successor
            and json.loads(root["plan_canonical"]).get("schema_version")
            == "mineagent.rd-structured-guidance-workflow-plan.v1"
        )
        request = _parse(
            root["creation_canonical"],
            root["creation_sha256"],
            CreateStructuredWorkflow if structured else CreateWorkflow,
            1100000,
        )
        plan = _parse(
            root["plan_canonical"],
            root["plan_sha256"],
            RDStructuredGuidancePlan if structured else RDGuidancePlan,
            1048576,
        )
        budget = _parse(
            root["budget_canonical"], root["budget_sha256"], BudgetVector, 4096
        )
        created = _parse(root["creation_result_canonical"], None, WorkflowCreated, 4096)
        if (
            plan != request.plan
            or str(plan.owner_id) != owner
            or budget != plan.budget_ceiling
            or request.idempotency_key != root["creation_key"]
            or str(created.workflow_id) != wid
            or created.request_sha256 != root["creation_sha256"]
            or type(root["revision"]) is not int
            or not 1 <= root["revision"] <= 1000000
            or root["mode"] != plan.mode
            or root["status"]
            not in {
                "ready",
                "running",
                "pause_requested",
                "paused",
                "cancel_requested",
                "cancelled",
                "blocked",
                "completed",
            }
            or plan.normalize_external_first
            != (request.external_definition is not None)
        ):
            raise ValueError("Workflow original creation/finite plan differs")
        plans[wid] = plan
        revisions = sorted(
            (
                r
                for r in own["research_workflow_revisions"].values()
                if r["workflow_id"] == wid
            ),
            key=lambda r: r["ordinal"],
        )
        if (
            not revisions
            or revisions[0]["ordinal"] != 1
            or any(
                type(r["ordinal"]) is not int
                or not 1 <= r["ordinal"] <= root["revision"]
                for r in revisions
            )
            or len({r["ordinal"] for r in revisions}) != len(revisions)
            or root["current_input_revision_id"] != revisions[-1]["id"]
        ):
            raise ValueError("Workflow input revision history/head differs")
        for row in revisions:
            selected = _parse(
                row["inputs_canonical"],
                row["inputs_sha256"],
                WorkflowInputSnapshot,
                32768,
            )
            if (
                str(selected.owner_id),
                str(selected.workflow_id),
                str(selected.revision_id),
            ) != (owner, wid, row["id"]):
                raise ValueError("Workflow frozen input identity differs")
            selections[row["id"]] = selected
        if (
            str(created.input_revision_id) != revisions[0]["id"]
            or selections[revisions[0]["id"]].refs != request.refs
        ):
            raise ValueError("Workflow creation does not bind original inputs")
        stages = sorted(
            (
                r
                for r in own["research_workflow_stages"].values()
                if r["workflow_id"] == wid
            ),
            key=lambda r: r["ordinal"],
        )
        schedule = guidance_stage_schedule(plan) if successor else stage_schedule(plan)
        if (
            len(stages) > len(schedule)
            or [r["ordinal"] for r in stages] != list(range(1, len(stages) + 1))
            or root["current_stage_id"] != (stages[-1]["id"] if stages else None)
        ):
            raise ValueError("Workflow stage schedule/head differs")
        allocated = BudgetVector()
        seen_checkpoints = []
        for index, stage in enumerate(stages):
            kind, iteration = schedule[index]
            revision = _scope(
                own["research_workflow_revisions"].get(stage["input_revision_id"]),
                owner,
                wid,
            )
            if stage["kind"] != kind or (stage["child_run_id"] is not None) != (
                kind == "rd_factor_generation"
                or (successor and kind == "structured_factor_execution")
            ):
                raise ValueError("Workflow stage family is not a real scheduled child")
            allocation = _parse(
                stage["allocation_canonical"],
                stage["allocation_sha256"],
                BudgetVector,
                4096,
            )
            expected = (
                plan.research_template.budget
                if kind == "rd_factor_generation"
                else plan.structured_template.budget
                if successor and kind == "structured_factor_execution"
                else BudgetVector(
                    storage_bytes=294912 if kind == "external_normalization" else 65536
                )
            )
            if allocation != expected:
                raise ValueError("Workflow exact stage allocation differs")
            allocated = allocated.add(allocation)
            model = {
                "rd_factor_generation": RDStageDefinition,
                "external_normalization": ExternalNormalizationDefinition,
                "experiment_feedback_projection": FeedbackProjectionDefinition,
                **(
                    {"structured_factor_execution": OEStageDefinition}
                    if successor
                    else {}
                ),
            }[kind]
            definition = _parse(
                stage["definition_canonical"], stage["definition_sha256"], model, 8192
            )
            definitions[stage["id"]] = definition
            if (
                kind == "external_normalization"
                and definition != request.external_definition
            ):
                raise ValueError("Workflow original normalization definition differs")
            if kind == "rd_factor_generation" and (
                definition.plan_sha256 != plan.sha256
                or definition.research_iteration != iteration
                or (
                    iteration == 0
                    and definition.parent_factor_version_id
                    != plan.initial_parent_factor_version_id
                )
            ):
                raise ValueError("Workflow actual child definition differs")
            matches = [
                r
                for r in own["research_workflow_checkpoints"].values()
                if r["stage_id"] == stage["id"]
            ]
            if len(matches) > 1 or (index + 1 < len(stages) and len(matches) != 1):
                raise ValueError(
                    "Workflow advanced without unique predecessor checkpoint"
                )
            if matches:
                row = matches[0]
                _scope(row, owner, wid)
                model = {
                    "rd_factor_generation": RDStageCheckpoint,
                    "external_normalization": LocalNormalizationCheckpoint,
                    "experiment_feedback_projection": FeedbackCheckpoint,
                    **(
                        {"structured_factor_execution": OEStageCheckpoint}
                        if successor
                        else {}
                    ),
                }[kind]
                checkpoint = _parse(
                    row["terminal_canonical"], row["terminal_sha256"], model, 1048576
                )
                if (
                    str(checkpoint.owner_id),
                    str(checkpoint.workflow_id),
                    str(checkpoint.stage_id),
                    str(checkpoint.input_revision_id),
                    checkpoint.input_sha256,
                    checkpoint.definition_sha256,
                    checkpoint.allocation_sha256,
                ) != (
                    owner,
                    wid,
                    stage["id"],
                    revision["id"],
                    revision["inputs_sha256"],
                    stage["definition_sha256"],
                    stage["allocation_sha256"],
                ):
                    raise ValueError(
                        "Workflow checkpoint frozen stage commitment differs"
                    )
                checkpoints[row["id"]] = checkpoint
                seen_checkpoints.append(row["id"])
        if not budget.covers(allocated) or root["latest_checkpoint_id"] != (
            seen_checkpoints[-1] if seen_checkpoints else None
        ):
            raise ValueError("Workflow allocation ceiling/checkpoint head differs")
        if root["status"] == "completed" and (
            len(stages) != len(schedule) or len(seen_checkpoints) != len(stages)
        ):
            raise ValueError("Workflow completed label lacks full finite stage history")
    child_ids = [
        row["child_run_id"]
        for row in own["research_workflow_stages"].values()
        if row["child_run_id"] is not None
    ]
    if len(child_ids) != len(set(child_ids)):
        raise ValueError("Workflow cannot attach one child to multiple stages")
    for stage in own["research_workflow_stages"].values():
        wid = stage["workflow_id"]
        stages = {
            r["ordinal"]: r
            for r in own["research_workflow_stages"].values()
            if r["workflow_id"] == wid
        }
        by_stage = {
            r["stage_id"]: (r, checkpoints[r["id"]])
            for r in own["research_workflow_checkpoints"].values()
            if r["workflow_id"] == wid
        }
        definition = definitions[stage["id"]]
        if successor and stage["kind"] == "structured_factor_execution":
            from backend.app.workflow_oe_backup import validate_stage_predecessor

            validate_stage_predecessor(stage, definitions, plans, stages, by_stage)
        elif stage["kind"] == "experiment_feedback_projection":
            previous = stages[stage["ordinal"] - 1]
            previous_row, previous_checkpoint = by_stage[previous["id"]]
            source = definition.source
            if previous["kind"] != "rd_factor_generation" or (
                str(source.workflow_id),
                str(source.stage_id),
                str(source.child_run_id),
                str(source.output_id),
                source.content_sha256,
            ) != (
                wid,
                previous["id"],
                previous["child_run_id"],
                str(previous_checkpoint.analysis_id),
                previous_checkpoint.analysis_result_sha256,
            ):
                raise ValueError(
                    "Workflow feedback is not immediately prior actual child output"
                )
            if stage["id"] in by_stage:
                _, checkpoint = by_stage[stage["id"]]
                if (
                    str(checkpoint.rd_checkpoint_id) != previous_row["id"]
                    or checkpoint.source != source
                ):
                    raise ValueError(
                        "Workflow feedback checkpoint points to another source"
                    )
        elif stage["kind"] == "rd_factor_generation":
            if stage["id"] in by_stage:
                _, checkpoint = by_stage[stage["id"]]
                if str(checkpoint.child_run_id) != stage["child_run_id"]:
                    raise ValueError("Workflow checkpoint attaches a different child")
            if definition.research_iteration == 0:
                expected = (
                    None
                    if stage["ordinal"] == 1
                    else by_stage[stages[stage["ordinal"] - 1]["id"]][0]["id"]
                )
                if (
                    None
                    if definition.predecessor_checkpoint_id is None
                    else str(definition.predecessor_checkpoint_id)
                ) != expected:
                    raise ValueError("Workflow initial child predecessor differs")
                if expected is not None:
                    previous_stage = stages[stage["ordinal"] - 1]
                    previous_checkpoint = by_stage[previous_stage["id"]][1]
                    if (
                        previous_stage["kind"] != "external_normalization"
                        or previous_checkpoint.state != "succeeded"
                    ):
                        raise ValueError(
                            "Workflow child follows unsuccessful normalization"
                        )
            else:
                feedback_stage = stages[stage["ordinal"] - 1]
                prior_stage = stages[stage["ordinal"] - 2]
                feedback_row, feedback = by_stage[feedback_stage["id"]]
                _, prior = by_stage[prior_stage["id"]]
                if (
                    feedback_stage["kind"] != "experiment_feedback_projection"
                    or prior_stage["kind"] != "rd_factor_generation"
                    or feedback.state != "succeeded"
                    or len(feedback.outputs) != 1
                    or str(definition.predecessor_checkpoint_id) != feedback_row["id"]
                    or str(definition.parent_source_stage_id) != prior_stage["id"]
                    or definition.parent_factor_version_id != prior.factor_version_id
                    or tuple(
                        ref
                        for ref in selections[stage["input_revision_id"]].refs
                        if ref.origin == "experiment_feedback_proposal"
                    )
                    != feedback.outputs
                ):
                    raise ValueError(
                        "Workflow new child lacks its immediate released feedback/parent chain"
                    )
    if set(checkpoints) != set(own["research_workflow_checkpoints"]):
        raise ValueError("Workflow orphan checkpoint")
    _validate_commands(own, selections, successor=successor)
    return {
        "indexes": own,
        "plans": plans,
        "selections": selections,
        "definitions": definitions,
        "checkpoints": checkpoints,
    }


def _validate_commands(own, selections, *, successor=False):
    """Retained CAS receipts are historical acknowledgements, not current state."""
    from packages.research_workflow.admission import SelectWorkflowInputs
    from packages.research_workflow.contracts import (
        WorkflowCommand,
        WorkflowCommandReceipt,
    )
    from packages.research_workflow.rd_claim import (
        WorkflowRDClaim,
        WorkflowRDClaimReceipt,
    )

    models = {
        "mineagent.workflow-command.v1": WorkflowCommand,
        "mineagent.workflow-select-inputs.v1": SelectWorkflowInputs,
        "mineagent.workflow-rd-claim.v1": WorkflowRDClaim,
    }
    from packages.research_workflow.oe_stage import (
        WorkflowOEClaim,
        WorkflowOEClaimReceipt,
    )

    if successor:
        models["mineagent.workflow-oe-claim.v1"] = WorkflowOEClaim
    keys, revisions = set(), set()
    for row in own["research_workflow_commands"].values():
        raw = row["command_canonical"]
        if type(raw) is not str or len(raw.encode()) > 32768:
            raise ValueError("Workflow command byte bound")
        model = models.get(json.loads(raw).get("schema_version"))
        if model is None:
            raise ValueError("Unknown workflow command schema")
        command = _parse(raw, row["command_sha256"], model, 32768)
        receipt = _parse(
            row["result_canonical"],
            None,
            WorkflowRDClaimReceipt
            if model is WorkflowRDClaim
            else WorkflowOEClaimReceipt
            if successor and model is WorkflowOEClaim
            else WorkflowCommandReceipt,
            8192,
        )
        root = own["research_workflows"][row["workflow_id"]]
        key = (row["owner_id"], row["workflow_id"], row["idempotency_key"])
        revision = (row["workflow_id"], receipt.revision)
        if (
            key in keys
            or revision in revisions
            or str(command.workflow_id) != row["workflow_id"]
            or command.idempotency_key != row["idempotency_key"]
            or str(receipt.workflow_id) != row["workflow_id"]
            or str(receipt.command_id) != row["id"]
            or receipt.command_sha256 != row["command_sha256"]
            or receipt.revision != command.expected_revision + 1
            or receipt.revision > root["revision"]
        ):
            raise ValueError("Workflow command immutable CAS receipt differs")
        keys.add(key)
        revisions.add(revision)
        if model is WorkflowRDClaim or (successor and model is WorkflowOEClaim):
            stage = _scope(
                own["research_workflow_stages"].get(str(receipt.stage_id)),
                row["owner_id"],
                row["workflow_id"],
            )
            if model is WorkflowOEClaim:
                matches = [
                    value
                    for value in own["oe_workflow_submissions"].values()
                    if value["submission_id"] == str(receipt.submission_id)
                ]
                if len(matches) != 1:
                    raise ValueError(
                        "OE claim requires exact child submission relation"
                    )
                submission_row = matches[0]
            else:
                submission_row = own["factor_workflow_submissions"].get(
                    str(receipt.submission_id)
                )
            submission = _scope(submission_row, row["owner_id"], row["workflow_id"])
            if stage["child_run_id"] != str(receipt.child_run_id) or (
                submission["stage_id"],
                submission["run_id"],
            ) != (stage["id"], stage["child_run_id"]):
                raise ValueError("Workflow claim receipt is not its actual new child")
            _scope(
                own["research_workflow_revisions"].get(str(command.input_revision_id)),
                row["owner_id"],
                row["workflow_id"],
            )
        else:
            for field, table in (
                ("checkpoint_id", "research_workflow_checkpoints"),
                ("current_stage_id", "research_workflow_stages"),
            ):
                identity = getattr(receipt, field)
                if identity is not None:
                    _scope(
                        own[table].get(str(identity)),
                        row["owner_id"],
                        row["workflow_id"],
                    )
            if model is SelectWorkflowInputs:
                rows = [
                    r
                    for r in own["research_workflow_revisions"].values()
                    if r["workflow_id"] == row["workflow_id"]
                    and r["ordinal"] == receipt.revision
                ]
                if len(rows) != 1 or selections[rows[0]["id"]].refs != command.refs:
                    raise ValueError(
                        "Workflow selection receipt lost its immutable version"
                    )


def validate_child_declarations(
    metadata, *, rd_source_closure, direct_analysis_closure
):
    """Exact immutable child declaration graph, not provider/physical completion.

    Inputs are already structurally validated predecessor closures. All resolved
    text still requires the source-owned retained-byte pass before public restore.
    """
    from backend.app.rdagent_provider_profile import RDProviderServerRuntimeProfile
    from backend.app.rdagent_provider_submission_contracts import provider_commitment
    from backend.app.rdagent_workflow_submission import stage_research_request
    from backend.app.research_workflow_public_schemas import WorkflowSubmit
    from packages.rdagent_adapter.workflow_config import WorkflowFactorResearchConfig
    from packages.research_workflow.admission import CreateWorkflow

    own = metadata["indexes"]
    runs = {r["id"]: r for r in rd_source_closure["external_bindings"]["runs"]}
    specs = {r["id"]: r for r in rd_source_closure["tables"]["factor_research_specs"]}
    analyses = {
        r["id"]: r for r in direct_analysis_closure["tables"]["factor_analysis_plans"]
    }
    submissions = own["factor_workflow_submissions"]
    stages = own["research_workflow_stages"]
    expected_runs = {
        r["id"]
        for r in runs.values()
        if r["config"].get("schema_version") == "mineagent.factor-research.v3"
    }
    if (
        expected_runs != {s["run_id"] for s in submissions.values()}
        or expected_runs
        != {
            s["child_run_id"]
            for s in stages.values()
            if s["kind"] == "rd_factor_generation"
        }
        or len(expected_runs) != len(submissions)
    ):
        raise ValueError(
            "Workflow exact new-child/submission reverse inventory differs"
        )
    configs = {}
    for row in submissions.values():
        owner, wid = row["owner_id"], row["workflow_id"]
        stage = _scope(stages.get(row["stage_id"]), owner, wid)
        revision = _scope(
            own["research_workflow_revisions"].get(row["input_revision_id"]), owner, wid
        )
        root, plan = own["research_workflows"][wid], metadata["plans"][wid]
        definition = metadata["definitions"][stage["id"]]
        selection = metadata["selections"][revision["id"]]
        run, spec, analysis = (
            runs.get(row["run_id"]),
            specs.get(row["spec_id"]),
            analyses.get(row["plan_id"]),
        )
        if any(v is None or v["owner_id"] != owner for v in (run, spec, analysis)):
            raise ValueError("Workflow child declaration owner differs")
        config = WorkflowFactorResearchConfig.model_validate_json(
            canonical_bytes(run["config"])
        )
        from backend.app.research_workflow_structured_schemas import (
            CreateStructuredWorkflow,
            StructuredWorkflowSubmit,
        )
        from packages.research_workflow.structured_plan import RDStructuredGuidancePlan

        structured = type(plan) is RDStructuredGuidancePlan
        if (
            structured
            and rd_source_closure.get("schema_version")
            != "mineagent.rd-source-restore-closure.v11"
        ):
            raise ValueError("Structured workflow child requires exact source10")
        created = _parse(
            root["creation_canonical"],
            root["creation_sha256"],
            CreateStructuredWorkflow if structured else CreateWorkflow,
            1100000,
        )
        body = _parse(
            created.public_submission_canonical,
            None,
            StructuredWorkflowSubmit if structured else WorkflowSubmit,
            131072,
        )
        request = stage_research_request(body.request.research, plan, definition)
        bound = config.workflow_inputs.binding
        parent = config.workflow_parent
        if (
            body.idempotency_key != root["creation_key"]
            or body.request.research.selected_steer_version_ids
            or row["submission_origin"] != "workflow_stage"
            or stage["kind"] != "rd_factor_generation"
            or stage["input_revision_id"] != revision["id"]
            or stage["child_run_id"] != run["id"]
            or str(config.workflow_child_run_id) != run["id"]
            or (
                str(bound.owner_id),
                str(bound.workflow_id),
                str(bound.stage_id),
                str(bound.input_revision_id),
                bound.input_sha256,
                bound.definition_sha256,
            )
            != (
                owner,
                wid,
                stage["id"],
                revision["id"],
                selection.sha256,
                definition.sha256,
            )
            or config.workflow_inputs.selection != selection
            or (spec["run_id"], analysis["run_id"], analysis["spec_id"])
            != (run["id"], run["id"], spec["id"])
            or (
                row["config_sha256"],
                spec["config_sha256"],
                run["request_sha256"],
                analysis["config_sha256"],
            )
            != (config.sha256,) * 4
            or (spec["input_sha256"], analysis["input_sha256"])
            != (config.input.sha256,) * 2
            or spec["config"] != run["config"]
            or run["config"] != config.model_dump(mode="json")
            or run["purpose"] != "factor_research"
            or run["engine"] != "rdagent"
            or run["idempotency_key"] != f"workflow-stage:{stage['id']}"
            or row["selection_sha256"] != selection.sha256
            or row["definition_sha256"] != definition.sha256
            or row["resolved_inputs_sha256"] != config.workflow_inputs.sha256
            or row["request_canonical"] != canonical_bytes(request).decode()
            or row["request_sha256"] != request.sha256
            or stage["allocation_canonical"] != canonical_bytes(config.budget).decode()
            or stage["allocation_sha256"] != digest(config.budget)
            or (None if parent is None else parent.factor_version_id)
            != definition.parent_factor_version_id
            or request.model_profile_id != config.model_binding.profile_id
            or request.objective != plan.objective
            or request.source_policy != plan.source_policy
            or analysis["objective"] != request.objective.model_dump(mode="json")
            or analysis["objective_canonical"]
            != canonical_bytes(request.objective).decode()
            or analysis["objective_sha256"] != request.objective.sha256
            or analysis["source_policy"]
            != request.source_policy.model_dump(mode="json")
            or analysis["source_policy_sha256"] != digest(request.source_policy)
            or analysis["label_source_dataset_id"] != str(request.dataset_version_id)
            or run["dataset_version_id"] != str(request.dataset_version_id)
            or analysis["label_source_manifest_sha256"]
            != config.input.dataset.content_sha256
            or analysis["qlib_image_id"] != config.runtime.analysis_image
            or analysis["qlib_installed_parent_image_id"]
            != plan.qlib_installed_parent_image_id
            or analysis["analysis_dependency_lock_sha256"]
            != plan.analysis_dependency_lock_sha256
        ):
            raise ValueError("Workflow actual child immutable declarations differ")
        from packages.rdagent_adapter.controller_profile import (
            controller_selection_fields,
        )

        runtime = RDProviderServerRuntimeProfile(
            **controller_selection_fields(config),
            runtime=config.runtime,
            qlib_installed_parent_image_id=analysis["qlib_installed_parent_image_id"],
        )
        if row["runtime_profile_sha256"] != digest(runtime) or row[
            "preflight_sha256"
        ] != provider_commitment(
            UUID(owner),
            request,
            dataset_sha256=config.input.dataset.content_sha256,
            input_sha256=config.input.sha256,
            profile_sha256=digest(runtime),
            model_binding_sha256=config.model_binding.sha256,
            steer_snapshot_sha256=config.steer_snapshot.sha256,
            parent_binding_sha256=None if parent is None else parent.sha256,
        ):
            raise ValueError(
                "Workflow child retained preflight/runtime commitment differs"
            )
        if definition.research_iteration:
            prior = next(
                c
                for c in metadata["checkpoints"].values()
                if str(c.stage_id) == str(definition.parent_source_stage_id)
            )
            if parent is None or parent.code_sha256 != prior.source_sha256:
                raise ValueError(
                    "Workflow actual prior child source commitment differs"
                )
        configs[run["id"]] = config
    return configs


def _only(rows, **fields):
    matches = [
        row
        for row in rows
        if all(row.get(key) == value for key, value in fields.items())
    ]
    if len(matches) != 1:
        raise ValueError("Workflow checkpoint requires one exact retained fact")
    return matches[0]


def validate_rd_checkpoints(
    metadata,
    *,
    configs,
    research_closure,
    rd_source_closure,
    execution_closure,
    direct_analysis_closure,
    factor_closure,
):
    """Bind checkpoint claims to previously byte-validated actual RD/process facts.

    This relational pass never substitutes for the exact v12/v7 process/source,
    snapshot and direct-analysis physical validators in the central restore order.
    """
    from packages.factors.execution_snapshot import ExecutionSnapshotManifest
    from packages.rdagent_adapter.workflow_bootstrap import (
        RDWorkflowOperationalBootstrap,
    )
    from packages.research_runtime.budget_contracts import BudgetVector
    from packages.research_workflow.rd_checkpoint import RDStageCheckpoint

    own = metadata["indexes"]
    shared, rd = research_closure["tables"], rd_source_closure["tables"]
    runs = rd_source_closure["external_bindings"]["runs"]
    attempts = research_closure["external_bindings"]["attempts"]
    for checkpoint in metadata["checkpoints"].values():
        if not isinstance(checkpoint, RDStageCheckpoint):
            continue
        owner, run_id = str(checkpoint.owner_id), str(checkpoint.child_run_id)
        scope = {
            "owner_id": owner,
            "run_id": run_id,
            "attempt_id": str(checkpoint.attempt_id),
            "generation": checkpoint.generation,
        }
        run = _only(runs, id=run_id, owner_id=owner)
        attempt = _only(attempts, owner_id=owner, run_id=run_id)
        receipt = own["factor_workflow_submissions"].get(str(checkpoint.submission_id))
        if receipt is None or (
            receipt["owner_id"],
            receipt["workflow_id"],
            receipt["stage_id"],
            receipt["run_id"],
            receipt["spec_id"],
            receipt["plan_id"],
        ) != (
            owner,
            str(checkpoint.workflow_id),
            str(checkpoint.stage_id),
            run_id,
            str(checkpoint.spec_id),
            str(checkpoint.plan_id),
        ):
            raise ValueError("Workflow checkpoint submission differs")
        if (
            run["status"] != "succeeded"
            or attempt["id"] != scope["attempt_id"]
            or (
                run["generation"],
                run["active_attempt_id"],
                attempt["generation"],
                attempt["sandbox_stopped"],
            )
            != (scope["generation"], scope["attempt_id"], scope["generation"], True)
            or attempt["finished_at"] is None
        ):
            raise ValueError(
                "Workflow checkpoint status lacks exact final contained attempt"
            )
        start = _only(rd["factor_generation_starts"], **scope)
        source = _only(rd["factor_generation_sources"], **scope, start_id=start["id"])
        evaluation = _only(
            rd["factor_evaluation_starts"], **scope, start_id=start["id"]
        )
        evaluated = _only(
            rd["factor_evaluation_outcomes"], **scope, evaluation_id=evaluation["id"]
        )
        publication = _only(
            execution_closure["tables"]["factor_execution_snapshot_publications"],
            **scope,
            evaluation_id=evaluation["id"],
        )
        analysis = _only(
            direct_analysis_closure["tables"]["factor_direct_analysis_intents"],
            **scope,
            plan_id=receipt["plan_id"],
            snapshot_id=publication["id"],
        )
        outcome = _only(
            direct_analysis_closure["tables"]["factor_direct_analysis_outcomes"],
            **scope,
            analysis_id=analysis["id"],
        )
        version = _only(
            factor_closure["tables"]["factor_versions"],
            owner_id=owner,
            id=source["factor_version_id"],
        )
        config = configs[run_id]
        parent = config.workflow_parent
        parent_id = None if parent is None else str(parent.factor_version_id)
        if (
            start["spec_id"] != receipt["spec_id"]
            or start["logical_step"] != 0
            or start["task_index"] != 0
            or start["parent_factor_version_id"] != parent_id
            or version["parent_version_id"] != parent_id
            or (parent is None and version["ordinal"] != 1)
            or (
                parent is not None
                and (
                    version["factor_id"] != str(parent.factor_id)
                    or version["ordinal"] <= parent.ordinal
                )
            )
            or version["source_kind"] != "rdagent"
            or version["code_sha256"] != source["source_sha256"]
            or evaluation["evaluated_source_id"] != source["id"]
            or evaluation["factor_version_id"] != version["id"]
            or evaluated["status"] != "valid"
            or (
                publication["start_id"],
                publication["source_id"],
                publication["factor_version_id"],
            )
            != (start["id"], source["id"], version["id"])
            or (
                str(checkpoint.start_id),
                str(checkpoint.source_id),
                str(checkpoint.evaluation_id),
                str(checkpoint.factor_version_id),
                checkpoint.source_sha256,
            )
            != (
                start["id"],
                source["id"],
                evaluation["id"],
                version["id"],
                source["source_sha256"],
            )
            or (
                str(checkpoint.snapshot_id),
                checkpoint.snapshot_manifest_sha256,
                str(checkpoint.analysis_id),
                checkpoint.analysis_result_sha256,
                checkpoint.analysis_measurement_sha256,
            )
            != (
                publication["id"],
                publication["manifest_sha256"],
                analysis["id"],
                outcome["result_sha256"],
                outcome["measurement_sha256"],
            )
        ):
            raise ValueError(
                "Workflow checkpoint source/evaluation/analysis chain differs"
            )
        manifest = ExecutionSnapshotManifest.model_validate_json(
            canonical_bytes(publication["manifest"])
        )
        if (
            str(manifest.owner_id),
            str(manifest.run_id),
            str(manifest.attempt_id),
            manifest.generation,
            str(manifest.snapshot_id),
            str(manifest.evaluation_id),
            str(manifest.generation_start_id),
            str(manifest.source_id),
            str(manifest.input.version.factor_version_id),
            manifest.input.version.code_sha256,
            manifest.input_sha256,
            manifest.output_rows_sha256,
            digest(manifest),
        ) != (
            owner,
            run_id,
            scope["attempt_id"],
            scope["generation"],
            publication["id"],
            evaluation["id"],
            start["id"],
            source["id"],
            version["id"],
            source["source_sha256"],
            evaluated["snapshot_input_sha256"],
            evaluated["snapshot_output_sha256"],
            publication["manifest_sha256"],
        ):
            raise ValueError("Workflow checkpoint snapshot commitments differ")
        controller = _only(rd["factor_controller_launches"], **scope)
        bootstrap = RDWorkflowOperationalBootstrap.model_validate_json(
            canonical_bytes(controller["bootstrap"])
        )
        if (
            bootstrap.config != config
            or bootstrap.sha256 != controller["bootstrap_sha256"]
            or (
                str(bootstrap.owner_id),
                str(bootstrap.run_id),
                str(bootstrap.attempt_id),
                bootstrap.generation,
                str(bootstrap.spec_id),
            )
            != (
                owner,
                run_id,
                scope["attempt_id"],
                scope["generation"],
                receipt["spec_id"],
            )
        ):
            raise ValueError("Workflow checkpoint controller belongs to another child")
        expected = {
            controller["admission_id"]: "rd_orchestrator",
            outcome["admission_id"]: "native_evaluator",
        }
        for part in manifest.partitions:
            for admission_id, role in (
                (str(part.candidate_admission_id), "factor_cutoff_candidate"),
                (str(part.normalizer_admission_id), "factor_normalizer"),
            ):
                if admission_id in expected:
                    raise ValueError("Workflow checkpoint duplicate physical unit")
                expected[admission_id] = role
        admissions = [
            r for r in shared["research_process_admissions"] if r["run_id"] == run_id
        ]
        if (
            not 4 <= len(admissions) <= 18
            or len(admissions) != len(expected)
            or {r["id"]: r["role"] for r in admissions} != expected
            or any(any(r[k] != v for k, v in scope.items()) for r in admissions)
        ):
            raise ValueError(
                "Workflow checkpoint exact physical-unit inventory differs"
            )
        if tuple(str(v) for v in checkpoint.process_admission_ids) != tuple(
            sorted(expected)
        ):
            raise ValueError(
                "Workflow checkpoint physical-unit receipt inventory differs"
            )
        for admission in admissions:
            state = _only(
                shared["research_process_states"], **scope, admission_id=admission["id"]
            )
            if state["status"] != "stopped":
                raise ValueError("Workflow checkpoint process remains unresolved")
        controller_receipts = [
            r
            for r in shared["research_process_receipts"]
            if r["admission_id"] == controller["admission_id"]
        ]
        if len(controller_receipts) != 4 or {
            r["kind"] for r in controller_receipts
        } != {"launch_intent", "launch_identity", "controller_child", "stop"}:
            raise ValueError("Workflow checkpoint controller stop chain incomplete")
        stop = _only(controller_receipts, **scope, kind="stop")
        if (
            str(checkpoint.controller_admission_id) != controller["admission_id"]
            or checkpoint.controller_stop_sha256 != stop["payload_sha256"]
        ):
            raise ValueError("Workflow checkpoint stop commitment differs")
        reservations = [
            r for r in shared["research_budget_reservations"] if r["run_id"] == run_id
        ]
        settlements = [
            r for r in shared["research_budget_settlements"] if r["run_id"] == run_id
        ]
        if (
            not 1 <= len(reservations) <= 256
            or len(reservations) != len(settlements)
            or tuple(str(v) for v in checkpoint.reservation_ids)
            != tuple(sorted(r["id"] for r in reservations))
        ):
            raise ValueError("Workflow checkpoint held-unit inventory differs")
        for reservation in reservations:
            settled = _only(settlements, **scope, reservation_id=reservation["id"])
            if (
                any(reservation[k] != v for k, v in scope.items())
                or reservation["state"] != "settled"
                or settled["outcome"] != "conservative_unknown"
                or BudgetVector.model_validate_json(
                    canonical_bytes(reservation["amounts"])
                )
                != BudgetVector.model_validate_json(canonical_bytes(settled["charge"]))
            ):
                raise ValueError(
                    "Workflow checkpoint original holds are not fully settled"
                )


def validate_local_checkpoints(metadata, *, input_closure, input_facts):
    """Join source byte-validation results to actual deterministic boundaries.

    Internal composition pass only: input_facts must be the results of the
    required source closure validator, not a serialized authorization document.
    """
    from backend.app.research_input_backup import indexes as input_indexes
    from packages.factors.contracts import digest

    source = input_indexes(input_closure)
    own = metadata["indexes"]
    for identity, checkpoint in metadata["checkpoints"].items():
        row = own["research_workflow_checkpoints"][identity]
        stage = own["research_workflow_stages"][row["stage_id"]]
        if stage["kind"] in {"rd_factor_generation", "structured_factor_execution"}:
            continue
        derivation = _only(
            source["research_input_derivations"].values(),
            id=str(checkpoint.derivation_id),
            owner_id=row["owner_id"],
            workflow_id=row["workflow_id"],
            stage_id=row["stage_id"],
        )
        fact = input_facts.get(derivation["id"])
        if fact is None or not fact["events"]:
            raise ValueError("Workflow local checkpoint lacks verified source events")
        terminal = fact["events"][-1]
        if (
            terminal.state in {"prepared", "started"}
            or str(terminal.id) != str(checkpoint.terminal_event_id)
            or digest(terminal) != checkpoint.terminal_event_sha256
            or fact["state"] != checkpoint.state
            or terminal.state != checkpoint.state
            or terminal.outputs != checkpoint.outputs
        ):
            raise ValueError("Workflow local checkpoint differs from actual terminal")
        versions = sorted(
            (
                v
                for v in source["research_input_versions"].values()
                if v["derivation_id"] == derivation["id"]
            ),
            key=lambda v: v["item_ordinal"],
        )
        object_ids = [v["object_id"] for v in versions]
        if stage["kind"] == "external_normalization":
            object_ids.insert(0, derivation["raw_object_id"])
        expected = tuple(
            (obj["id"], obj["kind"], obj["sha256"], obj["size_bytes"])
            for obj in (source["research_input_objects"][key] for key in object_ids)
        )
        if (
            tuple(
                (str(obj.id), obj.kind, obj.sha256, obj.size_bytes)
                for obj in checkpoint.objects
            )
            != expected
        ):
            raise ValueError("Workflow local checkpoint retained inventory differs")
        if stage["kind"] == "experiment_feedback_projection":
            exposures = () if not fact["outputs"] else fact["outputs"][0].exposure_ids
            if checkpoint.exposure_ids != exposures:
                raise ValueError("Workflow feedback checkpoint release ledger differs")


def validate_resolved_inputs(
    metadata, *, configs, input_closure, input_facts, steer_closure
):
    """Reconstruct selected text from immutable versions, never current heads."""
    from hashlib import sha256

    from backend.app.research_input_backup import indexes as input_indexes
    from packages.rdagent_adapter.steer import SteerSnapshot, SteerVersion
    from packages.research_inputs.resolved import ResolvedInputVersion

    source = input_indexes(input_closure)
    if steer_closure.get("schema_version") != "mineagent.rd-steer-closure.v1":
        raise ValueError("Workflow immutable manual closure required")
    manuals = {row["id"]: row for row in steer_closure["tables"]["rd_steer_versions"]}
    if len(manuals) != len(steer_closure["tables"]["rd_steer_versions"]):
        raise ValueError("Duplicate manual version")
    resolved = {}
    for identity, selection in metadata["selections"].items():
        values, manual_values = [], []
        for ref in selection.refs:
            if ref.origin == "manual_user_submission":
                row = manuals.get(str(ref.version_id))
                if row is None or (
                    row["owner_id"],
                    row["steer_id"],
                    row["content_sha256"],
                ) != (str(selection.owner_id), str(ref.record_id), ref.content_sha256):
                    raise ValueError("Workflow manual version binding differs")
                value = _parse(
                    row["content_canonical"], ref.content_sha256, SteerVersion, 32768
                )
                if (value.steer_id, value.version_id) != (
                    ref.record_id,
                    ref.version_id,
                ):
                    raise ValueError("Workflow manual document identity differs")
                manual_values.append(value)
                title = "Manual " + value.kind
            else:
                row = source["research_input_versions"].get(str(ref.version_id))
                if row is None or (
                    row["owner_id"],
                    row["record_id"],
                    row["content_sha256"],
                ) != (str(selection.owner_id), str(ref.record_id), ref.content_sha256):
                    raise ValueError("Workflow input version binding differs")
                fact = input_facts.get(row["derivation_id"])
                ordinal = row["item_ordinal"]
                if (
                    fact is None
                    or fact["state"] != "succeeded"
                    or not 0 <= ordinal < len(fact["outputs"])
                    or fact["events"][-1].outputs[ordinal] != ref
                ):
                    raise ValueError(
                        "Workflow input lacks actual successful derivation"
                    )
                value = fact["outputs"][ordinal]
                if value.origin != ref.origin or digest(value) != ref.content_sha256:
                    raise ValueError("Workflow projected source origin/content differs")
                title = value.title
            values.append(
                ResolvedInputVersion(
                    ref=ref,
                    kind=value.kind,
                    title=title,
                    text=value.text,
                    text_sha256=sha256(value.text.encode("utf-8")).hexdigest(),
                )
            )
        SteerSnapshot(owner_id=selection.owner_id, versions=tuple(manual_values))
        if len(canonical_bytes([v.model_dump(mode="json") for v in values])) > 32768:
            raise ValueError("Workflow resolved input aggregate exceeds bound")
        resolved[identity] = tuple(values)
    for config in configs.values():
        snapshot = config.workflow_inputs
        if snapshot.inputs != resolved[str(snapshot.selection.revision_id)]:
            raise ValueError(
                "Workflow child retained text differs from actual selected versions"
            )
    return resolved


def read_seed_parent(config, *, factor_closure, read_bytes):
    """Pure retained parent reconstruction; no live owner/session authority shim."""
    from packages.factors.contracts import FactorDefinition
    from packages.rdagent_adapter.seed_parent import (
        ResolvedSeedParent,
        SeedParentReference,
    )

    reference = config.workflow_parent
    if reference is None:
        return None
    tables = factor_closure["tables"]
    owner = str(reference.owner_id)
    version = _only(
        tables["factor_versions"], id=str(reference.factor_version_id), owner_id=owner
    )
    factor = _only(tables["factors"], id=version["factor_id"], owner_id=owner)
    obj = _only(tables["factor_objects"], id=version["code_object_id"], owner_id=owner)
    if (
        obj["object_kind"] != "code"
        or obj["media_type"] != "text/plain"
        or obj["sha256"] != version["code_sha256"]
        or type(obj["size_bytes"]) is not int
        or not 1 <= obj["size_bytes"] <= 131072
        or obj["storage_key"] != f"factors/{owner}/objects/{obj['sha256']}.py"
        or len(canonical_bytes(version["source_provenance"])) > 65536
    ):
        raise ValueError("Workflow retained parent storage identity differs")
    actual = SeedParentReference.model_validate_json(
        canonical_bytes(
            {
                "owner_id": owner,
                "factor_id": factor["id"],
                "factor_version_id": version["id"],
                "ordinal": version["ordinal"],
                "code_object_id": obj["id"],
                "code_sha256": obj["sha256"],
                "code_size_bytes": obj["size_bytes"],
                "definition_sha256": version["definition_sha256"],
                "source_provenance_sha256": digest(version["source_provenance"]),
            }
        )
    )
    if actual != reference:
        raise ValueError("Workflow retained parent declaration differs")
    raw = read_bytes(obj["storage_key"], obj["sha256"], 131072)
    if type(raw) is not bytes or len(raw) != obj["size_bytes"]:
        raise ValueError("Workflow retained parent size differs")
    value = ResolvedSeedParent(
        reference=actual,
        definition=FactorDefinition.model_validate_json(
            canonical_bytes(version["definition"])
        ),
        source=raw.decode("utf-8"),
    )
    if value.source != version["code"]:
        raise ValueError("Workflow retained parent bytes differ from immutable version")
    value.prompt_section()
    return value


def validate_consumption(
    metadata,
    *,
    configs,
    research_closure,
    rd_source_closure,
    factor_closure,
    read_bytes,
):
    """Exact admitted first request inclusion, never proof of provider delivery."""
    from packages.rdagent_adapter.workflow_prompt import first_workflow_messages
    from packages.rdagent_provider.codec import canonical_bytes as request_bytes
    from packages.rdagent_provider.codec import parse_request, request_sha256

    shared = research_closure["tables"]
    rd = rd_source_closure["tables"]
    own = metadata["indexes"]
    operations = {r["id"]: r for r in shared["research_model_operations"]}
    objects = {r["id"]: r for r in shared["research_objects"]}
    calls = {r["id"]: r for r in rd["factor_upstream_calls"]}
    expected, seen = set(), set()
    for member in rd["factor_generation_model_operations"]:
        if member["run_id"] not in configs:
            continue
        call = calls.get(member["call_id"])
        if call is None:
            raise ValueError("Workflow request lacks actual upstream call")
        if (
            call["phase"] == "generation"
            and call["ordinal"] == 0
            and member["request_ordinal"] == 0
        ):
            expected.add(member["operation_id"])
    for row in own["research_workflow_model_consumption"].values():
        run_id = row["run_id"]
        if run_id not in configs or row["operation_id"] in seen:
            raise ValueError("Workflow consumption run/operation inventory differs")
        seen.add(row["operation_id"])
        config = configs[run_id]
        snapshot = config.workflow_inputs
        stage = own["research_workflow_stages"][row["stage_id"]]
        if (
            row["owner_id"],
            row["workflow_id"],
            row["stage_id"],
            row["input_revision_id"],
            row["input_sha256"],
            run_id,
        ) != (
            str(snapshot.binding.owner_id),
            str(snapshot.binding.workflow_id),
            str(snapshot.binding.stage_id),
            str(snapshot.selection.revision_id),
            config.workflow_inputs_sha256,
            stage["child_run_id"],
        ):
            raise ValueError("Workflow consumption frozen input binding differs")
        scope = {
            key: row[key] for key in ("owner_id", "run_id", "attempt_id", "generation")
        }
        operation = _only(operations.values(), id=row["operation_id"], **scope)
        member = _only(
            rd["factor_generation_model_operations"],
            operation_id=operation["id"],
            **scope,
        )
        call = _only(calls.values(), id=member["call_id"], **scope)
        transport = _only(
            shared["research_model_transports"], id=row["transport_id"], **scope
        )
        if (
            call["phase"] != "generation"
            or call["ordinal"] != 0
            or member["request_ordinal"] != 0
            or member["relation"] != "initial"
            or transport["operation_id"] != operation["id"]
            or transport["ordinal"] != 1
            or row["request_sha256"] != operation["request_sha256"]
        ):
            raise ValueError(
                "Workflow consumption is not exact first generation admission"
            )
        request = parse_request(operation["protocol"], operation["request"])
        obj = _only(
            objects.values(),
            id=operation["request_object_id"],
            owner_id=row["owner_id"],
        )
        raw = read_bytes(obj["storage_key"], obj["sha256"], obj["size_bytes"])
        expected_raw = request_bytes(operation["protocol"], request)
        if (
            type(raw) is not bytes
            or len(raw) != obj["size_bytes"]
            or raw != expected_raw
            or obj["sha256"] != operation["request_sha256"]
            or request_sha256(operation["protocol"], request)
            != operation["request_sha256"]
        ):
            raise ValueError("Workflow retained actual request bytes differ")
        seed = read_seed_parent(
            config, factor_closure=factor_closure, read_bytes=read_bytes
        )
        system, user = first_workflow_messages(config, seed)
        if request.kind != "chat" or tuple(
            (m.role, m.content) for m in request.messages
        ) != (("system", system), ("user", user)):
            raise ValueError(
                "Workflow actual request omits or changes selected guidance"
            )
    if seen != expected:
        raise ValueError("Workflow first request reverse consumption inventory differs")


def validate_closure(
    closure,
    *,
    research_closure,
    rd_source_closure,
    execution_closure,
    direct_analysis_closure,
    factor_closure,
    input_closure,
    steer_closure,
    read_bytes,
    inventory,
    maximum_rows=100000,
    oe_closure=None,
    oe_facts=None,
):
    """028 composition after mandatory central v12/v7 physical validators.

    No capability is installed by this function. The central restore dispatcher
    must validate research processes/accounting, RD source/SDK, factor, snapshot,
    analysis and steer closures first; a successful metadata call is not a
    substitute. This pass additionally reads actual source and request bytes.
    """
    from backend.app import research_input_backup

    if (
        research_closure.get("schema_version"),
        rd_source_closure.get("schema_version"),
    ) not in {
        (
            "mineagent.research-restore-closure.v16",
            "mineagent.rd-source-restore-closure.v11",
        ),
        (
            "mineagent.research-restore-closure.v14",
            "mineagent.rd-source-restore-closure.v9",
        ),
        (
            "mineagent.research-restore-closure.v15",
            "mineagent.rd-source-restore-closure.v10",
        ),
    }:
        raise ValueError("Workflow cold dependency requires exact successor tuple")
    for value, schema in (
        (execution_closure, "mineagent.execution-snapshot-restore-closure.v2"),
        (direct_analysis_closure, "mineagent.direct-analysis-restore-closure.v1"),
        (factor_closure, "mineagent.factor-restore-closure.v2"),
        (steer_closure, "mineagent.rd-steer-closure.v1"),
    ):
        if type(value) is not dict or value.get("schema_version") != schema:
            raise ValueError("Workflow cold dependency requires exact successor tuple")
    metadata = validate_metadata(
        closure,
        maximum_rows,
        schema=SCHEMA_V2
        if rd_source_closure.get("schema_version")
        == "mineagent.rd-source-restore-closure.v11"
        else SCHEMA,
    )
    configs = validate_child_declarations(
        metadata,
        rd_source_closure=rd_source_closure,
        direct_analysis_closure=direct_analysis_closure,
    )
    facts = research_input_backup.validate_closure(
        input_closure,
        workflow_closure=closure,
        direct_analysis_closure=direct_analysis_closure,
        rd_source_closure=rd_source_closure,
        execution_closure=execution_closure,
        read_bytes=read_bytes,
        inventory=inventory,
        maximum_rows=maximum_rows,
    )
    validate_local_checkpoints(metadata, input_closure=input_closure, input_facts=facts)
    resolved = validate_resolved_inputs(
        metadata,
        configs=configs,
        input_closure=input_closure,
        input_facts=facts,
        steer_closure=steer_closure,
    )
    validate_rd_checkpoints(
        metadata,
        configs=configs,
        research_closure=research_closure,
        rd_source_closure=rd_source_closure,
        execution_closure=execution_closure,
        direct_analysis_closure=direct_analysis_closure,
        factor_closure=factor_closure,
    )
    for config in configs.values():
        read_seed_parent(config, factor_closure=factor_closure, read_bytes=read_bytes)
    rd_metadata = metadata
    oe_configs = {}
    if closure["schema_version"] == SCHEMA_V2:
        from backend.app import workflow_oe_backup

        oe_configs = workflow_oe_backup.build_context(
            {
                "workflow_closure": closure,
                "research_closure": research_closure,
                "rd_source_closure": rd_source_closure,
                "execution_closure": execution_closure,
                "direct_analysis_closure": direct_analysis_closure,
                "oe_structured_closure": oe_closure,
            },
            maximum_rows,
            metadata=metadata,
        )
        rd_metadata = workflow_oe_backup.partition_rd_consumption(
            metadata, configs, oe_configs
        )
        for config in oe_configs.values():
            inputs = config.workflow_context.inputs
            if inputs.inputs != resolved[str(inputs.selection.revision_id)]:
                raise ValueError(
                    "Workflow OE retained text differs from selected versions"
                )
        workflow_oe_backup.validate_consumption(
            metadata, oe_configs, research_closure, oe_closure, read_bytes
        )
        workflow_oe_backup.validate_checkpoints(
            metadata, oe_configs, research_closure, oe_closure, oe_facts
        )
    elif oe_closure is not None or oe_facts is not None:
        raise ValueError("Historical workflow cannot acquire OE closure facts")
    validate_consumption(
        rd_metadata,
        configs=configs,
        research_closure=research_closure,
        rd_source_closure=rd_source_closure,
        factor_closure=factor_closure,
        read_bytes=read_bytes,
    )


@dataclass(frozen=True)
class WorkflowColdContext:
    """Internal actual-row/byte projection, not a serialized restore grant."""

    config: WorkflowFactorResearchConfig
    parent: ResolvedSeedParent | None


def build_workflow_context(
    closure,
    *,
    rd_source_closure,
    direct_analysis_closure,
    factor_closure,
    read_bytes,
    maximum_rows=100000,
):
    """Pre-physical immutable declarations; final full closure remains mandatory.

    Unlike legacy PreparedSeed this carries no fabricated user-submission ID.
    Queued and unseeded workflow children are included so origin cannot fall
    through to an older controller/config path merely because no parent exists.
    """
    if rd_source_closure.get("schema_version") not in {
        "mineagent.rd-source-restore-closure.v11",
        "mineagent.rd-source-restore-closure.v9",
        "mineagent.rd-source-restore-closure.v10",
    }:
        raise ValueError(
            "Workflow pre-physical context requires exact supported RD schema"
        )
    metadata = validate_metadata(
        closure,
        maximum_rows,
        schema=SCHEMA_V2
        if rd_source_closure.get("schema_version")
        == "mineagent.rd-source-restore-closure.v11"
        else SCHEMA,
    )
    configs = validate_child_declarations(
        metadata,
        rd_source_closure=rd_source_closure,
        direct_analysis_closure=direct_analysis_closure,
    )
    return {
        run_id: WorkflowColdContext(
            config,
            read_seed_parent(
                config, factor_closure=factor_closure, read_bytes=read_bytes
            ),
        )
        for run_id, config in configs.items()
    }
