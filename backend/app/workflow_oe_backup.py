"""031 typed workflow/OE cold links. Metadata is not containment or release proof."""

from packages.factors.contracts import digest
from packages.research_workflow.oe_stage import OEStageDefinition
from packages.research_workflow.rd_checkpoint import RDStageCheckpoint
from packages.research_workflow.structured_plan import (
    RDStructuredGuidancePlan,
    guidance_stage_schedule,
)


def validate_stage_predecessor(stage, definitions, plans, stages, by_stage):
    """Immediate, successful final RD boundary; actual bytes validated separately."""
    plan = plans[stage["workflow_id"]]
    definition = definitions[stage["id"]]
    if (
        type(plan) is not RDStructuredGuidancePlan
        or type(definition) is not OEStageDefinition
    ):
        raise ValueError("Exact explicit terminal OE plan/definition required")
    if stage["ordinal"] != len(guidance_stage_schedule(plan)):
        raise ValueError("OE must be the one terminal scheduled stage")
    previous = stages.get(stage["ordinal"] - 1)
    if previous is None or previous["kind"] != "rd_factor_generation":
        raise ValueError("OE requires immediate final RD stage")
    pair = by_stage.get(previous["id"])
    if pair is None or type(pair[1]) is not RDStageCheckpoint:
        raise ValueError("OE requires actual successful RD checkpoint")
    row, checkpoint = pair
    if (
        definition.plan_sha256,
        definition.structured_template_sha256,
        str(definition.parent_source_stage_id),
        str(definition.predecessor_checkpoint_id),
        definition.predecessor_checkpoint_sha256,
        definition.source_run_id,
        definition.source_attempt_id,
        definition.source_generation,
        definition.factor_version_id,
        definition.snapshot_id,
        definition.snapshot_manifest_sha256,
        definition.analysis_id,
        definition.analysis_result_sha256,
    ) != (
        plan.sha256,
        digest(plan.structured_template),
        previous["id"],
        row["id"],
        row["terminal_sha256"],
        checkpoint.child_run_id,
        checkpoint.attempt_id,
        checkpoint.generation,
        checkpoint.factor_version_id,
        checkpoint.snapshot_id,
        checkpoint.snapshot_manifest_sha256,
        checkpoint.analysis_id,
        checkpoint.analysis_result_sha256,
    ):
        raise ValueError("OE declaration differs from actual final RD checkpoint")


