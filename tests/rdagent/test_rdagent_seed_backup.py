"""Real parent bytes and immutable declaration reconstruction; no PG acceptance."""

from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from test_engine_run_preflight import request_body  # noqa:F401
from test_rdagent_configured_source import retained  # noqa:F401
from test_rdagent_configured_source_service import setup_source  # noqa:F401
from test_rdagent_seed_parent import retained_parent  # noqa:F401
from test_rdagent_seed_submission import seeded_writer  # noqa:F401
from test_rdagent_submission_service import queued_writer  # noqa:F401

from backend.app import rdagent_seed_backup as backup
from backend.app.factor_analysis_models import FactorAnalysisPlan
from backend.app.models import OutboxEvent, ResearchRun
from backend.app.rdagent_models import FactorResearchSpec
from backend.app.rdagent_seed_models import FactorSeededSubmission
from backend.app.rdagent_seed_submission import submit_seeded_run
from backend.app.research_broker_models import ResearchBudgetControl


def json_value(value):
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    return value


def record(row, fields=None):
    names = fields.split() if fields else [col.name for col in row.__table__.columns]
    return {key: json_value(getattr(row, key)) for key in names}


@pytest.fixture
def cold(seeded_writer):  # noqa:F811
    q, parent, body, _ = seeded_writer
    submit_seeded_run(q.session, parent.owner, body, "seed-cold", q.source.settings)
    rows = q.session.rows
    one = lambda cls: next(row for row in rows if isinstance(row, cls))
    seed, outbox = one(FactorSeededSubmission), one(OutboxEvent)
    seed.created_at = outbox.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    closure = {
        "schema_version": backup.SCHEMA,
        "tables": {
            "factor_seeded_submissions": [
                record(seed, backup.FIELDS["factor_seeded_submissions"])
            ],
            "outbox_events": [record(outbox, backup.FIELDS["outbox_events"])],
        },
    }
    kwargs = {
        "rd_source_closure": {
            "schema_version": backup.RD,
            "tables": {
                "factor_research_specs": [record(one(FactorResearchSpec))],
                "factor_generation_starts": [],
                "factor_generation_sources": [],
            },
            "external_bindings": {"runs": [record(one(ResearchRun))]},
        },
        "research_closure": {
            "schema_version": backup.RESEARCH,
            "tables": {
                "research_budget_controls": [record(one(ResearchBudgetControl))],
            },
        },
        "factor_closure": {
            "schema_version": "mineagent.factor-restore-closure.v2",
            "tables": {
                "factors": [json_value(vars(parent.factor))],
                "factor_versions": [json_value(vars(parent.version))],
                "factor_objects": [json_value(vars(parent.obj))],
            },
        },
        "direct_analysis_closure": {
            "schema_version": "mineagent.direct-analysis-restore-closure.v1",
            "tables": {
                "factor_analysis_plans": [record(one(FactorAnalysisPlan))],
            },
        },
        "rd_submission_closure": {
            "schema_version": "mineagent.rd-submission-restore-closure.v1",
            "tables": {
                "factor_research_submissions": [],
                "outbox_events": [],
            },
        },
        "maximum_rows": 100,
    }
    calls = []

    def read(key, sha, cap):
        assert (key, sha, cap) == (parent.obj.storage_key, parent.obj.sha256, 131072)
        calls.append(key)
        return parent.path.read_bytes()

    kwargs["read_bytes"] = read
    return SimpleNamespace(closure=closure, kwargs=kwargs, parent=parent, calls=calls)


def test_reconstructs_seed_from_real_owned_version_and_retained_bytes(cold):
    result = backup.validate_closure(cold.closure, **cold.kwargs)
    row = cold.closure["tables"]["factor_seeded_submissions"][0]
    assert tuple(result) == (row["run_id"],)
    seed = result[row["run_id"]]
    assert seed.submission_id == UUID(row["id"])
    assert seed.parent.source.encode() == cold.parent.path.read_bytes()
    assert seed.parent.reference.factor_id == cold.parent.factor.id
    assert len(cold.calls) == 1
    with pytest.raises(TypeError):
        result[row["run_id"]] = seed


