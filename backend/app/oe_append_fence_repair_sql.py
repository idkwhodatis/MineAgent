"""032b exact additive repair of the OE append run/attempt fence.

ResearchRun has no terminal timestamp; its status and the actual current
ExecutionAttempt's finished_at/lease/status remain the authoritative guards.
Historical migration renderers and literals are deliberately unchanged.
"""

from hashlib import sha256

from backend.app.openevolve_revision_ddl import _function, historical

PINS = {
    "031": "67c916d766057c38db8f9e275de1936dcc3edf7a9803931dff597ed6b912ec84",
    "032": "0ccef3532943be981d5238168dee0ad411a31778f9d2e36cc1dd768de3944da9",
    "032a": "174de6e6032ef3bbcccbfb5a3267e3ba0ec7621876e52a3e1c0cb4c681dc59fe",
}
MARKER = "IF r.status IS DISTINCT FROM 'running' OR r.finished_at IS NOT NULL"


def previous_function():
    sources = {revision: historical(revision, "UPGRADE_SQL") for revision in PINS}
    for revision, expected in PINS.items():
        if sha256(sources[revision].encode()).hexdigest() != expected:
            raise ValueError("Exact OE append predecessor changed")
    for revision in ("032", "032a"):
        if "FUNCTION @SCHEMA@.mineagent_oes_append(" in sources[revision]:
            raise ValueError("Unexpected OE append successor override")
    return _function(sources["031"], "mineagent_oes_append")


def render_upgrade_sql():
    old = previous_function()
    if old.count(MARKER) != 1 or old.count("r.finished_at") != 1:
        raise ValueError("Exact missing run field guard required")
    return old.replace(MARKER, "IF r.status IS DISTINCT FROM 'running'")


def render_downgrade_sql():
    return previous_function()