def validate_submission_binding(
    row, metadata, *, config, request, spec, submission, run
):
    """Exact declarations only; callers separately prove source/release/physical bytes."""
    from uuid import UUID

    from backend.app.oe_structured_server_schemas import OEStructuredServerProfile
    from backend.app.research_workflow_backup import _scope
    from packages.factors.contracts import canonical_bytes
    from packages.openevolve_structured.workflow_run import WorkflowStructuredRunConfig

    if (
        type(config) is not WorkflowStructuredRunConfig
        or type(row.get("source_generation")) is not int
        or row["source_generation"] < 1
    ):
        raise ValueError(
            "Exact workflow OE configuration and source generation required"
        )
    own = metadata["indexes"]
    owner, wid = row["owner_id"], row["workflow_id"]
    root = own["research_workflows"].get(wid)
    if root is None or root["owner_id"] != owner:
        raise ValueError("Workflow OE foreign root")
    stage = _scope(own["research_workflow_stages"].get(row["stage_id"]), owner, wid)
    revision = _scope(
        own["research_workflow_revisions"].get(row["input_revision_id"]), owner, wid
    )
    selection = metadata["selections"][revision["id"]]
    definition = metadata["definitions"][stage["id"]]
    plan = metadata["plans"][wid]
    if (
        type(plan) is not RDStructuredGuidancePlan
        or type(definition) is not OEStageDefinition
    ):
        raise ValueError("Workflow OE requires explicit plan and stage types")
    context, template = config.workflow_context, plan.structured_template
    binding = context.inputs.binding
    if (
        stage["kind"] != "structured_factor_execution"
        or stage["ordinal"] != len(guidance_stage_schedule(plan))
        or stage["child_run_id"] != row["run_id"]
        or str(config.search.run_id) != row["run_id"]
        or str(context.child_run_id) != row["run_id"]
        or stage["input_revision_id"] != revision["id"]
        or definition.plan_sha256 != plan.sha256
        or definition.structured_template_sha256 != digest(template)
        or stage["allocation_canonical"] != canonical_bytes(template.budget).decode()
        or stage["allocation_sha256"] != digest(template.budget)
        or config.budget != template.budget
        or config.model_binding != template.model_binding
        or config.runtime != template.runtime
        or request.objective != template.objective
        or request.maximum_mutation_slots != template.maximum_mutation_slots
        or request.seed_parameters.model_dump() != template.seed_parameters.model_dump()
        or request.workflow_context != context
        or context.inputs.selection != selection
        or (
            str(binding.owner_id),
            str(binding.workflow_id),
            str(binding.stage_id),
            str(binding.input_revision_id),
            binding.input_sha256,
            binding.definition_sha256,
        )
        != (
            owner,
            wid,
            stage["id"],
            revision["id"],
            selection.sha256,
            definition.sha256,
        )
        or config.workflow_request_sha256 != request.sha256
        or row["submission_origin"] != "workflow_stage"
        or submission["origin"] != "workflow_stage"
        or row["submission_id"] != submission["id"]
        or (submission["run_id"], submission["owner_id"]) != (row["run_id"], owner)
        or run["id"] != row["run_id"]
        or run["owner_id"] != owner
        or spec["run_id"] != row["run_id"]
        or spec["owner_id"] != owner
        or spec["submission_id"] != submission["id"]
        or submission["request_canonical"] != canonical_bytes(request).decode()
        or spec["request_canonical"] != submission["request_canonical"]
        or spec["request"] != request.model_dump(mode="json")
        or spec["config"] != run["config"]
        or run["config"] != config.model_dump(mode="json")
        or spec["config_canonical"] != canonical_bytes(config).decode()
        or spec["config_sha256"] != digest(config)
        or submission["config_sha256"] != digest(config)
    ):
        raise ValueError("Workflow OE retained declaration differs")
    expected = {
        "request_sha256": request.sha256,
        "config_sha256": digest(config),
        "runtime_profile_sha256": digest(
            OEStructuredServerProfile(runtime=config.runtime)
        ),
        "definition_sha256": definition.sha256,
        "selection_sha256": selection.sha256,
        "resolved_inputs_sha256": context.inputs.sha256,
        "rd_checkpoint_sha256": context.rd_checkpoint_sha256,
        "fixed_inputs_sha256": digest(config.fixed_inputs),
        "rd_stage_id": context.rd_stage_id,
        "rd_checkpoint_id": context.rd_checkpoint_id,
        "source_run_id": context.source_run_id,
        "source_attempt_id": context.source_attempt_id,
        "source_generation": context.source_generation,
        "snapshot_id": context.snapshot_id,
        "factor_version_id": context.factor_version_id,
        "analysis_id": context.analysis_id,
        "release_exposure_id": context.release_exposure_id,
    }
    expected["preflight_sha256"] = digest(
        {
            "schema_version": "mineagent.workflow-oe-preflight.v1",
            "request_sha256": request.sha256,
            "fixed_input_sha256": digest(config.fixed_inputs),
            "runtime_profile_sha256": expected["runtime_profile_sha256"],
            "model_binding_sha256": config.model_binding.sha256,
        }
    )
    if any(
        row[key] != (str(value) if isinstance(value, UUID) else value)
        for key, value in expected.items()
    ):
        raise ValueError("Workflow OE immutable attachment differs")
    if any(
        value["preflight_sha256"] != expected["preflight_sha256"]
        or value["request_sha256"] != request.sha256
        for value in (spec, submission)
    ):
        raise ValueError("Workflow OE actual preflight differs")
    fields = {
        "predecessor_checkpoint_id": "rd_checkpoint_id",
        "predecessor_checkpoint_sha256": "rd_checkpoint_sha256",
        "parent_source_stage_id": "rd_stage_id",
        **{
            key: key
            for key in (
                "source_run_id",
                "source_attempt_id",
                "source_generation",
                "snapshot_id",
                "snapshot_manifest_sha256",
                "factor_version_id",
                "analysis_id",
                "analysis_result_sha256",
                "release_exposure_id",
                "release_provenance_sha256",
            )
        },
    }
    if any(
        getattr(definition, left) != getattr(context, right)
        for left, right in fields.items()
    ):
        raise ValueError("Workflow OE frozen context differs from final RD predecessor")


