"""Construction and actual producer contracts; these are NOT PostgreSQL execution."""

import json
import re
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.app import oe_structured_checkpoint_sql as checkpoint
from backend.app import oe_structured_provider_sql as provider
from packages.factors.contracts import canonical_bytes
from packages.openevolve_structured.provider_accounting import request_reservation
from packages.openevolve_structured.provider_contracts import ChatPricing, OEChatBinding
from packages.openevolve_structured.provider_response import build_request
from packages.openevolve_structured.upload_contracts import Begin, File
from packages.research_runtime.budget_contracts import BudgetVector
from tests.openevolve.test_structured_provider_contracts import declared


def schema_for(sql, kind):
    raw = re.search(r"WHEN '" + kind + r"' THEN rule := '((?:''|[^'])*)'::jsonb;", sql)[
        1
    ]
    return json.loads(raw.replace("''", "'"))


@pytest.mark.parametrize("module", [provider, checkpoint])
def test_literal_fragments_are_deterministic_bounded_and_not_self_installing(module):
    sql = module.render_upgrade_sql()
    assert sql == module.render_upgrade_sql()
    assert len(sql) < 100000
    assert "@SCHEMA@" in sql and "@RULE" not in sql
    assert "CREATE TRIGGER" not in sql and "CREATE CONSTRAINT TRIGGER" not in sql
    assert "alembic_version" not in sql
    assert "DROP FUNCTION @SCHEMA@.mineagent_oes_" in module.render_downgrade_sql()
    assert len(module.TABLES) == len(set(module.TABLES))
    assert "research_budget_settlements" in module.TABLES
    # Demonstrate why assembler MUST use raw no_parameters, not text() parsing.
    assert "%ROWTYPE" in sql
    assert text(sql) is not None


def test_request_schema_matches_actual_producer_not_fixture_alias():
    _, binding, domain, _, raw, rendered, context, _ = declared()
    binding = OEChatBinding.model_validate(
        {
            **binding.model_dump(),
            "pricing": ChatPricing(
                status="operator_declared",
                input_microusd_per_million=1200,
                output_microusd_per_million=3400,
            ),
        }
    )
    request = build_request(
        binding=binding,
        context=context,
        domain=domain,
        parent_candidate=raw,
        rendered=rendered,
    )
    held = request_reservation(request, binding)
    assert held.model_dump() == {
        "calls": 1,
        "tokens": 262144 + request.max_output_tokens,
        "cost_microunits": (262144 * 1200 + request.max_output_tokens * 3400 + 999999)
        // 1000000,
        "mutation_slots": 0,
        "evaluator_executions": 0,
        "wall_milliseconds": binding.request_deadline_ms,
        "storage_bytes": 589824,
        "process_slots": 1,
    }
    sql = provider.render_upgrade_sql()
    schema = schema_for(sql, "request")
    assert set(schema["properties"]) == set(request.model_dump())
    assert set(schema["required"]) == set(request.model_dump())
    assert schema["additionalProperties"] is False
    for field in BudgetVector.model_fields:
        assert f"'{field}'" in sql
    assert "ceil(" in sql and "262144::numeric" in sql
    assert "config_raw::json#>'{search,domain}'" in sql
    assert "canonical_json(config#>" not in sql  # No lossy float-domain reconstruction.


@pytest.mark.parametrize(
    "guard",
    [
        "t.operation_id,t.ordinal,o.protocol,o.purpose,o.ordinal",
        "membership_id=m.id",
        "request->>'parent_candidate_sha256'",
        "request#>>'{messages,0,content}'",
        "request#>>'{messages,1,content}'",
        "c.context IS DISTINCT FROM i.context_canonical::jsonb",
        "e.epoch_id,e.iteration_intent_id,e.parent_candidate_id",
        "outcome.response_object_id,outcome.response_object_kind,outcome.response_sha256",
        "s.charge IS DISTINCT FROM expected",
        "outcome.usage IS DISTINCT FROM expected",
        "s.outcome IS DISTINCT FROM 'conservative_unknown'",
        "OE terminal transport outcome",
    ],
)
def test_same_scope_provider_swaps_have_explicit_comparison(guard):
    assert guard in provider.render_upgrade_sql()


