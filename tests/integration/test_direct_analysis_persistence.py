"""Genuine PostgreSQL plan/trigger tests; no process or Qlib execution claim.

The pinned digests here are declaration-only test data. Production registration
is still closed and these tests never call a launcher or fabricate a result.
"""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from backend.app.models import User

pytestmark = pytest.mark.postgres


@pytest.fixture
def declared_plan(migrated_engine, settings, monkeypatch):
    from rd_configured_controller_fixture import prepare_configured_generation

    for role in ("CANDIDATE", "NORMALIZER", "ORCHESTRATOR", "ANALYSIS"):
        monkeypatch.setenv("MINEAGENT_" + role + "_IMAGE", "sha256:" + "a" * 64)
    monkeypatch.setenv("MINEAGENT_QLIB_INSTALLED_IMAGE", "sha256:" + "b" * 64)
    with Session(migrated_engine, expire_on_commit=False) as session:
        user = User(
            id=uuid4(),
            email=uuid4().hex + "@example.test",
            hashed_password="test-no-login",
            full_name="Plan constraint test",
        )
        session.add(user)
        session.commit()
    factory, fence = prepare_configured_generation(migrated_engine, settings, user.id)
    return factory, fence, settings


def test_declared_plan_precedes_generation_and_has_no_fabricated_result(declared_plan):
    factory, fence, _ = declared_plan
    with factory() as session:
        assert (
            session.scalar(
                text("SELECT count(*) FROM factor_analysis_plans WHERE run_id=:id"),
                {"id": fence.run_id},
            )
            == 1
        )
        for table in (
            "factor_generation_starts",
            "factor_direct_analysis_intents",
            "factor_direct_analysis_outcomes",
            "research_process_admissions",
        ):
            assert session.scalar(text(f"SELECT count(*) FROM {table}")) == 0


@pytest.mark.parametrize(
    "column,value",
    [
        ("objective_sha256", "c" * 64),
        ("qlib_installed_parent_image_id", "sha256:" + "c" * 64),
        ("source_policy_sha256", "c" * 64),
    ],
)
def test_declared_plan_is_immutable_in_actual_pg(declared_plan, column, value):
    factory, fence, _ = declared_plan
    with pytest.raises(DBAPIError), factory.begin() as session:
        session.execute(
            text(f"UPDATE factor_analysis_plans SET {column}=:value WHERE run_id=:id"),
            {"id": fence.run_id, "value": value},
        )


def test_downgrade_rejects_even_preexecution_analysis_plan(declared_plan):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    factory, _, _ = declared_plan
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "backend/alembic.ini"))
    config.set_main_option("script_location", str(root / "backend/alembic"))
    with factory() as session:
        entry_revision = session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    with pytest.raises(DBAPIError), factory.kw["bind"].begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "021_execution_snapshots")
    with factory() as session:
        assert (
            session.scalar(text("SELECT version_num FROM alembic_version"))
            == entry_revision
        )


def test_022_installs_frozen_semantic_and_immutable_triggers(migrated_engine):
    import json

    from rd_receipt_predicate_fixture import predicate_cases

    from packages.factors.contracts import canonical_bytes, digest

    with migrated_engine.connect() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
        assert schema.startswith("tradegrad_test_") and schema != "public"
        installed = connection.execute(
            text(r"""
SELECT p.proname,p.provolatile,p.prosecdef,
 regexp_split_to_array(replace(o.option_value,'"',''), '\s*,\s*') AS paths
FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
CROSS JOIN LATERAL pg_options_to_table(p.proconfig) o
WHERE n.nspname=current_schema()
 AND p.proname IN ('mineagent_analysis_payload','mineagent_analysis_stopped')
 AND o.option_name='search_path'
ORDER BY p.proname
""")
        ).all()
        assert installed == [
            ("mineagent_analysis_payload", "i", False, [schema, "pg_catalog"]),
            ("mineagent_analysis_stopped", "s", False, [schema, "pg_catalog"]),
        ]
        for function, kind, payload, expected in predicate_cases():
            if function == "mineagent_rd_controller_payload":
                continue
            assert function in {
                "mineagent_analysis_payload",
                "mineagent_direct_contract",
            }
            assert (
                connection.scalar(
                    text(f"SELECT {function}(:kind,CAST(:payload AS jsonb))"),
                    {"kind": kind, "payload": json.dumps(payload)},
                )
                is expected
            )
            if function == "mineagent_direct_contract" and expected:
                assert (
                    connection.scalar(
                        text(
                            "SELECT mineagent_analysis_raw_commitment(:raw,CAST(:payload AS jsonb),:sha,1048576)"
                        ),
                        {
                            "raw": canonical_bytes(payload).decode(),
                            "payload": json.dumps(payload),
                            "sha": digest(payload),
                        },
                    )
                    is True
                )
    with migrated_engine.connect() as connection:
        names = set(
            connection.execute(
                text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")
            ).scalars()
        )
        assert {
            "analysis_plan_before_insert",
            "analysis_generation_order",
            "analysis_plan_consistent",
            "direct_analysis_consistent",
            "configured_input_consistent",
            "rd_analysis_generation_closed",
        } <= names
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM pg_constraint WHERE conname='ck_process_v2_unregistered' AND pg_get_constraintdef(oid) LIKE '%mineagent.qlib-direct-analysis.v1%'"
                )
            ).scalar_one()
            == 1
        )


