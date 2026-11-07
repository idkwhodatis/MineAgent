"""Reduce interpreter overhead without skipping metadata or graph validation.

Canonical containers inline the unchanged common scalar grammar. The schema
interpreter and every caller of the serializer remain unchanged.
No result is cached and no persisted row, deadline, or constraint is changed.
Historical migration literals remain the source of the exact predecessor body.
"""

from backend.app.rdagent_provider_revision import changed, function, literal


def canonical_original():
    return function(
        literal("018_rd_source_persistence.py"), "mineagent_rd_canonical_json"
    )


def schema_original():
    return function(
        literal("025_structured_factor_strategy.py"), "mineagent_rd_schema"
    )


DEPTH_CHECK = """     IF depth>=24 THEN
       RAISE EXCEPTION 'RD canonical metadata depth exceeded' USING ERRCODE='22023';
     END IF;
"""

SCALAR_VALUE = """     CASE jsonb_typeof(item.value)
     WHEN 'string' THEN
       t:=item.value#>>'{}';
       IF octet_length(t)>1048576 THEN
         RAISE EXCEPTION 'RD canonical metadata byte bound exceeded' USING ERRCODE='22023';
       END IF;
       IF octet_length(t)=char_length(t) AND position(chr(127) in t)=0 THEN
         child:=pg_catalog.to_json(t)::text;
       ELSE child:=@SCHEMA@.mineagent_rd_canonical_json(item.value,ascii_only,depth+1);
       END IF;
     WHEN 'number' THEN
       child:=item.value::text;
       IF child !~ '^(0|-?[1-9][0-9]*)$' THEN
         RAISE EXCEPTION 'RD canonical metadata requires integer tokens' USING ERRCODE='22023';
       END IF;
     WHEN 'boolean', 'null' THEN child:=item.value::text;
     ELSE child:=@SCHEMA@.mineagent_rd_canonical_json(item.value,ascii_only,depth+1);
     END CASE;
     IF octet_length(child)>1048576 THEN
       RAISE EXCEPTION 'RD canonical metadata byte bound exceeded' USING ERRCODE='22023';
     END IF;
"""


def canonical_sql():
    sql = changed(
        canonical_original(),
        "DECLARE result text; item record; t text;",
        "DECLARE result text; item record; t text; child text; key_text text;",
    )
    sql = changed(
        sql,
        """     result:=result||@SCHEMA@.mineagent_rd_canonical_json(to_jsonb(item.key),ascii_only,depth+1)
       ||':'||@SCHEMA@.mineagent_rd_canonical_json(item.value,ascii_only,depth+1);
""",
        DEPTH_CHECK
        + """     t:=item.key;
     IF octet_length(t)>1048576 THEN
       RAISE EXCEPTION 'RD canonical metadata byte bound exceeded' USING ERRCODE='22023';
     END IF;
     IF octet_length(t)=char_length(t) AND position(chr(127) in t)=0 THEN
       key_text:=pg_catalog.to_json(t)::text;
     ELSE key_text:=@SCHEMA@.mineagent_rd_canonical_json(to_jsonb(item.key),ascii_only,depth+1);
     END IF;
     IF octet_length(key_text)>1048576 THEN
       RAISE EXCEPTION 'RD canonical metadata byte bound exceeded' USING ERRCODE='22023';
     END IF;
"""
        + SCALAR_VALUE
        + "     result:=result||key_text||':'||child;\n",
    )
    return changed(
        sql,
        "     result:=result||@SCHEMA@.mineagent_rd_canonical_json(item.value,ascii_only,depth+1);\n",
        DEPTH_CHECK + SCALAR_VALUE + "     result:=result||child;\n",
    )


def render_upgrade_sql():
    return canonical_sql()


def render_downgrade_sql():
    return canonical_original()
