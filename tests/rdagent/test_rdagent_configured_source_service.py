"""Real retained files and typed labels; DB/fence doubles, no live/PIT claim."""

import json
import os
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_rdagent_configured_source import encoded, retained, sha  # noqa: F401
from test_rdagent_contracts import research_dict

from backend.app import rdagent_configured_source as service
from backend.app.factor_analysis_models import FactorAnalysisPlan
from backend.app.models import DatasetVersion
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.qlib_adapter.contracts import LabelManifest, LabelRow
from packages.qlib_adapter.objective import DirectObjectiveDeclaration
from packages.rdagent_adapter.configured_source import parse_retained_source
from packages.rdagent_adapter.contracts import FactorResearchConfig


@pytest.fixture
def setup_source(tmp_path, retained, monkeypatch):  # noqa: F811
    args = encoded(retained)
    args["input_names"] = ("volume",)
    source = parse_retained_source(**args)
    owner = uuid4()
    key = f"datasets/{owner}/{retained['dataset_id']}"
    directory = tmp_path / key
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_bytes(args["manifest_bytes"])
    for name, content in args["artifacts"].items():
        (directory / name).write_bytes(content)
    dataset = DatasetVersion(
        id=retained["dataset_id"],
        owner_id=owner,
        storage_key=key,
        content_sha256=args["manifest_sha256"],
        manifest=json.loads(args["manifest_bytes"]),
        start_date=date(2024, 3, 11),
        end_date=date(2024, 3, 14),
        market="US",
    )
    raw = research_dict()
    raw["input"].update(
        dataset=source.dataset.model_dump(mode="json"),
        calculation_universe=source.universe.model_dump(mode="json"),
        calendar_sha256=source.calendar.sha256,
        market_timezone=source.calendar.timezone,
        input_fields=[x.model_dump(mode="json") for x in source.input_fields],
        window={"start": "2024-03-12", "end": "2024-03-13"},
        lookback_sessions=1,
        lag_sessions=0,
    )
    config = FactorResearchConfig.model_validate(raw)
    fence = SimpleNamespace(owner_id=owner, run_id=uuid4())
    spec = SimpleNamespace(id=uuid4())
    objective = DirectObjectiveDeclaration(
        window=config.input.window,
        label_horizon_sessions=1,
        metric="ic",
        direction="at_least",
        threshold=0.0,
        minimum_defined_days=1,
        minimum_pairs=2,
    )
    plan = FactorAnalysisPlan(
        id=uuid4(),
        owner_id=owner,
        run_id=fence.run_id,
        spec_id=spec.id,
        config_sha256=config.sha256,
        input_sha256=config.input.sha256,
        source_policy=args["policy"].model_dump(mode="json"),
        source_policy_sha256=digest(args["policy"].model_dump(mode="json")),
        label_source_dataset_id=dataset.id,
        label_source_manifest_sha256=dataset.content_sha256,
        objective=objective.model_dump(mode="json"),
        objective_canonical=canonical_bytes(objective).decode("ascii"),
        objective_sha256=objective.sha256,
    )
    queries = []

    class Session:
        def scalar(self, query):
            queries.append(query)
            entity = query.column_descriptions[0]["entity"]
            return plan if entity is FactorAnalysisPlan else dataset

    session = Session()
    scopes = []
    monkeypatch.setattr(
        service,
        "require_neutral_transaction",
        lambda s, f, **kw: scopes.append((s, f, kw)),
    )
    monkeypatch.setattr(service, "require_research_spec", lambda s, f: (spec, config))
    return SimpleNamespace(
        args=args,
        source=source,
        dataset=dataset,
        directory=directory,
        settings=SimpleNamespace(storage_root=tmp_path),
        session=session,
        fence=fence,
        config=config,
        plan=plan,
        queries=queries,
        scopes=scopes,
    )


def preflight(s):
    return service.resolve_dataset_preflight(
        s.session,
        owner_id=s.fence.owner_id,
        dataset_id=s.dataset.id,
        policy=s.args["policy"],
        input_names=("volume",),
        settings=s.settings,
    )


def test_real_files_fenced_config_and_exact_owner_queries(setup_source):
    s = setup_source
    assert preflight(s) == s.source
    assert service.resolve_configured_source(s.session, s.fence, s.settings) == s.source
    assert s.scopes == [(s.session, s.fence, {"resource_scope": "factor_objects"})]
    for query in s.queries:
        params = query.compile().params
        assert s.fence.owner_id in params.values()
        if query.column_descriptions[0]["entity"] is DatasetVersion:
            assert s.dataset.id in params.values()
        else:
            assert s.fence.run_id in params.values()
            assert query._for_update_arg is not None
    assert s.plan.source_policy_sha256 == digest(s.plan.source_policy)
    assert s.source.dataset.availability_revision_sha256 != s.plan.source_policy_sha256


def test_forward_close_labels_separate_from_volume_candidate(setup_source):
    s = setup_source
    labels = service.resolve_configured_labels(s.session, s.fence, s.settings)
    assert type(labels.manifest) is LabelManifest
    assert len(labels.rows) == 2 and all(type(row) is LabelRow for row in labels.rows)
    assert labels.manifest.rows_sha256 == digest(
        [row.model_dump(mode="json") for row in labels.rows]
    )
    assert labels.manifest.horizon_sessions == 1
    for row in labels.rows:
        assert row.session == date(2024, 3, 12) and row.horizon_end == date(2024, 3, 13)
        assert row.value == pytest.approx(114 / 113 - 1)
        assert row.available_at == s.source.calendar.sessions[2].close_time
    assert [x.name for x in s.source.input_fields] == ["volume"]
    assert all(row.values == (1000.0,) for row in s.source.observations)
    assert (
        labels.qualification
        == "retained_declared_source_not_independently_verified_pit"
    )
    assert labels.source_policy_sha256 == s.source.policy_sha256