@pytest.mark.parametrize("value", [0.0, -0.0, 1.0, -1.0, 1e-7, 1e-20, 5e-324, -5e-324])
def test_actual_pg_lossless_float_commitments(migrated_engine, value):
    import hashlib
    import json

    from packages.factors.contracts import canonical_bytes

    raw = canonical_bytes({"value": value}).decode("ascii")
    with migrated_engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT mineagent_analysis_raw_commitment(:raw,CAST(:payload AS jsonb),:sha,1048576)"
                ),
                {
                    "raw": raw,
                    "payload": json.dumps({"value": value}),
                    "sha": hashlib.sha256(raw.encode()).hexdigest(),
                },
            )
            is True
        )
        assert (
            connection.scalar(
                text(
                    "SELECT mineagent_analysis_raw_commitment(:raw,CAST(:payload AS jsonb),:sha,1048576)"
                ),
                {
                    "raw": raw,
                    "payload": '{"value":0.5}',
                    "sha": hashlib.sha256(raw.encode()).hexdigest(),
                },
            )
            is False
        )


@pytest.mark.parametrize(
    "raw",
    [
        '{"value":NaN}',
        '{"value":Infinity}',
        '{"value":1e999}',
        '{"value":1e-999}',
        '{"value":0.0,"value":1.0}',
        '{ "value":0.0}',
    ],
)
def test_actual_pg_raw_commitment_rejects_unsafe_or_noncanonical(migrated_engine, raw):
    import hashlib

    with migrated_engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT mineagent_analysis_raw_commitment(:raw,CAST(:payload AS jsonb),:sha,1048576)"
                ),
                {
                    "raw": raw,
                    "payload": "{}" if "NaN" in raw or "Infinity" in raw else raw,
                    "sha": hashlib.sha256(raw.encode()).hexdigest(),
                },
            )
            is False
        )