@pytest.mark.parametrize(
    "field",
    [
        "parent_factor_id",
        "parent_code_sha256",
        "parent_definition_sha256",
        "parent_binding_sha256",
        "preflight_sha256",
        "runtime_profile_sha256",
        "config_sha256",
    ],
)
def test_swapped_seed_metadata_fails_before_bootstrap_authority(cold, field):
    row = cold.closure["tables"]["factor_seeded_submissions"][0]
    row[field] = str(uuid4()) if field.endswith("_id") else "f" * 64
    with pytest.raises((ValueError, KeyError)):
        backup.validate_closure(cold.closure, **cold.kwargs)


def test_actual_retained_parent_tamper_rejected(cold):
    cold.parent.path.write_bytes(b"not the retained parent")
    with pytest.raises(ValueError, match="actual retained"):
        backup.validate_closure(cold.closure, **cold.kwargs)


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "missing_outbox",
        "foreign_outbox",
        "fresh_family_key",
        "old_rd",
        "old_research",
        "future_research",
        "foreign_factor",
        "wrong_child",
    ],
)
def test_seed_graph_requires_exact_closed_owner_family_and_lineage(cold, mutation):
    row = cold.closure["tables"]["factor_seeded_submissions"][0]
    if mutation == "duplicate":
        cold.closure["tables"]["factor_seeded_submissions"].append(deepcopy(row))
    elif mutation == "missing_outbox":
        cold.closure["tables"]["outbox_events"].clear()
    elif mutation == "foreign_outbox":
        cold.closure["tables"]["outbox_events"][0]["run_id"] = str(uuid4())
    elif mutation == "fresh_family_key":
        cold.kwargs["rd_submission_closure"]["tables"][
            "factor_research_submissions"
        ].append(
            {
                "owner_id": row["owner_id"],
                "idempotency_key": row["idempotency_key"],
                "run_id": str(uuid4()),
                "plan_id": str(uuid4()),
            }
        )
    elif mutation == "old_rd":
        cold.kwargs["rd_source_closure"]["schema_version"] = (
            "mineagent.rd-source-restore-closure.v4"
        )
    elif mutation in {"old_research", "future_research"}:
        cold.kwargs["research_closure"]["schema_version"] = (
            "mineagent.research-restore-closure."
            + ("v9" if mutation == "old_research" else "v11")
        )
    elif mutation == "foreign_factor":
        cold.kwargs["factor_closure"]["tables"]["factors"][0]["owner_id"] = str(uuid4())
    elif mutation == "wrong_child":
        cold.kwargs["rd_source_closure"]["tables"]["factor_generation_starts"].append(
            {
                "run_id": row["run_id"],
                "owner_id": row["owner_id"],
                "logical_step": 0,
                "parent_factor_version_id": None,
            }
        )
    with pytest.raises((ValueError, KeyError)):
        backup.validate_closure(cold.closure, **cold.kwargs)


@pytest.mark.parametrize("bound", ["rows", "bytes"])
def test_seed_reader_closes_stream_before_unbounded_materialization(bound):
    class Cursor:
        consumed = 0
        closed = False

        def scalars(self):
            for _ in range(100):
                self.consumed += 1
                yield {"payload": "x" * 100}

        def close(self):
            self.closed = True

    cursor = Cursor()

    class Connection:
        def execute(self, statement, parameters):
            assert statement.get_execution_options()["stream_results"] is True
            assert statement.get_execution_options()["yield_per"] == 1
            assert parameters["limit"] == (2 if bound == "rows" else 101)
            return cursor

    with pytest.raises(ValueError, match="bound"):
        backup.read_closure(
            Connection(),
            1 if bound == "rows" else 100,
            maximum_bytes=1_000 if bound == "rows" else 10,
        )
    assert cursor.closed
    assert cursor.consumed == (2 if bound == "rows" else 1)


