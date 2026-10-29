"""Real installed032 rejection/legacy matrix, never a fabricated OE success graph."""

from uuid import NAMESPACE_URL, uuid5


def uid(label):
    return str(uuid5(NAMESPACE_URL, "mineagent-note032-sql:" + label))


def rejection_statements():
    owner, note, revision, pin = (uid(x) for x in ("owner", "note", "revision", "pin"))
    yield f"""INSERT INTO users(id,email,hashed_password,is_active,is_superuser,created_at)
 VALUES('{owner}','note032-sql@example.invalid','not-a-login',false,false,now());
 INSERT INTO research_notes(id,owner_id) VALUES('{note}','{owner}');
 INSERT INTO research_note_revisions(id,note_id,owner_id,revision,revision_sha256,pins_sha256,
 content,evidence_snapshot,admission_snapshot,title,tags,status,search_text,change_note,idempotency_key,request_sha256)
 VALUES('{revision}','{note}','{owner}',1,repeat('a',64),repeat('b',64),
 '{{"title":"SQL boundary","status":"pending","tags":[],"evidence":[]}}',
 '[]','[]','SQL boundary','[]','pending','SQL boundary',NULL,'note032-sql',repeat('c',64));
 SET CONSTRAINTS ALL IMMEDIATE; SET CONSTRAINTS ALL DEFERRED;"""
    # Old non-artifact kind retains its old no-external-FK behavior. The new
    # validator must not silently upgrade historical generic pins to normal OE.
    yield f"""INSERT INTO research_note_evidence_pins(id,revision_id,owner_id,resource_kind,resource_id,sha256,details)
 VALUES('{pin}','{revision}','{owner}','dataset','{uid("missing-dataset")}',NULL,'{{}}');
 SET CONSTRAINTS ALL IMMEDIATE; SET CONSTRAINTS ALL DEFERRED;"""
    inserts = {
        "normal_missing_attachment": f"""INSERT INTO research_note_evidence_pins(id,revision_id,owner_id,resource_kind,resource_id,sha256,details)
 VALUES('{uid("normal")}','{revision}','{owner}','normal_oe_execution','{uid("execution")}',repeat('d',64),'{{}}');""",
        "legacy_has_attachment": f"""INSERT INTO research_note_normal_oe_pins(pin_id,owner_id,run_id,attempt_id,generation,execution_id,engine_outcome_id,factor_exposure_id,market_exposure_id)
 VALUES('{pin}','{owner}','{uid("run")}','{uid("attempt")}',1,'{uid("execution")}','{uid("outcome")}','{uid("factor-release")}','{uid("market-release")}');""",
        "unknown_kind": f"""INSERT INTO research_note_evidence_pins(id,revision_id,owner_id,resource_kind,resource_id,sha256,details)
 VALUES('{uid("unknown")}','{revision}','{owner}','normal_oe_fill','{uid("execution")}',repeat('d',64),'{{}}');""",
        "pin_update": f"UPDATE research_note_evidence_pins SET details='{{\"changed\":true}}' WHERE id='{pin}';",
        "pin_delete": f"DELETE FROM research_note_evidence_pins WHERE id='{pin}';",
        "revision_update": f"UPDATE research_note_revisions SET title='rewritten' WHERE id='{revision}';",
        "revision_delete": f"DELETE FROM research_note_revisions WHERE id='{revision}';",
    }
    for label, sql in inserts.items():
        yield f"""DO $$ DECLARE rejected boolean:=false; BEGIN
 BEGIN {sql} SET CONSTRAINTS ALL IMMEDIATE;
 EXCEPTION WHEN integrity_constraint_violation OR raise_exception THEN rejected:=true; END;
 IF NOT rejected THEN RAISE EXCEPTION 'Missing rejection: {label}'; END IF;
 END $$; SET CONSTRAINTS ALL DEFERRED;"""
    yield "SELECT 'NORMAL_NOTE_032_REJECTION_MATRIX_COMPLETE';"


def unicode_identity_statements():
    for value in (
        "a.b-c",
        " straße ",
        "\u3000σίσυφος\u0085",
        "ıiİſ",
        "\u2003𐐨\u001c",
        "",
        "\t \n",
        "".join(chr(i) for i in range(0x110000) if chr(i).isspace()),
    ):
        for trim in (False, True):
            expected = (value.strip() if trim else value).upper()

            def lit(s):
                return "convert_from(decode('" + s.encode().hex() + "','hex'),'UTF8')"

            yield (
                "DO $$ BEGIN IF mineagent_normal_note_upper("
                + lit(value)
                + ","
                + str(trim).lower()
                + ") IS DISTINCT FROM "
                + lit(expected)
                + " THEN RAISE EXCEPTION 'Normal note Unicode identity differs'; END IF; END $$;"
            )
    yield "SELECT 'NORMAL_NOTE_UNICODE_MATRIX_COMPLETE';"


def identity_dataset_statements():
    """Exact normalization predicate in temporary tables, not full FK admission."""
    import json
    from types import SimpleNamespace

    from backend.app.experiment_services import dataset_identities
    from backend.app.normal_oe_note_identity_sql import render_identity_sql

    def j(value):
        return (
            "convert_from(decode('"
            + json.dumps(value, ensure_ascii=True).encode().hex()
            + "','hex'),'UTF8')::jsonb"
        )

    yield "CREATE TEMP TABLE dataset_versions AS SELECT * FROM dataset_versions WITH NO DATA; CREATE TEMP TABLE listings AS SELECT * FROM listings WITH NO DATA;"
    yield render_identity_sql().replace("@SCHEMA@", "pg_temp")
    a, b = uid("listing-a"), uid("listing-b")
    symbols = {a: " ſtraße ", b: "\u3000𐐨\u2003"}
    for key, symbol in symbols.items():
        yield (
            "INSERT INTO pg_temp.listings SELECT * FROM jsonb_populate_record(NULL::pg_temp.listings,"
            + j({"id": key, "symbol": symbol})
            + ");"
        )
    manifests = (
        {},
        {"listings": []},
        {"listings": [{"listing_id": a}]},
        {"listings": [{"listing_id": a, "symbol": "ı"}, {"listing_id": b}]},
        {
            "listings": [{"listing_id": a, "symbol": ""}],
            "evaluator_symbol_mapping": {a: "SHADOWED"},
        },
        {
            "listings": [{"listing_id": uid("absent")}],
            "evaluator_symbol_mapping": {uid("absent"): " mapped "},
        },
        {"listings": [{"listing_id": b}, {"listing_id": a}, {"listing_id": b}]},
        {"listings": [{"listing_id": a, "symbol": "\u3000 "}]},
    )
    for n, manifest in enumerate(manifests):
        did = uid("identity-dataset-" + str(n))
        expected = dataset_identities(
            SimpleNamespace(manifest=manifest, market="us"), listing_symbols=symbols
        )
        yield (
            "INSERT INTO pg_temp.dataset_versions SELECT * FROM jsonb_populate_record(NULL::pg_temp.dataset_versions,"
            + j({"id": did, "manifest": manifest, "market": "us"})
            + ");"
        )
        yield f"DO $$ BEGIN IF pg_temp.mineagent_normal_note_dataset_identities('{did}') IS DISTINCT FROM {j(expected)} THEN RAISE EXCEPTION 'Dataset identity normalization differs'; END IF; END $$;"
    yield "SELECT 'NORMAL_NOTE_DATASET_IDENTITY_MATRIX_COMPLETE';"
