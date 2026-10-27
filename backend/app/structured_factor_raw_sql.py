"""025 bounded lossless raw JSON commitment; historical022 codec is unchanged.

Uses aggregate assembly for arrays/objects rather than quadratic text appends.
Number tokens remain original, with finite float8 checks for fractional tokens.
"""

RAW_SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_structured_raw_json(v json, depth integer DEFAULT 0)
RETURNS text LANGUAGE plpgsql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
DECLARE result text; token text; number double precision;
BEGIN
 IF current_setting('server_encoding')<>'UTF8' OR depth<0 OR depth>24
 OR octet_length(v::text)>16777216 THEN RAISE EXCEPTION 'Structured raw bound'; END IF;
 CASE json_typeof(v)
 WHEN 'object' THEN
  IF (SELECT count(*) FROM json_each(v))<>(SELECT count(DISTINCT key COLLATE "C") FROM json_each(v))
  THEN RAISE EXCEPTION 'Structured duplicate key'; END IF;
  SELECT '{'||coalesce(string_agg(@SCHEMA@.mineagent_rd_canonical_json(to_jsonb(key),true)||':'||
    @SCHEMA@.mineagent_structured_raw_json(value,depth+1),',' ORDER BY key COLLATE "C"),'')||'}'
  INTO result FROM json_each(v);
 WHEN 'array' THEN
  SELECT '['||coalesce(string_agg(@SCHEMA@.mineagent_structured_raw_json(value,depth+1),',' ORDER BY ord),'')||']'
  INTO result FROM json_array_elements(v) WITH ORDINALITY a(value,ord);
 WHEN 'string' THEN result:=@SCHEMA@.mineagent_rd_canonical_json(to_jsonb(v#>>'{}'),true);
 WHEN 'number' THEN
  token:=v::text;
  IF token !~ '^-?(0|[1-9][0-9]*)(\.[0-9]+)?(e[+-]?[0-9]+)?$'
  THEN RAISE EXCEPTION 'Structured raw number'; END IF;
  IF token ~ '[.e]' THEN
   number:=token::double precision;
   IF number::text IN ('NaN','Infinity','-Infinity') OR (number=0 AND token::numeric<>0)
   THEN RAISE EXCEPTION 'Structured nonfinite/unrepresentable number'; END IF;
  END IF;
  result:=token;
 WHEN 'boolean' THEN result:=v::text;
 WHEN 'null' THEN result:='null';
 ELSE RAISE EXCEPTION 'Structured raw kind';
 END CASE;
 IF octet_length(result)>16777216 THEN RAISE EXCEPTION 'Structured raw size'; END IF;
 RETURN result;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_structured_raw_commitment(raw text,payload jsonb,sha text,maximum integer)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
BEGIN
 IF maximum NOT BETWEEN 1 AND 16777216 OR octet_length(raw) NOT BETWEEN 1 AND maximum
 OR sha !~ '^[a-f0-9]{64}$' OR raw<>@SCHEMA@.mineagent_structured_raw_json(raw::json)
 OR raw::jsonb IS DISTINCT FROM payload
 OR sha<>encode(sha256(convert_to(raw,'UTF8')),'hex') THEN RETURN false; END IF;
 RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;
"""