def test_prepared_parent_rebuilds_explicit_bootstrap_and_never_changes_fresh(cold):
    from packages.factors.contracts import canonical_bytes
    from packages.rdagent_adapter.contracts import FactorResearchConfig
    from packages.rdagent_adapter.operational_bootstrap import RDOperationalBootstrap
    from packages.rdagent_adapter.seeded_bootstrap import RDSeededOperationalBootstrap

    context = backup.validate_closure(cold.closure, **cold.kwargs)
    run = cold.kwargs["rd_source_closure"]["external_bindings"]["runs"][0]
    spec = cold.kwargs["rd_source_closure"]["tables"]["factor_research_specs"][0]
    config = FactorResearchConfig.model_validate_json(canonical_bytes(run["config"]))
    base = RDOperationalBootstrap(
        owner_id=UUID(run["owner_id"]),
        run_id=UUID(run["id"]),
        attempt_id=uuid4(),
        generation=1,
        spec_id=UUID(spec["id"]),
        attempt_binding_id=uuid4(),
        config=config,
        config_sha256=config.sha256,
        input_sha256=config.input.sha256,
    )
    seeded = backup.seeded_bootstrap(base, context)
    assert type(seeded) is RDSeededOperationalBootstrap
    assert seeded.seed.source.encode() == cold.parent.path.read_bytes()
    assert seeded.seed.reference.factor_version_id == cold.parent.version.id
    assert backup.seeded_bootstrap(base, {}) is base
    with pytest.raises(ValueError, match="prepared seed"):
        backup.seeded_bootstrap(base, {str(base.run_id): seeded.seed})


@pytest.mark.parametrize("seeded", [False, True])
def test_new_config_bootstrap_preserves_explicit_model_and_unknown_pricing(
    cold, seeded
):
    from packages.factors.contracts import canonical_bytes
    from packages.rdagent_adapter.provider_config import ProviderFactorResearchConfig

    context = backup.validate_closure(cold.closure, **cold.kwargs)
    run = cold.kwargs["rd_source_closure"]["external_bindings"]["runs"][0]
    raw = {
        **run["config"],
        "schema_version": "mineagent.factor-research.v2",
        "model_profile": "configured.rdagent.v1",
        "model_binding": {
            "profile_id": "offline-declared",
            "profile_sha256": "a" * 64,
            "chat_model": "declared-chat",
            "embedding_model": "declared-embedding",
            "embedding_dimensions": 16,
            "max_output_tokens": 1024,
            "request_deadline_ms": 5000,
            "pricing": {"status": "unavailable"},
        },
    }
    from packages.rdagent_adapter.steer import SteerSnapshot

    snapshot = SteerSnapshot(owner_id=UUID(run["owner_id"]))
    raw["steer_snapshot"] = snapshot.model_dump(mode="json")
    raw["steer_snapshot_sha256"] = snapshot.sha256
    config = ProviderFactorResearchConfig.model_validate_json(canonical_bytes(raw))
    result = backup.build_expected_bootstrap(
        seed_context=context if seeded else {},
        owner_id=UUID(run["owner_id"]),
        run_id=UUID(run["id"]),
        attempt_id=uuid4(),
        generation=1,
        spec_id=uuid4(),
        attempt_binding_id=uuid4(),
        config=config,
        config_sha256=config.sha256,
        input_sha256=config.input.sha256,
    )
    assert result.schema_version == (
        "mineagent.rdagent-provider-seeded-bootstrap.v1"
        if seeded
        else "mineagent.rdagent-provider-operational-bootstrap.v1"
    )
    assert result.config.model_binding.model_dump(mode="json")["pricing"] == {
        "status": "unavailable"
    }
    assert result.config.model_binding.embedding_model == "declared-embedding"
    assert "endpoint" not in canonical_bytes(result).decode()
    if seeded:
        assert result.seed.source.encode() == cold.parent.path.read_bytes()


@pytest.mark.parametrize(
    "research_version,source_version,allowed",
    [
        (10, 5, True),
        (11, 6, True),
        (12, 7, True),
        (10, 6, False),
        (11, 5, False),
        (11, 7, False),
        (12, 6, False),
    ],
)
def test_seed_parent_closure_versions_are_exact_pairs(
    cold, research_version, source_version, allowed
):
    closure, kwargs = cold.closure, cold.kwargs
    kwargs["research_closure"]["schema_version"] = (
        f"mineagent.research-restore-closure.v{research_version}"
    )
    kwargs["rd_source_closure"]["schema_version"] = (
        f"mineagent.rd-source-restore-closure.v{source_version}"
    )
    if allowed:
        backup.validate_closure(closure, **kwargs)
    else:
        with pytest.raises(ValueError):
            backup.validate_closure(closure, **kwargs)
