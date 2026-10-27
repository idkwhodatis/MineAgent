"""Actual migrated 026 PostgreSQL checks; collection is not execution proof."""

import json

import pytest
from sqlalchemy import text

from packages.rdagent_provider.accounting import request_reservation
from packages.rdagent_provider.contracts import (
    DeclaredTokenPricing,
    RDModelBinding,
    RDProviderChatRequest,
    RDProviderEmbeddingRequest,
)

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("kind", ["chat", "embedding"])
@pytest.mark.parametrize(
    "mutation", [None, "model", "missing_binding", "unknown_price"]
)
def test_provider_request_and_conservative_amount_match_python(
    migrated_engine, kind, mutation
):
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
    if mutation == "unknown_price":
        binding = RDModelBinding.model_validate(
            {
                **binding.model_dump(),
                "pricing": {"status": "unavailable"},
            }
        )
    common = {
        "model_profile_id": binding.profile_id,
        "model_binding_sha256": binding.sha256,
    }
    request = (
        RDProviderChatRequest(
            **common,
            model="chat",
            messages=({"role": "user", "content": "中文😀"},),
            max_output_tokens=100,
            response_format=None,
        )
        if kind == "chat"
        else RDProviderEmbeddingRequest(
            **common, model="embed", inputs=("中文😀",), dimensions=2
        )
    )
    value = request.model_dump(mode="json")
    config = binding.model_dump(mode="json")
    if mutation == "model":
        value["model"] = "other"
    elif mutation == "missing_binding":
        config.pop("profile_id")
    with migrated_engine.connect() as connection:
        accepted, amount = connection.execute(
            text("""
            SELECT mineagent_rd_provider_request_binding(CAST(:request AS jsonb),CAST(:binding AS jsonb)),
                   mineagent_rd_provider_reservation(CAST(:request AS jsonb),CAST(:binding AS jsonb))
        """),
            {"request": json.dumps(value), "binding": json.dumps(config)},
        ).one()
    # Actual SQL interpreter: booleans are accepted only as JSON booleans.
    # This directly checks the structural rule, independent of binding hashes.
    with migrated_engine.connect() as connection:
        for candidate, expected in (
            (True, True),
            (False, True),
            (0, False),
            (1, False),
            ("true", False),
            ("false", False),
            (None, False),
            ([], False),
            ({}, False),
        ):
            actual = connection.scalar(
                text(
                    "SELECT mineagent_rd_schema(CAST(:value AS jsonb), "
                    "'{\"type\":\"boolean\"}'::jsonb, '{}'::jsonb, 0)"
                ),
                {"value": json.dumps(candidate)},
            )
            assert actual is expected
    assert accepted is (mutation in (None, "unknown_price"))
    if mutation is None:
        assert amount == request_reservation(request, binding).model_dump()
    else:
        assert amount is None
