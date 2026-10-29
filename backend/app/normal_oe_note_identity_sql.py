"""032 note-only retained dataset identity normalization, independent of DB locale.

Python's locale-independent Unicode upper/strip semantics are frozen into the new
migration. No existing ledger row or historical SQL function is reinterpreted.
"""

import json
import unicodedata


def render_identity_sql():
    if unicodedata.unidata_version != "15.0.0":
        raise ValueError("Reviewed Python Unicode identity semantics required")
    changes = {
        chr(i): chr(i).upper()
        for i in range(0x110000)
        if not 0xD800 <= i <= 0xDFFF and chr(i).upper() != chr(i)
    }
    mapping = json.dumps(changes, ensure_ascii=True, separators=(",", ":"))
    whitespace = [i for i in range(0x110000) if chr(i).isspace()]
    strip_chars = "||".join(f"chr({i})" for i in whitespace)
    return r"""
CREATE FUNCTION @SCHEMA@.mineagent_normal_note_upper(value text,trim_space boolean) RETURNS text
LANGUAGE plpgsql IMMUTABLE SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE mapping constant jsonb:='__MAPPING__'::jsonb; output text;
BEGIN
 IF value IS NULL THEN RETURN NULL; END IF;
 IF trim_space THEN value:=btrim(value,__WHITESPACE__); END IF;
 SELECT coalesce(string_agg(coalesce(mapping->>substr(value,i,1),substr(value,i,1)),'' ORDER BY i),'')
 INTO output FROM generate_series(1,length(value)) i;
 RETURN output;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_normal_note_dataset_identities(did uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE d dataset_versions%ROWTYPE; result jsonb;
BEGIN
 SELECT * INTO STRICT d FROM dataset_versions WHERE id=did;
 WITH raw AS (
  SELECT nullif(x.value->>'listing_id','') AS listing_id,
   @SCHEMA@.mineagent_normal_note_upper(d.market,false) AS market,
   coalesce(nullif(x.value->>'symbol',''),nullif(l.symbol,''),
     nullif(d.manifest->'evaluator_symbol_mapping'->>(x.value->>'listing_id'),'')) AS symbol
  FROM jsonb_array_elements(coalesce(d.manifest->'listings','[]'::jsonb)) x
  LEFT JOIN listings l ON l.id::text=x.value->>'listing_id'
 ), normalized AS (
  SELECT DISTINCT listing_id COLLATE "C" listing_id,market COLLATE "C" market,
   @SCHEMA@.mineagent_normal_note_upper(symbol,true) COLLATE "C" symbol FROM raw
 ) SELECT jsonb_agg(jsonb_build_object('listing_id',listing_id,'market',market,'symbol',symbol)
 ORDER BY coalesce(listing_id,'') COLLATE "C",market COLLATE "C",coalesce(symbol,'') COLLATE "C")
 INTO result FROM normalized;
 RETURN coalesce(result,jsonb_build_array(jsonb_build_object('listing_id',NULL,
 'market',@SCHEMA@.mineagent_normal_note_upper(d.market,false),'symbol',NULL)));
END $$;
""".replace("__MAPPING__", mapping.replace("'", "''")).replace(
        "__WHITESPACE__", strip_chars
    )
