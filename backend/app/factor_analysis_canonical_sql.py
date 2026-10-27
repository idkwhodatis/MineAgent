"""022 lossless raw-document validation; NOT a float reserialization algorithm.

Number tokens are retained byte-for-byte, finite and within float8 representation
when fractional/exponential. The typed host validator separately requires Python
canonical roundtrip; SQL never hashes a lossy JSONB float rendering. JSON grammar,
duplicate keys, ordering, escaping, compact layout, depth and byte bounds are
checked before exact original-byte SHA256 and typed JSONB semantic comparison.
"""

RAW_SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_analysis_raw_json(v json, depth integer DEFAULT 0)
RETURNS text LANGUAGE plpgsql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
DECLARE result text; item record; token text; number double precision; first_item boolean:=true;
BEGIN
 IF current_setting('server_encoding')<>'UTF8' OR depth<0 OR depth>24
 THEN RAISE EXCEPTION 'Analysis raw JSON encoding/depth'; END IF;
 CASE json_typeof(v)
 WHEN 'object' THEN
  IF (SELECT count(*) FROM json_each(v))<>(SELECT count(DISTINCT key COLLATE "C") FROM json_each(v))
  THEN RAISE EXCEPTION 'Analysis raw JSON duplicate key'; END IF;
  result:='{';
  FOR item IN SELECT key,value FROM json_each(v) ORDER BY key COLLATE "C" LOOP
   IF NOT first_item THEN result:=result||','; END IF; first_item:=false;
   result:=result||@SCHEMA@.mineagent_rd_canonical_json(to_jsonb(item.key),true)||':'||
    @SCHEMA@.mineagent_analysis_raw_json(item.value,depth+1);
   IF octet_length(result)>1048576 THEN RAISE EXCEPTION 'Analysis raw JSON size'; END IF;
  END LOOP;
  result:=result||'}';
 WHEN 'array' THEN
  result:='[';
  FOR item IN SELECT value FROM json_array_elements(v) WITH ORDINALITY a(value,ord) ORDER BY ord LOOP
   IF NOT first_item THEN result:=result||','; END IF; first_item:=false;
   result:=result||@SCHEMA@.mineagent_analysis_raw_json(item.value,depth+1);
   IF octet_length(result)>1048576 THEN RAISE EXCEPTION 'Analysis raw JSON size'; END IF;
  END LOOP;
  result:=result||']';
 WHEN 'string' THEN result:=@SCHEMA@.mineagent_rd_canonical_json(to_jsonb(v#>>'{}'),true);
 WHEN 'number' THEN
  token:=v::text;
  IF token !~ '^-?(0|[1-9][0-9]*)(\.[0-9]+)?(e[+-]?[0-9]+)?$'
  THEN RAISE EXCEPTION 'Analysis raw numeric token'; END IF;
  IF token ~ '[.e]' THEN
   number:=token::double precision;
   IF number::text IN ('NaN','Infinity','-Infinity') OR (number=0 AND token::numeric<>0)
   THEN RAISE EXCEPTION 'Analysis raw numeric not finite/representable'; END IF;
  END IF;
  result:=token;
 WHEN 'boolean' THEN result:=v::text;
 WHEN 'null' THEN result:='null';
 ELSE RAISE EXCEPTION 'Analysis raw JSON kind';
 END CASE;
 IF octet_length(result)>1048576 THEN RAISE EXCEPTION 'Analysis raw JSON size'; END IF;
 RETURN result;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_analysis_raw_commitment(raw text, payload jsonb, sha text, maximum integer)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
BEGIN
 IF maximum NOT BETWEEN 1 AND 1048576 OR octet_length(raw) NOT BETWEEN 1 AND maximum
 OR sha !~ '^[a-f0-9]{64}$' OR raw<>@SCHEMA@.mineagent_analysis_raw_json(raw::json)
 OR raw::jsonb IS DISTINCT FROM payload
 OR sha<>encode(sha256(convert_to(raw,'UTF8')),'hex') THEN RETURN false; END IF;
 RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;
"""