def build_context(snapshot, maximum_rows=100000, *, metadata=None):
    """Rebuild exact attachments before physical checks; never completion evidence."""
    from backend.app import oe_structured_backup as oe
    from backend.app import research_workflow_backup as workflow
    from backend.app.oe_workflow_submission_schemas import (
        WorkflowStructuredSearchRequest,
    )
    from packages.factors.contracts import canonical_bytes
    from packages.openevolve_structured.workflow_run import (
        WorkflowStructuredRunConfig,
        parse_provider_run,
    )

    if (
        snapshot["research_closure"]["schema_version"],
        snapshot["rd_source_closure"]["schema_version"],
    ) != (
        "mineagent.research-restore-closure.v16",
        "mineagent.rd-source-restore-closure.v11",
    ):
        raise ValueError("Workflow OE requires exact research15/source10")
    if metadata is None:
        metadata = workflow.validate_metadata(
            snapshot["workflow_closure"], maximum_rows, schema=workflow.SCHEMA_V2
        )
    own = metadata["indexes"]
    domain = oe.indexes(
        snapshot["oe_structured_closure"], maximum_rows, schema=oe.SCHEMA_V2
    )
    expected = {
        k
        for k, row in domain["research_runs"].items()
        if row["config"].get("schema_version")
        == "mineagent.oe-structured-provider-run.v2"
    }
    attachments = own["oe_workflow_submissions"]
    stage_runs = {
        s["child_run_id"]
        for s in own["research_workflow_stages"].values()
        if s["kind"] == "structured_factor_execution" and s["child_run_id"] is not None
    }
    if (
        expected != {row["run_id"] for row in attachments.values()}
        or expected != stage_runs
        or len(expected) != len(attachments)
    ):
        raise ValueError("Workflow OE attachment reverse inventory differs")
    rd_checkpoints = metadata["checkpoints"]
    checkpoints = own["research_workflow_checkpoints"]
    publications = snapshot["execution_closure"]["tables"][
        "factor_execution_snapshot_publications"
    ]
    outcomes = snapshot["execution_closure"]["tables"][
        "factor_execution_snapshot_outcomes"
    ]
    direct = snapshot["direct_analysis_closure"]["tables"]
    configs = {}
    for row in attachments.values():
        run_id = row["run_id"]
        run = domain["research_runs"][run_id]
        spec = domain["oe_structured_specs"][run_id]
        submission = domain["oe_structured_submissions"][row["submission_id"]]
        config = parse_provider_run(spec["config_canonical"].encode())
        if type(config) is not WorkflowStructuredRunConfig:
            raise ValueError("Workflow attachment cannot relabel normal user config")
        request = WorkflowStructuredSearchRequest.model_validate_json(
            spec["request_canonical"]
        )
        validate_submission_binding(
            row,
            metadata,
            config=config,
            request=request,
            spec=spec,
            submission=submission,
            run=run,
        )
        context = config.workflow_context
        checkpoint_row = checkpoints.get(str(context.rd_checkpoint_id))
        checkpoint = rd_checkpoints.get(str(context.rd_checkpoint_id))
        if (
            checkpoint_row is None
            or type(checkpoint) is not RDStageCheckpoint
            or (
                checkpoint_row["owner_id"],
                checkpoint_row["workflow_id"],
                checkpoint_row["stage_id"],
                checkpoint_row["terminal_sha256"],
                checkpoint.child_run_id,
                checkpoint.attempt_id,
                checkpoint.generation,
                checkpoint.snapshot_id,
                checkpoint.snapshot_manifest_sha256,
                checkpoint.factor_version_id,
                checkpoint.analysis_id,
                checkpoint.analysis_result_sha256,
            )
            != (
                row["owner_id"],
                row["workflow_id"],
                str(context.rd_stage_id),
                context.rd_checkpoint_sha256,
                context.source_run_id,
                context.source_attempt_id,
                context.source_generation,
                context.snapshot_id,
                context.snapshot_manifest_sha256,
                context.factor_version_id,
                context.analysis_id,
                context.analysis_result_sha256,
            )
        ):
            raise ValueError("Workflow OE actual source checkpoint differs")
        fixed = config.fixed_inputs
        if (
            fixed.snapshot_id,
            fixed.snapshot_manifest_sha256,
            fixed.factor_version_id,
            tuple(fixed.release_receipt_ids),
        ) != (
            context.snapshot_id,
            context.snapshot_manifest_sha256,
            context.factor_version_id,
            (context.release_exposure_id,),
        ):
            raise ValueError("Workflow OE context differs from fixed released input")
        publication = workflow._only(
            publications, id=str(fixed.snapshot_id), owner_id=row["owner_id"]
        )
        outcome = workflow._only(
            outcomes, publication_id=publication["id"], owner_id=row["owner_id"]
        )
        dataset = workflow._only(
            direct["dataset_versions"],
            id=str(fixed.dataset_version_id),
            owner_id=row["owner_id"],
        )
        oe.validate_source_release(
            row["owner_id"],
            config,
            publication,
            outcome,
            dataset,
            direct["data_usage_exposures"],
        )
        release = workflow._only(
            direct["data_usage_exposures"],
            id=str(context.release_exposure_id),
            owner_id=row["owner_id"],
        )
        if digest(release["provenance"]) != context.release_provenance_sha256 or (
            publication["run_id"],
            publication["attempt_id"],
            publication["generation"],
        ) != (
            str(context.source_run_id),
            str(context.source_attempt_id),
            context.source_generation,
        ):
            raise ValueError("Workflow OE release/source scope differs")
        # Parsing preserves canonical input; no current settings or heads are used.
        if canonical_bytes(config).decode() != spec["config_canonical"]:
            raise ValueError("Workflow OE noncanonical retained configuration")
        configs[run_id] = config
    return configs


