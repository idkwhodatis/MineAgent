"""Production030 tables/constraints with synthetic metadata, never raw CSV proof."""

import copy
import json
from uuid import UUID

from packages.factors.contracts import digest


def literal(value):
    return (
        "'" + json.dumps(value, separators=(",", ":")).replace("'", "''") + "'::jsonb"
    )


def probe_statements():
    owner, other, basket, target = (
        str(UUID(int=i)) for i in (93001, 93002, 93003, 93004)
    )
    ids = [str(UUID(int=i)) for i in (93005, 93006)]
    listings = [
        {
            "listing_id": str(UUID(int=i)),
            "mic": "XNYS",
            "currency": "USD",
            "identity_status": "user_supplied",
        }
        for i in (93007, 93008)
    ]
    sources = [
        {
            "schema_version": 2,
            "is_synthetic": False,
            "evaluation_compatibility": {"compatible": True},
            "upload_source": {"artifact": "source.csv"},
            "listings": [listing],
        }
        for listing in listings
    ]
    cases = [
        None,
        "count_one",
        "count_zero",
        "no_header",
        "duplicate_key",
        "duplicate_source",
        "source_owner",
        "source_hash",
        "source_dates",
        "source_synthetic",
        "source_provider",
        "source_compatibility",
        "source_original",
        "source_schema",
        "target_profile",
        "target_pit",
        "target_synthetic",
        "target_owner",
        "target_dates",
        "manifest_member_hash",
        "manifest_order",
        "listing_drift",
        "currency",
        "request_hash",
        "ordinal",
        "update_source",
        "update_target",
        "replace_member",
        "delete_source",
        "update_head",
        "delete_member",
        "oe_wrong_synthetic",
        "oe_wrong_source",
        "oe_unknown",
        "downgrade_data",
    ]
    statements = []
    for mutation in cases:
        src = copy.deepcopy(sources)
        if mutation == "source_compatibility":
            src[0]["evaluation_compatibility"]["compatible"] = False
        if mutation == "source_original":
            src[0]["upload_source"]["artifact"] = "other.csv"
        if mutation == "source_schema":
            src[0]["schema_version"] = 3
        hashes = [digest(x) for x in src]
        manifest = {
            "schema_version": 3,
            "profile": "mineagent.declared-upload-basket.v1",
            "owner_id": owner,
            "dataset_version_id": target,
            "start": "2024-01-01",
            "end": "2024-02-01",
            "market": "US",
            "is_synthetic": False,
            "data_evidence": "user_declaration_unverified",
            "point_in_time_proven": False,
            "artifact_files": {},
            "listings": copy.deepcopy(listings),
            "sources": [
                {
                    "ordinal": i,
                    "dataset_version_id": ids[i],
                    "manifest_sha256": hashes[i],
                    "listing_id": listings[i]["listing_id"],
                    "artifact_prefix": f"sources/{i}/",
                }
                for i in range(2)
            ],
        }
        if mutation == "target_profile":
            manifest["profile"] = "unknown"
        if mutation == "target_pit":
            manifest["point_in_time_proven"] = True
        if mutation == "target_owner":
            manifest["owner_id"] = other
        if mutation == "target_dates":
            manifest["end"] = "2024-02-02"
        if mutation == "manifest_member_hash":
            manifest["sources"][0]["manifest_sha256"] = "f" * 64
        if mutation == "manifest_order":
            manifest["sources"].reverse()
        if mutation == "listing_drift":
            manifest["listings"][0]["identity_status"] = "provider_resolved"
        if mutation == "currency":
            manifest["listings"][0]["currency"] = "CNY"
        request = {
            "source_dataset_ids": ids,
            "acknowledge_unverified": True,
            "idempotency_key": "test-key",
        }
        request_hash = "f" * 64 if mutation == "request_hash" else digest(request)
        lines = [
            f"INSERT INTO users(id,email,hashed_password,is_active,is_superuser,created_at) VALUES('{owner}','upload030@example.test','not-a-login',true,false,now()),('{other}','upload030-other@example.test','not-a-login',true,false,now());"
        ]
        for i in range(2):
            own = other if mutation == "source_owner" and i == 0 else owner
            end = (
                "2024-02-02" if mutation == "source_dates" and i == 0 else "2024-02-01"
            )
            synthetic = "true" if mutation == "source_synthetic" and i == 0 else "false"
            provider = (
                "yfinance" if mutation == "source_provider" and i == 0 else "upload"
            )
            lines.append(
                f"INSERT INTO dataset_versions(id,owner_id,name,market,start_date,end_date,source,is_synthetic,content_sha256,manifest,storage_key,created_at) VALUES('{ids[i]}','{own}','source','US','2024-01-01','{end}','{provider}',{synthetic},'{hashes[i]}',{literal(src[i])},'datasets/{own}/{ids[i]}',now());"
            )
        target_synthetic = "true" if mutation == "target_synthetic" else "false"
        lines.append(
            f"INSERT INTO dataset_versions(id,owner_id,name,market,start_date,end_date,source,is_synthetic,content_sha256,manifest,storage_key,created_at) VALUES('{target}','{owner}','basket','US','2024-01-01','2024-02-01','declared_upload_basket',{target_synthetic},'{digest(manifest)}',{literal(manifest)},'datasets/{owner}/{target}',now());"
        )
        lines.append(
            f"INSERT INTO declared_upload_baskets VALUES('{basket}','{owner}','test-key','{request_hash}','{target}','{digest(manifest)}','mineagent.declared-upload-basket.v1',now());"
        )
        if mutation == "no_header":
            lines.pop()
        for i in range(
            0
            if mutation in {"count_zero", "no_header"}
            else 1
            if mutation == "count_one"
            else 2
        ):
            sid = ids[0] if mutation == "duplicate_source" else ids[i]
            sha = "f" * 64 if mutation == "source_hash" and i == 0 else hashes[i]
            ordinal = 2 if mutation == "ordinal" and i == 1 else i
            lines.append(
                f"INSERT INTO declared_upload_basket_members VALUES('{basket}',{ordinal},'{owner}','{sid}','{sha}',now());"
            )
        lines += [
            "SET CONSTRAINTS ALL IMMEDIATE;",
            f"PERFORM mineagent_upload_basket_check('{basket}');",
            f"PERFORM mineagent_upload_oe_qualification('{owner}','{target}','user_declaration_unverified');",
        ]
        actions = {
            "update_source": f"UPDATE dataset_versions SET manifest='{{}}'::jsonb WHERE id='{ids[0]}';",
            "delete_source": f"DELETE FROM dataset_versions WHERE id='{ids[0]}';",
            "update_head": f"UPDATE declared_upload_baskets SET request_sha256=repeat('f',64) WHERE id='{basket}';",
            "delete_member": f"DELETE FROM declared_upload_basket_members WHERE basket_id='{basket}';",
            "oe_wrong_synthetic": f"PERFORM mineagent_upload_oe_qualification('{owner}','{target}','synthetic_by_construction');",
            "oe_wrong_source": f"PERFORM mineagent_upload_oe_qualification('{owner}','{ids[0]}','user_declaration_unverified');",
            "oe_unknown": f"PERFORM mineagent_upload_oe_qualification('{owner}','{target}','unknown');",
        }
        actions.update(
            {
                "update_target": f"UPDATE dataset_versions SET manifest='{{}}'::jsonb WHERE id='{target}';",
                "replace_member": f"UPDATE declared_upload_basket_members SET source_dataset_id='{target}' WHERE basket_id='{basket}' AND ordinal=0;",
                "duplicate_key": f"INSERT INTO declared_upload_baskets VALUES('{UUID(int=999992)!s}','{owner}','test-key','{request_hash}','{target}','{digest(manifest)}','mineagent.declared-upload-basket.v1',now());",
            }
        )
        if mutation in actions:
            lines.append(actions[mutation])
        if mutation == "downgrade_data":
            from backend.app.declared_upload_revision import render_downgrade_sql

            lines.append(
                "EXECUTE replace('"
                + render_downgrade_sql().replace("'", "''")
                + "', '@SCHEMA@', quote_ident(current_schema()));"
            )
        expect = "false" if mutation is None else "true"
        allowed = (
            ("23503", "23505")
            if mutation in {"duplicate_source", "duplicate_key"}
            else ("23503",)
            if mutation in {"source_owner", "source_hash"}
            else ("23514",)
            if mutation == "ordinal"
            else ("P0002",)
            if mutation in {"oe_wrong_source", "no_header"}
            else ("P0001",)
        )
        states = ",".join("'" + value + "'" for value in allowed)
        sql = "\n".join(lines)
        statements.append(f"""DO $probe$ BEGIN BEGIN
{sql}
IF {expect} THEN RAISE EXCEPTION USING ERRCODE='XX000', MESSAGE='negative case unexpectedly passed {mutation}'; END IF;
RAISE EXCEPTION USING ERRCODE='P9001', MESSAGE='rollback successful positive';
EXCEPTION WHEN SQLSTATE 'P9001' THEN NULL;
WHEN OTHERS THEN IF NOT {expect} OR SQLSTATE NOT IN ({states}) THEN RAISE; END IF;
IF SQLSTATE='P0001' AND SQLERRM NOT LIKE 'Declared upload%' AND SQLERRM NOT LIKE 'Unknown structured market qualification%' THEN RAISE; END IF;
END; END $probe$;""")
    statements.append("SELECT 'DECLARED_UPLOAD_030_MATRIX_COMPLETE';")
    return statements


