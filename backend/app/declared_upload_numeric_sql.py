"""030 narrow signed child-exit grammar; global PID/counter rules stay unchanged.

The historical028 function and every rule literal are byte-pinned. Only the
rejection child's already validated signed field is substituted for structural
validation. Original payload bytes still reach all downstream commitment,
protected-frame, relational and physical checks unchanged.
"""

import hashlib

from backend.app.openevolve_revision_ddl import _function, historical

PREDECESSOR_SHA256 = "c7f3b0ab6b405e6456a527da796ef9d1010a4acebbe36fc1a8aca74680379090"
RETURN = " RETURN mineagent_rd_schema(v,rule,coalesce(rule->'$defs','{}'::jsonb),0);"
SIGNED_REJECTION = """ IF kind='rejection' THEN
   IF jsonb_typeof(v->'child_exit_code') IS DISTINCT FROM 'number'
    OR (v->'child_exit_code')::text !~ '^-?(0|[1-9][0-9]*)$'
    OR (v->>'child_exit_code')::numeric < -64
    OR (v->>'child_exit_code')::numeric > 255 THEN RETURN false; END IF;
   -- Only this exact field is signed. Validate every other field using the
   -- unchanged historical grammar, including all closed keys and literal tags.
   RETURN mineagent_rd_schema(jsonb_set(v,'{child_exit_code}','0'::jsonb,false),
     rule,coalesce(rule->'$defs','{}'::jsonb),0);
 END IF;
"""


def render_downgrade_sql():
    source = _function(
        historical("028", "UPGRADE_SQL"), "mineagent_numeric_rejection_payload"
    )
    if hashlib.sha256(source.encode()).hexdigest() != PREDECESSOR_SHA256:
        raise ValueError("Historical028 numeric rejection predicate changed")
    if source.count(RETURN) != 1:
        raise ValueError("Exact historical structural return required")
    return source


def render_upgrade_sql():
    return render_downgrade_sql().replace(RETURN, SIGNED_REJECTION + RETURN)
