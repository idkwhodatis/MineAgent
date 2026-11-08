"""Exact scalar fast paths inside the frozen lossless raw-JSON serializer.

No graph checks, callers, persisted metadata, or cross-invocation state change.
Complex scalars and every boundary case retain the predecessor implementation.
"""

from backend.app.rdagent_provider_revision import changed, function, literal

RAW_NAME = "mineagent_structured_raw_json"
CANONICAL_KEY = "@SCHEMA@.mineagent_rd_canonical_json(to_jsonb(key),true)"
CHILD = "@SCHEMA@.mineagent_structured_raw_json(value,depth+1)"


def raw_original():
    return function(literal("025_structured_factor_strategy.py"), RAW_NAME)


def ascii_string(expression, fallback):
    # For UTF8 ASCII text, to_json emits precisely the frozen ASCII canonical
    # string, except DEL (which must use the Unicode-escaping predecessor).
    # Checking escaped length also implies the input-byte bound for ASCII.
    encoded = f"pg_catalog.to_json({expression})::text"
    return f"""CASE WHEN octet_length({expression})=char_length({expression})
      AND position(chr(127) in {expression})=0
      AND octet_length({encoded})<=1048576
    THEN {encoded} ELSE {fallback} END"""


def scalar_child():
    string = ascii_string("value#>>'{}'", CHILD)
    return f"""CASE WHEN depth<24 AND octet_length(value::text)<=16777216 THEN
      CASE json_typeof(value)
      WHEN 'string' THEN {string}
      WHEN 'number' THEN CASE WHEN value::text ~ '^-?(0|[1-9][0-9]*)$'
        THEN value::text ELSE {CHILD} END
      WHEN 'boolean' THEN value::text
      WHEN 'null' THEN 'null'
      ELSE {CHILD} END
    ELSE {CHILD} END"""


def raw_sql():
    sql = raw_original()
    if sql.count(CHILD) != 2:
        raise ValueError("Exact frozen raw-JSON child inventory required")
    sql = sql.replace(CHILD, scalar_child())
    return changed(sql, CANONICAL_KEY, ascii_string("key", CANONICAL_KEY))


def render_upgrade_sql():
    return raw_sql()


def render_downgrade_sql():
    return raw_original()
