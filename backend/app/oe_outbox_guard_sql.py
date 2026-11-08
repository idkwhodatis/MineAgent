"""Retain workflow OE queue evidence without rechecking delivery-only updates.

The existing graph requires a generation-one outbox row even after delivery.
Only deleting a row or changing its run/generation can remove that evidence.
Reuse the installed OLD+NEW reverse checker, including its READ COMMITTED and
parent FOR UPDATE NOWAIT fences. This does not cache or replace any predicate.
"""

TRIGGERS = ("oes032f_outbox_delete", "oes032f_outbox_identity")


def render_preflight_sql():
    """Run under the migration's quiescent table locks before adding triggers."""
    return """DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM @SCHEMA@.oe_workflow_submissions q
  WHERE NOT EXISTS(SELECT 1 FROM @SCHEMA@.outbox_events
   WHERE run_id=q.run_id AND generation=1))
 THEN RAISE EXCEPTION 'Workflow OE retained generation-one outbox evidence missing'; END IF;
END $$;"""


def render_upgrade_sql():
    return (
        render_preflight_sql()
        + "\n"
        + """CREATE CONSTRAINT TRIGGER oes032f_outbox_delete
AFTER DELETE ON @SCHEMA@.outbox_events
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
EXECUTE FUNCTION @SCHEMA@.mineagent_oes_consistent();
CREATE CONSTRAINT TRIGGER oes032f_outbox_identity
AFTER UPDATE ON @SCHEMA@.outbox_events
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
WHEN (OLD.run_id IS DISTINCT FROM NEW.run_id
 OR OLD.generation IS DISTINCT FROM NEW.generation)
EXECUTE FUNCTION @SCHEMA@.mineagent_oes_consistent();"""
    )


def render_downgrade_sql():
    return "\n".join(
        f"DROP TRIGGER {name} ON @SCHEMA@.outbox_events;" for name in reversed(TRIGGERS)
    )
