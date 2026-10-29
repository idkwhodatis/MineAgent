"""Real PostgreSQL differential proof of 027b pure material against old oracles."""

import json

import pytest
from sqlalchemy import text

from backend.app.rdagent_provider_material_sql import BINDING_SQL, MATERIAL_SQL
from packages.rdagent_provider.contracts import (
    DeclaredTokenPricing,
    RDModelBinding,
    RDProviderChatRequest,
    RDProviderEmbeddingRequest,
)

pytestmark = pytest.mark.postgres


def material_cases():
    binding = RDModelBinding(
        profile_id="synthetic",
        profile_sha256="a" * 64,
        chat_model="chat",
        embedding_model="embed",
        embedding_dimensions=2,
        max_output_tokens=100,
        request_deadline_ms=8000,
        synthetic=True,
        pricing=DeclaredTokenPricing(
            chat_input_microusd_per_million=1,
            chat_output_microusd_per_million=2,
            embedding_microusd_per_million=3,
        ),
    )
    b = binding.model_dump(mode="json")
    requests = []
    for content in ["ascii", "中文😀", 'quote"\\\n\r\t\b\f\x7f', "x" * 8192]:
        common = {
            "model_profile_id": binding.profile_id,
            "model_binding_sha256": binding.sha256,
        }
        requests.extend(
            [
                RDProviderChatRequest(
                    **common,
                    model="chat",
                    messages=({"role": "user", "content": content},),
                    max_output_tokens=100,
                    response_format=None,
                ).model_dump(mode="json"),
                RDProviderEmbeddingRequest(
                    **common, model="embed", inputs=(content,), dimensions=2
                ).model_dump(mode="json"),
            ]
        )
    cases = []
    for req in requests:
        cases.append((req, b))
        for key in list(req):
            v = dict(req)
            v.pop(key)
            cases.append((v, b))
            for wrong in [None, False, {}, [], 0, 1.5, "wrong"]:
                v = dict(req)
                v[key] = wrong
                cases.append((v, b))
        for key in b:
            v = dict(b)
            v.pop(key)
            cases.append((req, v))
            for wrong in [None, False, {}, [], 0, 1.5, "wrong"]:
                v = dict(b)
                v[key] = wrong
                cases.append((req, v))
    for x in [None, False, True, {}, [], 0, 1.5, "wrong"]:
        cases.extend([(x, b), (requests[0], x)])
    # Pricing unavailable remains valid request but reservation NULL.
    u = RDModelBinding.model_validate({**b, "pricing": {"status": "unavailable"}})
    for req in requests[:2]:
        cases.append(
            ({**req, "model_binding_sha256": u.sha256}, u.model_dump(mode="json"))
        )
    return cases


def test_material_matches_frozen_public_oracles(migrated_engine):
    # pg_temp only; does not replace either frozen oracle or production graph.
    wrapper = (
        """CREATE FUNCTION pg_temp.probe_material(v jsonb,b jsonb) RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE run record;op record;binding jsonb;binding_valid boolean;binding_digest text;request_valid boolean;request_digest text;request_utf8 text;request_ascii text;amount jsonb;output_tokens bigint;cost numeric;
BEGIN
SELECT jsonb_build_object('model_binding',b) AS config INTO run;
SELECT v AS request INTO op;
"""
        + BINDING_SQL
        + MATERIAL_SQL
        + "RETURN jsonb_build_object('valid',request_valid,'sha256',request_digest,'amount',amount);END $$;"
    )
    query = text("""
WITH inputs AS (SELECT CAST(:request AS jsonb) v, CAST(:binding AS jsonb) b),
validated AS (SELECT *, mineagent_rd_provider_request_binding(v,b) valid FROM inputs)
SELECT pg_temp.probe_material(v,b), jsonb_build_object(
'valid',valid,'sha256',CASE WHEN valid THEN encode(sha256(convert_to(mineagent_rd_canonical_json(v,false),'UTF8')),'hex') ELSE NULL END,
'amount',mineagent_rd_provider_reservation(v,b)) FROM validated
""")
    with migrated_engine.connect() as connection:
        connection.exec_driver_sql(wrapper)
        cases = [(json.dumps(v), json.dumps(b)) for v, b in material_cases()]
        # SQL NULL is distinct from JSON null. Exercise each independently.
        cases.extend([(None, None), (None, cases[0][1]), (cases[0][0], None)])
        for index, (value, binding) in enumerate(cases):
            actual, expected = connection.execute(
                query, {"request": value, "binding": binding}
            ).one()
            assert actual == expected, (index, actual, expected)