def validate_consumption(metadata, configs, research_closure, oe_closure, read_bytes):
    """All OE member requests must have exactly one immutable consumption row."""
    from backend.app.research_workflow_backup import _only
    from packages.factors.contracts import canonical_bytes
    from packages.openevolve_structured.provider_contracts import OEChatRequest
    from packages.openevolve_structured.workflow_prompt import (
        validate_workflow_rendered,
    )

    shared, domain = research_closure["tables"], oe_closure["tables"]
    rows = [
        r
        for r in metadata["indexes"]["research_workflow_model_consumption"].values()
        if r["run_id"] in configs
    ]
    members = [
        r for r in domain["oe_structured_provider_transports"] if r["run_id"] in configs
    ]
    expected = {
        (r["run_id"], r["operation_id"], r["transport_id"], r["request_sha256"])
        for r in members
    }
    if (
        len(expected) != len(members)
        or len(rows) != len(members)
        or {
            (r["run_id"], r["operation_id"], r["transport_id"], r["request_sha256"])
            for r in rows
        }
        != expected
    ):
        raise ValueError("Workflow OE exact admitted consumption inventory differs")
    for row in rows:
        if type(row.get("generation")) is not int or row["generation"] < 1:
            raise ValueError("Exact workflow OE consumption generation required")
        config = configs[row["run_id"]]
        context = config.workflow_context
        binding = context.inputs.binding
        if (
            row["owner_id"],
            row["workflow_id"],
            row["stage_id"],
            row["input_revision_id"],
            row["input_sha256"],
        ) != (
            str(binding.owner_id),
            str(binding.workflow_id),
            str(binding.stage_id),
            str(binding.input_revision_id),
            context.inputs.sha256,
        ):
            raise ValueError("Workflow OE consumption selected input differs")
        scope = {
            key: row[key] for key in ("owner_id", "run_id", "attempt_id", "generation")
        }
        member = _only(
            members,
            operation_id=row["operation_id"],
            transport_id=row["transport_id"],
            **scope,
        )
        operation = _only(
            shared["research_model_operations"], id=member["operation_id"], **scope
        )
        transport = _only(
            shared["research_model_transports"], id=member["transport_id"], **scope
        )
        if (
            transport["operation_id"] != operation["id"]
            or transport["ordinal"] != 1
            or operation["protocol"] != "mineagent.oe-structured-provider-chat.v1"
        ):
            raise ValueError("Workflow OE actual request transport differs")
        request = OEChatRequest.model_validate_json(
            canonical_bytes(operation["request"])
        )
        obj = _only(
            shared["research_objects"],
            id=operation["request_object_id"],
            owner_id=row["owner_id"],
        )
        raw = read_bytes(obj["storage_key"], obj["sha256"], obj["size_bytes"])
        if (
            type(raw) is not bytes
            or len(raw) != obj["size_bytes"]
            or raw != canonical_bytes(request)
            or not (
                obj["sha256"]
                == request.sha256
                == operation["request_sha256"]
                == row["request_sha256"]
            )
        ):
            raise ValueError("Workflow OE actual request bytes differ")
        validate_workflow_rendered(
            context, request.messages[0].content, request.messages[1].content
        )