@pytest.fixture
def dataset_admission_frontier(migrated_engine, settings, owner):
    """Actual queued declaration, two connections, no synthetic plan or SDK."""
    from rd_configured_dataset_fixture import persist_configured_dataset
    from rd_submission_fixture import body, profile
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool

    from backend.app.engine_run_preflight import preflight
    from backend.app.engine_run_submission_schemas import RDSubmit

    factory = sessionmaker(migrated_engine, expire_on_commit=False)
    dataset, _, policy = persist_configured_dataset(factory, owner.user.id, settings)
    settings.rdagent_research_enabled = True
    settings.rdagent_runtime_profile = profile(actual_images=False)
    with factory() as session:
        checked = preflight(session, owner.user.id, body(dataset, policy), settings)
        assert checked.can_submit
        submitted = RDSubmit.model_validate(
            {
                **checked.normalized_request.model_dump(mode="json"),
                "preflight_sha256": checked.preflight_sha256,
            }
        )
        schema = session.scalar(text("SELECT current_schema()"))
        for relation in (
            "research_workflows",
            "oe_structured_specs",
            "structured_strategy_specs",
            "research_runs",
            "factor_analysis_plans",
        ):
            assert (
                session.scalar(
                    text(f"SELECT count(*) FROM {relation} WHERE owner_id=:owner"),
                    {"owner": owner.user.id},
                )
                == 0
            )
        # An ordinary immediate UNIQUE key makes all fields consumed by the
        # original plan validator key-changing UPDATEs, even though the graph
        # FKs reference only dataset id. Never assume a partial/expression index.
        index = session.execute(
            text("""
          SELECT c.contype, c.condeferrable, c.convalidated,
                 i.indisunique, i.indisvalid, i.indisready, i.indimmediate,
                 i.indpred IS NULL, i.indexprs IS NULL,
                 ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(n,o)
                       JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.n ORDER BY k.o)
          FROM pg_constraint c JOIN pg_index i ON i.indexrelid=c.conindid
          WHERE c.conrelid='dataset_versions'::regclass AND c.conname='oe024_uq_dataset_versions'
        """)
        ).one()
        assert tuple(index) == (
            "u",
            False,
            True,
            True,
            True,
            True,
            True,
            True,
            True,
            ["owner_id", "id", "content_sha256"],
        )
        for relation, column in (
            ("factor_analysis_plans", "label_source_dataset_id"),
            ("research_runs", "dataset_version_id"),
        ):
            assert (
                session.scalar(
                    text("""
              SELECT count(*) FROM pg_constraint c
              JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=c.conkey[1]
              JOIN pg_attribute r ON r.attrelid=c.confrelid AND r.attnum=c.confkey[1]
              WHERE c.conrelid=to_regclass(:relation) AND c.confrelid='dataset_versions'::regclass
                AND c.contype='f' AND NOT c.condeferrable AND c.convalidated
                AND cardinality(c.conkey)=1 AND cardinality(c.confkey)=1
                AND a.attname=:column AND r.attname='id'
            """),
                    {"relation": relation, "column": column},
                )
                == 1
            )
    import re

    assert re.fullmatch(r"tradegrad_test_[a-f0-9]{32}", schema)
    engine = create_engine(
        migrated_engine.url,
        poolclass=NullPool,
        isolation_level="READ COMMITTED",
        connect_args={
            "connect_timeout": 5,
            "options": f"-csearch_path={schema} -cstatement_timeout=10000 -clock_timeout=2000",
        },
    )
    try:
        yield (
            sessionmaker(engine, expire_on_commit=False),
            owner.user.id,
            dataset,
            submitted,
            settings,
        )
    finally:
        engine.dispose()


def _frontier_submit(session, fixture):
    from backend.app.rdagent_submission_services import submit_configured_run

    _, owner, _, submitted, settings = fixture
    receipt = submit_configured_run(
        session, owner, submitted, "dataset-frontier-proof", settings
    )
    session.flush()
    session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    session.execute(
        text("SELECT mineagent_analysis_plan_check(:id)"), {"id": receipt.plan_id}
    )
    return receipt


def _frontier_hash_update(session, fixture):
    dataset = fixture[2]
    alternate = "f" * 64 if dataset.content_sha256 != "f" * 64 else "e" * 64
    session.execute(
        text("UPDATE dataset_versions SET content_sha256=:sha WHERE id=:id"),
        {"sha": alternate, "id": dataset.id},
    )
    session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


def _frontier_rejected(action, *, state, message=None):
    matched = False
    try:
        action()
    except DBAPIError as error:
        matched = getattr(error.orig, "sqlstate", None) == state
        if message is not None:
            matched = (
                matched
                and getattr(getattr(error.orig, "diag", None), "message_primary", None)
                == message
            )
    assert matched, "Expected exact original database rejection was not observed"


