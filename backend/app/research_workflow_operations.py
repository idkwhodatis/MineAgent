"""Exact029 backup/retention composition; no schema or runtime activation."""

from backend.app import research_input_backup, research_workflow_backup

WORKFLOW_CAP = "mineagent.research-workflow-closure.v1"
WORKFLOW_CAP_V2 = "mineagent.research-workflow-closure.v2"
PAIR_V2 = (
    "mineagent.research-restore-closure.v16",
    "mineagent.rd-source-restore-closure.v11",
)
INPUT_CAP = "mineagent.research-inputs.v1"
PAIR = (
    "mineagent.research-restore-closure.v14",
    "mineagent.rd-source-restore-closure.v9",
)


PAIRS = {
    PAIR,
    (
        "mineagent.research-restore-closure.v15",
        "mineagent.rd-source-restore-closure.v10",
    ),
}


def enabled(capabilities):
    caps = (
        getattr(capabilities, "workflow_schema", None),
        getattr(capabilities, "research_input_schema", None),
    )
    pair = (
        getattr(capabilities, "research_closure_schema", None),
        getattr(capabilities, "rd_source_closure_schema", None),
    )
    if caps == (None, None):
        if pair[0] in {p[0] for p in PAIRS | {PAIR_V2}} or pair[1] in {
            p[1] for p in PAIRS | {PAIR_V2}
        }:
            raise ValueError(
                "Workflow successor requires both source and graph closures"
            )
        return False
    if not (
        (caps == (WORKFLOW_CAP, INPUT_CAP) and pair in PAIRS)
        or (caps == (WORKFLOW_CAP_V2, INPUT_CAP) and pair == PAIR_V2)
    ):
        raise ValueError("Exact workflow/source successor capability tuple required")
    return True


def serialized_schema(capabilities):
    if not enabled(capabilities):
        return None
    return (
        research_workflow_backup.SCHEMA_V2
        if capabilities.workflow_schema == WORKFLOW_CAP_V2
        else research_workflow_backup.SCHEMA
    )


def closures_for(snapshot, capabilities):
    present = ("workflow_closure" in snapshot, "research_input_closure" in snapshot)
    active = enabled(capabilities)
    if present != (active, active):
        raise ValueError("Workflow/source closure presence differs")
    if not active:
        return None
    workflow, inputs = snapshot["workflow_closure"], snapshot["research_input_closure"]
    if (
        type(workflow) is not dict
        or type(inputs) is not dict
        or workflow.get("schema_version") != serialized_schema(capabilities)
        or inputs.get("schema_version") != research_input_backup.SCHEMA
    ):
        raise ValueError("Workflow/source serialized schema differs")
    return workflow, inputs


def read_snapshot(connection, capabilities, limits):
    if not enabled(capabilities):
        return {}
    result = {
        "workflow_closure": research_workflow_backup.read_closure(
            connection,
            limits.max_files,
            maximum_bytes=limits.max_manifest_bytes,
            schema=serialized_schema(capabilities),
        ),
        "research_input_closure": research_input_backup.read_closure(
            connection, limits.max_files, maximum_bytes=limits.max_manifest_bytes
        ),
    }
    closures_for(result, capabilities)
    return result


def manifest_keys(snapshot, capabilities):
    values = closures_for(snapshot, capabilities)
    return set() if values is None else research_input_backup.manifest_keys(values[1])


def prepare_context(snapshot, capabilities, read_bytes, maximum_rows):
    values = closures_for(snapshot, capabilities)
    if values is None:
        return None
    return research_workflow_backup.build_workflow_context(
        values[0],
        rd_source_closure=snapshot["rd_source_closure"],
        direct_analysis_closure=snapshot["direct_analysis_closure"],
        factor_closure=snapshot["factor_closure"],
        read_bytes=read_bytes,
        maximum_rows=maximum_rows,
    )


def validate_snapshot(
    snapshot, capabilities, read_bytes, maximum_rows, *, inventory, oe_facts=None
):
    values = closures_for(snapshot, capabilities)
    if values is None:
        return None
    # Mandatory final pass after central process/SDK/source/snapshot/analysis,
    # original manual versions and inherited normal/failure validators.
    return research_workflow_backup.validate_closure(
        values[0],
        input_closure=values[1],
        research_closure=snapshot["research_closure"],
        rd_source_closure=snapshot["rd_source_closure"],
        execution_closure=snapshot["execution_closure"],
        direct_analysis_closure=snapshot["direct_analysis_closure"],
        factor_closure=snapshot["factor_closure"],
        steer_closure=snapshot["rd_steer_closure"],
        read_bytes=read_bytes,
        inventory=inventory,
        maximum_rows=maximum_rows,
        **(
            {"oe_closure": snapshot["oe_structured_closure"], "oe_facts": oe_facts}
            if serialized_schema(capabilities) == research_workflow_backup.SCHEMA_V2
            else {}
        ),
    )
