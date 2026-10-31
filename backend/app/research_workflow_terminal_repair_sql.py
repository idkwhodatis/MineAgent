"""032a additive checkpoint result-family repair; historical SQL is immutable."""

from hashlib import sha256

from backend.app.openevolve_revision_ddl import _function, historical

PINS = {
    "031": "67c916d766057c38db8f9e275de1936dcc3edf7a9803931dff597ed6b912ec84",
    "032": "0ccef3532943be981d5238168dee0ad411a31778f9d2e36cc1dd768de3944da9",
}


def previous_function():
    sources = {revision: historical(revision, "UPGRADE_SQL") for revision in PINS}
    for revision, expected in PINS.items():
        if sha256(sources[revision].encode()).hexdigest() != expected:
            raise ValueError("Exact workflow predecessor changed")
    if "FUNCTION @SCHEMA@.mineagent_workflow_checkpoint(" in sources["032"]:
        raise ValueError("Unexpected successor checkpoint override")
    return _function(sources["031"], "mineagent_workflow_checkpoint")


BINDINGS = """    AND ev.attempt_id=aid AND ev.generation=gen
    AND eo.owner_id=o AND eo.run_id=rid AND eo.attempt_id=aid AND eo.generation=gen
    AND eo.generation_start_id=g.id AND eo.snapshot_id=snap.id
    AND eo.snapshot_manifest_sha256=snap.manifest_sha256
    AND eo.snapshot_input_sha256=snap.manifest->>'input_sha256'
    AND eo.snapshot_output_sha256=snap.manifest->>'output_rows_sha256'
    AND snap.attempt_id=aid AND snap.generation=gen AND snap.start_id=g.id
    AND EXISTS(SELECT 1 FROM factor_generation_boundaries b
     JOIN factor_controller_launches l ON l.id=b.controller_launch_id
     AND l.owner_id=b.owner_id AND l.run_id=b.run_id
     AND l.attempt_id=b.attempt_id AND l.generation=b.generation
     WHERE b.owner_id=o AND b.run_id=rid AND b.attempt_id=aid AND b.generation=gen
     AND b.id=eo.boundary_id AND b.start_id=g.id
     AND b.controller_launch_id=eo.controller_launch_id
     AND b.logical_step=g.logical_step AND b.boundary='returned'
     AND b.next_logical_step IS NULL AND l.admission_id::text=v->>'controller_admission_id')
"""


def render_upgrade_sql():
    old = previous_function()
    join = "JOIN factor_evaluation_outcomes eo ON eo.evaluation_id=ev.id"
    marker = "    AND snap.id::text=v->>'snapshot_id'"
    if old.count(join) != 1 or old.count(marker) != 1:
        raise ValueError("Exact checkpoint result binding changed")
    old_receipts = "SELECT array_agg(kind ORDER BY kind) FROM rd_provider_transport_receipts WHERE transport_id=t.id"
    if old.count(old_receipts) != 1:
        raise ValueError("Exact provider receipt predicate changed")
    old = old.replace(
        old_receipts,
        "SELECT array_agg(pr.kind ORDER BY pr.kind) FROM rd_provider_transport_receipts pr WHERE pr.transport_id=t.id",
    )
    return (
        old.replace(
            join,
            "JOIN factor_execution_evaluation_outcomes eo ON eo.evaluation_id=ev.id",
        ).replace(marker, BINDINGS + marker)
        + """
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM @SCHEMA@.research_workflow_checkpoints c
 JOIN @SCHEMA@.research_workflow_stages s ON s.id=c.stage_id
 AND s.owner_id=c.owner_id AND s.workflow_id=c.workflow_id
 WHERE s.kind='rd_factor_generation'
 AND @SCHEMA@.mineagent_workflow_checkpoint(c.owner_id,c.workflow_id,c.id) IS NOT TRUE) THEN
  RAISE EXCEPTION 'Retained RD terminal checkpoint is not representable after032a';
 END IF;
END $$;
"""
    )


def render_downgrade_sql():
    # A completed modern RD checkpoint cannot be reinterpreted by the old family.
    return """DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM @SCHEMA@.research_workflow_checkpoints c
 JOIN @SCHEMA@.research_workflow_stages s ON s.id=c.stage_id
 AND s.owner_id=c.owner_id AND s.workflow_id=c.workflow_id
 WHERE s.kind='rd_factor_generation') THEN
  RAISE EXCEPTION 'Cannot downgrade retained RD terminal checkpoints';
 END IF;
END $$;
""" + previous_function()