def _frontier_snapshot(session):
    # Private bounded digests only, never export rows or row identifiers.
    import hashlib

    tables = (
        "dataset_versions",
        "research_runs",
        "factor_research_specs",
        "factor_analysis_plans",
        "factor_research_submissions",
        "outbox_events",
        "research_budget_controls",
        "data_usage_exposures",
    )
    result = []
    for table in tables:
        rows = (
            session.execute(
                text(
                    f"SELECT to_jsonb(t)::text FROM {table} t ORDER BY to_jsonb(t)::text LIMIT 1001"
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) <= 1000
        result.append(hashlib.sha256("\n".join(rows).encode()).digest())
    return tuple(result)


def test_dataset_identity_update_conflicts_with_actual_uncommitted_submission(
    dataset_admission_frontier,
):
    f = dataset_admission_frontier
    with f[0]() as admission, f[0]() as writer:
        assert admission.scalar(text("SELECT pg_backend_pid()")) != writer.scalar(
            text("SELECT pg_backend_pid()")
        )
        receipt = _frontier_submit(admission, f)
        # Distinguish the existing FK KEY SHARE mechanism from owner advisory
        # contention: weaker non-key locking succeeds, key-changing locking fails.
        writer.execute(
            text(
                "SELECT id FROM dataset_versions WHERE id=:id FOR NO KEY UPDATE NOWAIT"
            ),
            {"id": f[2].id},
        )
        writer.rollback()
        _frontier_rejected(
            lambda: writer.execute(
                text("SELECT id FROM dataset_versions WHERE id=:id FOR UPDATE NOWAIT"),
                {"id": f[2].id},
            ),
            state="55P03",
        )
        writer.rollback()
        _frontier_rejected(lambda: _frontier_hash_update(writer, f), state="55P03")
        writer.rollback()
        admission.commit()
        writer.execute(
            text("SELECT mineagent_analysis_plan_check(:id)"), {"id": receipt.plan_id}
        )
        assert (
            writer.scalar(
                text("SELECT content_sha256 FROM dataset_versions WHERE id=:id"),
                {"id": f[2].id},
            )
            == f[2].content_sha256
        )
        writer.rollback()


def test_dataset_identity_writer_blocks_actual_submission_and_rolls_back_all_graph_rows(
    dataset_admission_frontier,
):
    f = dataset_admission_frontier
    with f[0]() as admission, f[0]() as writer:
        before = _frontier_snapshot(admission)
        admission.rollback()
        _frontier_hash_update(writer, f)
        _frontier_rejected(lambda: _frontier_submit(admission, f), state="55P03")
        admission.rollback()
        writer.rollback()
        assert _frontier_snapshot(admission) == before
        admission.rollback()


def test_dataset_serial_identity_corruption_rejected_by_original_plan_guard(
    dataset_admission_frontier,
):
    f = dataset_admission_frontier
    with f[0]() as admission, f[0]() as writer:
        receipt = _frontier_submit(admission, f)
        admission.commit()
        before = _frontier_snapshot(writer)
        writer.rollback()
        _frontier_rejected(
            lambda: _frontier_hash_update(writer, f),
            state="P0001",
            message="Analysis plan source/spec/objective differs",
        )
        writer.rollback()
        assert _frontier_snapshot(writer) == before
        writer.execute(
            text("SELECT mineagent_analysis_plan_check(:id)"), {"id": receipt.plan_id}
        )
        writer.rollback()


def test_dataset_nonkey_name_update_remains_concurrent_and_graph_valid(
    dataset_admission_frontier,
):
    f = dataset_admission_frontier
    with f[0]() as admission, f[0]() as writer:
        receipt = _frontier_submit(admission, f)
        writer.execute(
            text("UPDATE dataset_versions SET name=:name WHERE id=:id"),
            {"name": "Concurrent harmless name", "id": f[2].id},
        )
        writer.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        writer.commit()
        admission.commit()
        writer.execute(
            text("SELECT mineagent_analysis_plan_check(:id)"), {"id": receipt.plan_id}
        )
        assert (
            writer.scalar(
                text("SELECT name FROM dataset_versions WHERE id=:id"), {"id": f[2].id}
            )
            == "Concurrent harmless name"
        )
        writer.rollback()


def test_dataset_graph_fk_lock_is_released_only_after_admission_savepoint_rollback(
    dataset_admission_frontier,
):
    f = dataset_admission_frontier
    with f[0]() as admission, f[0]() as writer:
        before = _frontier_snapshot(admission)
        nested = admission.begin_nested()
        _frontier_submit(admission, f)
        _frontier_rejected(lambda: _frontier_hash_update(writer, f), state="55P03")
        writer.rollback()
        nested.rollback()
        assert _frontier_snapshot(admission) == before
        _frontier_hash_update(writer, f)
        writer.rollback()
        admission.rollback()
        assert _frontier_snapshot(admission) == before
        admission.rollback()