def actual_basket_statements():
    """Actual inert source/basket builder output, checked by production SQL too."""
    from packages.rdagent_adapter.declared_upload import build_declared_basket
    from tests.rdagent.test_declared_upload_source import OWNER, TARGET, source

    sources = (source(), source(1))
    built = build_declared_basket(owner_id=OWNER, dataset_id=TARGET, sources=sources)
    target_manifest = json.loads(built.manifest_bytes)
    request = {
        "source_dataset_ids": [str(item.dataset_id) for item in sources],
        "acknowledge_unverified": True,
        "idempotency_key": "real-builder",
    }
    statements = [
        f"INSERT INTO users(id,email,hashed_password,is_active,is_superuser,created_at) VALUES('{OWNER}','actual-builder@example.test','not-a-login',true,false,now());"
    ]
    for item in sources:
        manifest = json.loads(item.manifest_bytes)
        start, end = manifest["request"]["start"], manifest["request"]["end"]
        statements.append(
            f"INSERT INTO dataset_versions(id,owner_id,name,market,start_date,end_date,source,is_synthetic,content_sha256,manifest,storage_key,created_at) VALUES('{item.dataset_id}','{OWNER}','actual source builder','US','{start}','{end}','upload',false,'{item.manifest_sha256}',{literal(manifest)},'datasets/{OWNER}/{item.dataset_id}',now());"
        )
    statements.append(
        f"INSERT INTO dataset_versions(id,owner_id,name,market,start_date,end_date,source,is_synthetic,content_sha256,manifest,storage_key,created_at) VALUES('{TARGET}','{OWNER}','actual basket builder','US','{target_manifest['start']}','{target_manifest['end']}','declared_upload_basket',false,'{built.sha256}',{literal(target_manifest)},'datasets/{OWNER}/{TARGET}',now());"
    )
    bid = str(UUID(int=99901))
    statements.append(
        f"INSERT INTO declared_upload_baskets VALUES('{bid}','{OWNER}','real-builder','{digest(request)}','{TARGET}','{built.sha256}','mineagent.declared-upload-basket.v1',now());"
    )
    for ordinal, item in enumerate(sources):
        statements.append(
            f"INSERT INTO declared_upload_basket_members VALUES('{bid}',{ordinal},'{OWNER}','{item.dataset_id}','{item.manifest_sha256}',now());"
        )
    statements += [
        "SET CONSTRAINTS ALL IMMEDIATE;",
        f"SELECT mineagent_upload_basket_check('{bid}');",
        f"SELECT mineagent_upload_oe_qualification('{OWNER}','{TARGET}','user_declaration_unverified');",
        "SELECT 'ACTUAL_DECLARED_BASKET_SQL_COMPLETE';",
    ]
    return statements