def validate_checkpoints(metadata, configs, research_closure, oe_closure, oe_facts):
    """Final relational commitments after all actual OE physical/domain validators."""
    from backend.app.research_workflow_backup import _only
    from packages.factors.contracts import canonical_bytes
    from packages.research_runtime.budget_contracts import BudgetVector
    from packages.research_workflow.oe_checkpoint import (
        OEStageCheckpoint,
        model_consumption_commitment,
        provider_receipt_commitment,
        terminal_application_commitment,
    )

    shared, domain = research_closure["tables"], oe_closure["tables"]
    own = metadata["indexes"]
    if type(oe_facts) is not dict or set(oe_facts) != {
        "configs",
        "measurements",
        "epochs",
    }:
        raise ValueError("Actual full normal OE closure facts required")
    if any(oe_facts["configs"].get(k) != v for k, v in configs.items()):
        raise ValueError("Workflow OE physical configuration differs")
    for checkpoint in metadata["checkpoints"].values():
        if type(checkpoint) is not OEStageCheckpoint:
            continue
        run_id, owner = str(checkpoint.child_run_id), str(checkpoint.owner_id)
        config = configs.get(run_id)
        if config is None:
            raise ValueError("OE checkpoint has no verified attachment")
        scope = {
            "owner_id": owner,
            "run_id": run_id,
            "attempt_id": str(checkpoint.attempt_id),
            "generation": checkpoint.generation,
        }
        attached = _only(
            own["oe_workflow_submissions"].values(),
            id=str(checkpoint.workflow_submission_id),
            run_id=run_id,
            owner_id=owner,
        )
        stage = own["research_workflow_stages"][attached["stage_id"]]
        context = config.workflow_context
        if (
            str(checkpoint.workflow_id),
            str(checkpoint.stage_id),
            str(checkpoint.input_revision_id),
            checkpoint.input_sha256,
            checkpoint.definition_sha256,
            checkpoint.allocation_sha256,
            str(checkpoint.child_submission_id),
        ) != (
            attached["workflow_id"],
            stage["id"],
            attached["input_revision_id"],
            context.inputs.selection.sha256,
            stage["definition_sha256"],
            stage["allocation_sha256"],
            attached["submission_id"],
        ):
            raise ValueError("OE checkpoint frozen workflow declaration differs")
        run = _only(
            research_closure["external_bindings"]["runs"], id=run_id, owner_id=owner
        )
        attempt = _only(
            research_closure["external_bindings"]["attempts"],
            run_id=run_id,
            owner_id=owner,
        )
        if (
            run["status"] != "succeeded"
            or run["generation"] != checkpoint.generation
            or (
                attempt["id"],
                attempt["generation"],
                attempt["status"],
                attempt["sandbox_stopped"],
            )
            != (scope["attempt_id"], scope["generation"], "succeeded", True)
            or attempt["finished_at"] is None
            or attempt["sandbox_stopped"] is not True
        ):
            raise ValueError("OE checkpoint actual attempt is not closed successfully")

        # Scope every member from the entire run, not just a caller-selected generation.
        def rows(table, source=domain, run_id=run_id, scope=scope):
            values = [r for r in source[table] if r["run_id"] == run_id]
            if any(
                type(r.get("generation")) is not int
                or any(r[k] != v for k, v in scope.items())
                for r in values
            ):
                raise ValueError("OE checkpoint hidden generation/scope inventory")
            return values

        epoch = _only(rows("oe_structured_epochs"), id=str(checkpoint.epoch_id))
        if epoch["controller_admission_id"] != str(checkpoint.controller_admission_id):
            raise ValueError("OE checkpoint epoch/controller differs")
        physical_epoch = oe_facts["epochs"].get(epoch["id"])
        if (
            type(physical_epoch) is not tuple
            or len(physical_epoch) != 2
            or physical_epoch[0].get("completion") is None
            or len(physical_epoch[1]) != 2 * config.search.maximum_mutation_slots + 1
        ):
            raise ValueError("OE checkpoint lacks actual completed physical epoch")
        admissions = rows("research_process_admissions", shared)
        if not 1 <= len(admissions) <= 4 or tuple(
            sorted(r["id"] for r in admissions)
        ) != tuple(str(x) for x in checkpoint.process_admission_ids):
            raise ValueError("OE checkpoint complete physical inventory differs")
        for admission in admissions:
            state = _only(
                rows("research_process_states", shared), admission_id=admission["id"]
            )
            stop = _only(
                rows("research_process_receipts", shared),
                admission_id=admission["id"],
                kind="stop",
            )
            if state["status"] != "stopped":
                raise ValueError("OE checkpoint retains active physical unit")
            if (
                admission["id"] == epoch["controller_admission_id"]
                and stop["payload_sha256"] != checkpoint.controller_stop_sha256
            ):
                raise ValueError("OE checkpoint actual controller stop differs")
        members = rows("oe_structured_provider_transports")
        receipts = rows("oe_structured_provider_receipts")
        if (
            len(members) != config.search.maximum_mutation_slots
            or tuple(sorted(r["id"] for r in members))
            != tuple(str(x) for x in checkpoint.provider_transport_ids)
            or len(receipts) != 3 * len(members)
        ):
            raise ValueError("OE checkpoint complete SDK inventory differs")
        for member in members:
            actual = [r for r in receipts if r["membership_id"] == member["id"]]
            if len(actual) != 3 or {r["kind"] for r in actual} != {
                "intent",
                "started",
                "stopped",
            }:
                raise ValueError("OE checkpoint SDK receipt prefix is not stopped")
        if (
            provider_receipt_commitment(members, receipts)
            != checkpoint.provider_receipts_sha256
        ):
            raise ValueError("OE checkpoint actual SDK receipt commitment differs")
        reservations = rows("research_budget_reservations", shared)
        settlements = rows("research_budget_settlements", shared)
        if (
            not 1 <= len(reservations) <= 256
            or len(reservations) != len(settlements)
            or tuple(sorted(r["id"] for r in reservations))
            != tuple(str(x) for x in checkpoint.reservation_ids)
        ):
            raise ValueError("OE checkpoint complete reservation inventory differs")
        for reservation in reservations:
            settled = _only(settlements, reservation_id=reservation["id"])
            if (
                reservation["state"] != "settled"
                or settled["outcome"] != "conservative_unknown"
                or BudgetVector.model_validate_json(
                    canonical_bytes(reservation["amounts"])
                )
                != BudgetVector.model_validate_json(canonical_bytes(settled["charge"]))
            ):
                raise ValueError("OE checkpoint original holds are not fully settled")
        executions = rows("oe_structured_executions")
        if not 1 <= len(executions) <= 3 or tuple(
            sorted(r["id"] for r in executions)
        ) != tuple(str(r.execution_id) for r in checkpoint.executions):
            raise ValueError("OE checkpoint complete execution inventory differs")
        outcomes = rows("oe_structured_engine_outcomes")
        if len(outcomes) != len(executions):
            raise ValueError("OE checkpoint missing actual execution outcome")
        for ref in checkpoint.executions:
            execution = _only(executions, id=str(ref.execution_id))
            outcome = _only(outcomes, execution_id=execution["id"])
            measurement = oe_facts["measurements"].get(str(ref.candidate_id))
            if (
                measurement is None
                or (
                    execution["candidate_id"],
                    execution["program_version_id"],
                    outcome["candidate_id"],
                    outcome["result_sha256"],
                    outcome["measurement_sha256"],
                )
                != (
                    str(ref.candidate_id),
                    str(ref.program_version_id),
                    str(ref.candidate_id),
                    ref.result_sha256,
                    ref.measurement_sha256,
                )
                or digest(measurement) != ref.measurement_sha256
            ):
                raise ValueError("OE checkpoint actual measured execution differs")
        applications = sorted(
            rows("oe_structured_terminal_applications"), key=lambda r: r["ordinal"]
        )
        if (
            any(type(row.get("ordinal")) is not int for row in applications)
            or len(applications) != config.search.maximum_mutation_slots + 1
            or [r["ordinal"] for r in applications] != list(range(len(applications)))
            or tuple(r["id"] for r in applications)
            != tuple(str(x) for x in checkpoint.terminal_application_ids)
            or terminal_application_commitment(applications)
            != checkpoint.terminal_applications_sha256
        ):
            raise ValueError(
                "OE checkpoint actual terminal transition inventory differs"
            )
        consumption = rows("research_workflow_model_consumption", own_as_tables(own))
        if (
            model_consumption_commitment(consumption)
            != checkpoint.model_consumption_sha256
        ):
            raise ValueError(
                "OE checkpoint actual model consumption commitment differs"
            )


def own_as_tables(own):
    return {key: list(value.values()) for key, value in own.items()}


def partition_rd_consumption(metadata, rd_configs, oe_configs):
    """Partition only verified child sets; never silently filter unknown rows."""
    if set(rd_configs) & set(oe_configs):
        raise ValueError("RD/OE child inventories overlap")
    rows = metadata["indexes"]["research_workflow_model_consumption"]
    if any(
        row["run_id"] not in set(rd_configs) | set(oe_configs) for row in rows.values()
    ):
        raise ValueError("Unknown workflow model consumption origin")
    return {
        **metadata,
        "indexes": {
            **metadata["indexes"],
            "research_workflow_model_consumption": {
                key: row for key, row in rows.items() if row["run_id"] in rd_configs
            },
        },
    }