@pytest.mark.parametrize("name", ["manifest.json", "preview.json", "sessions.json"])
def test_each_use_rereads_actual_bytes(setup_source, name):
    s = setup_source
    service.resolve_configured_labels(s.session, s.fence, s.settings)
    path = s.directory / name
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(DomainError):
        service.resolve_configured_labels(s.session, s.fence, s.settings)


@pytest.mark.parametrize("name", ["manifest.json", "preview.json", "sessions.json"])
@pytest.mark.parametrize("mode", ["symlink", "hardlink", "empty", "directory"])
def test_no_follow_regular_single_link(setup_source, name, mode):
    s = setup_source
    path = s.directory / name
    content = path.read_bytes()
    path.unlink()
    target = s.settings.storage_root / "external"
    target.write_bytes(content)
    if mode == "symlink":
        path.symlink_to(target)
    elif mode == "hardlink":
        os.link(target, path)
    elif mode == "empty":
        path.write_bytes(b"")
    else:
        path.mkdir()
    with pytest.raises(DomainError):
        preflight(s)


def test_symlink_ancestor_rejected(setup_source):
    s = setup_source
    moved = s.directory.with_name("moved")
    s.directory.rename(moved)
    s.directory.symlink_to(moved, target_is_directory=True)
    with pytest.raises(DomainError):
        preflight(s)


@pytest.mark.parametrize("limit", ["MANIFEST_LIMIT", "ARTIFACT_LIMIT", "TOTAL_LIMIT"])
def test_all_byte_caps_fail_closed(setup_source, monkeypatch, limit):
    s = setup_source
    monkeypatch.setattr(service, limit, 1)
    with pytest.raises(DomainError):
        preflight(s)


@pytest.mark.parametrize(
    "field,value",
    [
        ("storage_key", "datasets/../../escape"),
        ("content_sha256", "f" * 64),
        ("manifest", {}),
        ("start_date", date(2024, 3, 12)),
        ("end_date", date(2024, 3, 13)),
        ("market", "CN"),
    ],
)
def test_dataset_metadata_drift(setup_source, field, value):
    s = setup_source
    setattr(s.dataset, field, value)
    with pytest.raises(DomainError):
        preflight(s)


@pytest.mark.parametrize(
    "field",
    [
        "spec_id",
        "config_sha256",
        "input_sha256",
        "label_source_dataset_id",
        "label_source_manifest_sha256",
        "source_policy_sha256",
    ],
)
def test_plan_binding_drift(setup_source, field):
    s = setup_source
    setattr(s.plan, field, uuid4() if field.endswith("_id") else "f" * 64)
    with pytest.raises(DomainError):
        service.resolve_configured_source(s.session, s.fence, s.settings)


def test_objective_hash_drift(setup_source):
    s = setup_source
    s.plan.objective_sha256 = "f" * 64
    with pytest.raises(DomainError):
        service.resolve_configured_labels(s.session, s.fence, s.settings)


@pytest.mark.parametrize("horizon", [2, 3])
def test_label_horizon_requires_actual_retained_future_session(setup_source, horizon):
    s = setup_source
    s.plan.objective["label_horizon_sessions"] = horizon
    s.plan.objective_sha256 = digest(s.plan.objective)
    s.plan.objective_canonical = canonical_bytes(s.plan.objective).decode("ascii")
    with pytest.raises(DomainError):
        service.resolve_configured_labels(s.session, s.fence, s.settings)


def test_read_replacement_detected_after_fd_open(setup_source, monkeypatch):
    s = setup_source
    path = s.directory / "preview.json"
    original = service.os.read
    changed = False

    def read(fd, count):
        nonlocal changed
        data = original(fd, count)
        if not changed:
            changed = True
            replacement = path.with_name("replacement")
            replacement.write_bytes(path.read_bytes())
            replacement.replace(path)
        return data

    monkeypatch.setattr(service.os, "read", read)
    with pytest.raises(DomainError):
        service.read_dataset_artifact(
            s.settings.storage_root,
            s.dataset.storage_key,
            "preview.json",
            sha(s.args["artifacts"]["preview.json"]),
            service.ARTIFACT_LIMIT,
        )


@pytest.mark.parametrize(
    "field,value",
    [("lookback_sessions", 2), ("lag_sessions", 2), ("calendar_sha256", "f" * 64)],
)
def test_exact_config_source_and_history_bounds(
    setup_source, monkeypatch, field, value
):
    s = setup_source
    raw = s.config.model_dump(mode="json")
    raw["input"][field] = value
    config = FactorResearchConfig.model_validate(raw)
    spec = SimpleNamespace(id=s.plan.spec_id)
    s.plan.config_sha256 = config.sha256
    s.plan.input_sha256 = config.input.sha256
    monkeypatch.setattr(
        service, "require_research_spec", lambda session, fence: (spec, config)
    )
    with pytest.raises(DomainError):
        service.resolve_configured_source(s.session, s.fence, s.settings)


def test_stable_reader_deadline_is_bounded(setup_source, monkeypatch):
    s = setup_source
    ticks = iter((100.0, 111.0))
    monkeypatch.setattr(service.time, "monotonic", lambda: next(ticks))
    with pytest.raises(DomainError):
        service.read_dataset_artifact(
            s.settings.storage_root,
            s.dataset.storage_key,
            "preview.json",
            sha(s.args["artifacts"]["preview.json"]),
            service.ARTIFACT_LIMIT,
        )