def test_actual_inventory_shape_and_held_peak_are_exact():
    begin = Begin(
        token=uuid4(),
        files=tuple(
            File(name=name, size_bytes=size, sha256="a" * 64)
            for name, size in [
                ("best_program.json", 65537),
                ("best_program_info.json", 1),
                ("metadata.json", 10),
                ("programs/00000000-0000-0000-0000-000000000001.json", 512000),
            ]
        ),
    )
    assert len(begin.chunks) == 12
    assert (
        begin.transfer_peak_bytes
        == 2 * begin.total_bytes + 2 * 1048576 + 2 * 4096 * (4 + 12 + 1)
    )
    sql = checkpoint.render_upgrade_sql()
    rule = json.loads(
        re.search(r"rule jsonb:='((?:''|[^'])*)'::jsonb", sql)[1].replace("''", "'")
    )
    assert set(rule["properties"]) == set(json.loads(canonical_bytes(begin)))
    assert rule["additionalProperties"] is False
    assert "2*bytes+2*1048576+2*4096*(u.file_count+u.chunk_count+1)" in sql
    assert "observed<>chunks+1" in sql  # Pending prefix allowed, gaps rejected.
    assert "IF u.id IS NULL THEN" in sql and "RETURN;" in sql
    assert "save_ordinal NOT BETWEEN 1 AND maximum+2" in sql
    assert "count_saves<>max_save" in sql
    assert "UNIQUE" not in sql  # No false uniqueness of control/frontier saves.


@pytest.mark.parametrize(
    "guard",
    [
        "u.generation,u.epoch_id",
        "inventory->>'token' IS DISTINCT FROM i.id::text",
        "chunk.file_name,chunk.offset_bytes,chunk.size_bytes",
        "obj.sha256=chunk.object_sha256",
        "observed<>chunks",
        "pub.completion_id,pub.control_state_id,pub.state_sha256",
        "paid.charge IS DISTINCT FROM expected",
        "pr.kind='controller_completion'",
        "st.status='stopped'",
        "cs.charge=cr.amounts",
    ],
)
def test_checkpoint_same_scope_swaps_and_premature_release_guarded(guard):
    assert guard in checkpoint.render_upgrade_sql()


def test_every_static_row_reference_uses_actual_mapped_column():
    from backend.app import oe_structured_checkpoint_models as transfers
    from backend.app import oe_structured_models as domain
    from backend.app import oe_structured_operational_models as operation
    from backend.app import research_broker_models as broker
    from backend.app.research_process_models import ResearchProcessAdmission

    aliases = {
        "m": operation.OEStructuredProviderTransport,
        "i": operation.OEStructuredIterationIntent,
        "p": domain.OEStructuredCandidate,
        "o": broker.ResearchModelOperation,
        "t": broker.ResearchModelTransport,
        "c": broker.ResearchOperationContext,
        "r": broker.ResearchBudgetReservation,
        "s": broker.ResearchBudgetSettlement,
        "outcome": broker.ResearchModelOutcome,
        "e": domain.OEStructuredProviderExchange,
        "receipt": operation.OEStructuredProviderReceipt,
    }
    checkpoints = {
        "i": transfers.OEStructuredCheckpointIntent,
        "u": transfers.OEStructuredCheckpointUpload,
        "c": operation.OEStructuredControlState,
        "e": operation.OEStructuredEpoch,
        "r": broker.ResearchBudgetReservation,
        "paid": broker.ResearchBudgetSettlement,
        "done": transfers.OEStructuredCheckpointCompletion,
        "pub": transfers.OEStructuredCheckpointPublication,
        "controller": ResearchProcessAdmission,
        "chunk": transfers.OEStructuredCheckpointChunk,
    }
    for sql, models in (
        (provider.render_upgrade_sql(), aliases),
        (checkpoint.render_upgrade_sql(), checkpoints),
    ):
        for alias, model in models.items():
            for field in re.findall(r"\b" + alias + r"\.([a-z_][a-z_0-9]*)", sql):
                assert field in model.__table__.columns, (alias, model, field)


def test_real_commit_order_atomic_consumers_and_pending_prefix_are_distinct():
    provider_sql = provider.render_upgrade_sql()
    checkpoint_sql = checkpoint.render_upgrade_sql()
    assert (
        "CASE WHEN outcome.outcome='completed' THEN 1::bigint ELSE 0::bigint END"
        in provider_sql
    )
    assert "OE next save before prior publication" in checkpoint_sql
    assert "prior.save_ordinal<i.save_ordinal" in checkpoint_sql
    assert "IF done.id IS NOT NULL THEN" in checkpoint_sql
    assert "IF pub.id IS NOT NULL AND" in checkpoint_sql


def test_release_is_atomic_with_actual_terminal_consumers_not_only_receipts():
    sql = provider.render_upgrade_sql()
    assert "(r.state='settled') IS DISTINCT FROM (outcome.id IS NOT NULL)" in sql
    sql = checkpoint.render_upgrade_sql()
    assert "tail.epoch_id=e.id AND NOT EXISTS" in sql
    assert "final.save_ordinal=maximum+2 AND final.frontier=maximum" in sql
    assert "fc.phase='applied' AND fc.sequence=2*maximum+1" in sql
