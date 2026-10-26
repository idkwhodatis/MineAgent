"""Bounded canonical metadata serializer for the RD persistence revision.

This is NOT a general Python/JSON serialization equivalence claim. Input must
already satisfy the versioned config/context/request grammar: JSON objects,
arrays, strings, booleans, null and integer tokens only. PostgreSQL JSONB has
already erased duplicate keys and some numeric lexical distinctions (1e0 -> 1);
raw codec validation remains mandatory. JSONB cannot hold U+0000 or unpaired
surrogates, so those inputs fail at PostgreSQL JSON parsing, before this helper.

UTF-8 database required; C collation orders UTF-8 keys by Unicode code point,
matching Python sort_keys=True. Output is <=1 MiB at depth <=24. No registration,
migration, execution authority, floats or native binary parsing is added here.
"""

import re

MAX_BYTES = 1_048_576
MAX_DEPTH = 24


def render_sql(schema="public"):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
        raise ValueError("Unsafe schema")
    return _SQL.replace("@S@", schema)


_SQL = r"""
CREATE OR REPLACE FUNCTION @S@.mineagent_rd_canonical_json(
 v jsonb, ascii_only boolean, depth integer DEFAULT 0)
RETURNS text LANGUAGE plpgsql IMMUTABLE STRICT
SET search_path=pg_catalog AS $$
DECLARE result text; item record; t text;
        first_item boolean := true; slash text := chr(92);
BEGIN
 IF current_setting('server_encoding') <> 'UTF8' THEN
   RAISE EXCEPTION 'RD canonical metadata requires UTF8' USING ERRCODE='22023';
 END IF;
 IF depth<0 OR depth>24 THEN
   RAISE EXCEPTION 'RD canonical metadata depth exceeded' USING ERRCODE='22023';
 END IF;
 CASE jsonb_typeof(v)
 WHEN 'object' THEN
   result:='{';
   FOR item IN SELECT key,value FROM jsonb_each(v) ORDER BY key COLLATE "C" LOOP
     IF NOT first_item THEN result:=result||','; END IF;
     first_item:=false;
     result:=result||@S@.mineagent_rd_canonical_json(to_jsonb(item.key),ascii_only,depth+1)
       ||':'||@S@.mineagent_rd_canonical_json(item.value,ascii_only,depth+1);
     IF octet_length(result)>1048576 THEN
       RAISE EXCEPTION 'RD canonical metadata byte bound exceeded' USING ERRCODE='22023';
     END IF;
   END LOOP;
   result:=result||'}';
 WHEN 'array' THEN
   result:='[';
   FOR item IN SELECT value FROM jsonb_array_elements(v) WITH ORDINALITY a(value,ord) ORDER BY ord LOOP
     IF NOT first_item THEN result:=result||','; END IF;
     first_item:=false;
     result:=result||@S@.mineagent_rd_canonical_json(item.value,ascii_only,depth+1);
     IF octet_length(result)>1048576 THEN
       RAISE EXCEPTION 'RD canonical metadata byte bound exceeded' USING ERRCODE='22023';
     END IF;
   END LOOP;
   result:=result||']';
 WHEN 'string' THEN
   result:=chr(34); t:=v#>>'{}';
   IF octet_length(t)>1048576 THEN
     RAISE EXCEPTION 'RD canonical metadata byte bound exceeded' USING ERRCODE='22023';
   END IF;
   SELECT chr(34)||coalesce(string_agg(
     CASE z.p
       WHEN 8 THEN slash||'b' WHEN 9 THEN slash||'t'
       WHEN 10 THEN slash||'n' WHEN 12 THEN slash||'f'
       WHEN 13 THEN slash||'r' WHEN 34 THEN slash||chr(34)
       WHEN 92 THEN slash||slash
       ELSE CASE
         WHEN z.p<32 OR (ascii_only AND z.p>=127 AND z.p<=65535)
           THEN slash||'u'||lpad(to_hex(z.p),4,'0')
         WHEN ascii_only AND z.p>65535
           THEN slash||'u'||lpad(to_hex(55296+(z.p-65536)/1024),4,'0')
             ||slash||'u'||lpad(to_hex(56320+(z.p-65536)%1024),4,'0')
         ELSE z.c END
       END, '' ORDER BY z.ord), '')||chr(34)
   INTO result
   FROM (SELECT chars.c, chars.ord, ascii(chars.c) AS p
         FROM regexp_split_to_table(t,'') WITH ORDINALITY AS chars(c,ord) WHERE chars.c<>'') z;
 WHEN 'number' THEN
   result:=v::text;
   IF result !~ '^(0|-?[1-9][0-9]*)$' THEN
     RAISE EXCEPTION 'RD canonical metadata requires integer tokens' USING ERRCODE='22023';
   END IF;
 WHEN 'boolean' THEN result:=v::text;
 WHEN 'null' THEN result:='null';
 ELSE
   RAISE EXCEPTION 'Unsupported RD canonical metadata type' USING ERRCODE='22023';
 END CASE;
 IF octet_length(result)>1048576 THEN
   RAISE EXCEPTION 'RD canonical metadata byte bound exceeded' USING ERRCODE='22023';
 END IF;
 RETURN result;
END $$;
"""