def qualification_statements():
    """Pure installed contract pair, not relational/OE execution proof."""
    from tests.openevolve.test_structured_persisted_identity import graph

    value = graph()[1].config
    statements = []
    schemas = [
        "mineagent.oe-structured-retained-inputs.v1",
        "mineagent.oe-structured-retained-inputs.v2",
        "unknown",
        None,
        False,
    ]
    qualifications = [
        "synthetic_by_construction",
        "user_declaration_unverified",
        "unknown",
        None,
        False,
    ]
    for schema in schemas:
        for qualification in qualifications:
            raw = copy.deepcopy(value)
            raw["fixed_inputs"].update(
                schema_version=schema, market_qualification=qualification
            )
            expected = (schema, qualification) in [
                (schemas[0], qualifications[0]),
                (schemas[1], qualifications[1]),
            ]
            # The unchanged028 shape rejects this legitimate 0.5 weight even on v1.
            legacy = False
            statements.append(
                f"DO $$ BEGIN IF mineagent_oes_contract('config',{literal(raw)}) IS DISTINCT FROM {str(expected).lower()} THEN RAISE EXCEPTION 'New retained contract pair differs {schema} {qualification}'; END IF; IF mineagent_oes_contract_before_upload('config',{literal(raw)}) IS DISTINCT FROM {str(legacy).lower()} THEN RAISE EXCEPTION 'Historical retained contract accepted new semantics {schema} {qualification}'; END IF; END $$;"
            )
    statements.append("SELECT 'DECLARED_UPLOAD_QUALIFICATION_MATRIX_COMPLETE';")
    return statements
