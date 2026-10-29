"""Bounded actual file integrity plus owner/terminal admission doubles, no PG."""
# ruff: noqa: F811

import hashlib
import os
from contextlib import contextmanager
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import oe_structured_result_history as service
from backend.app.factor_native_io import native_storage_guard
from backend.app.services import DomainError


def test_missing_native_guard_is_not_a_caller_grant(tmp_path):
    (tmp_path / "factors").mkdir()
    with pytest.raises(ValueError):
        service._Physical(tmp_path)


@pytest.mark.parametrize(
    "change",
    [None, "bytes", "extra", "symlink", "hardlink", "directory", "bound", "deadline"],
)
def test_actual_scoped_inventory_rejects_mutation_and_unknown_files(
    tmp_path, monkeypatch, change
):
    (tmp_path / "factors").mkdir()
    owner, admission = uuid4(), uuid4()
    operation = tmp_path / "research-processes" / str(owner) / str(admission)
    (operation / "control").mkdir(parents=True)
    file = operation / "control" / "stop.json"
    file.write_bytes(b"{}")
    key = file.relative_to(tmp_path).as_posix()
    with native_storage_guard(tmp_path):
        physical = service._Physical(tmp_path)
        physical.process(owner, admission)
        assert physical.read(key, hashlib.sha256(b"{}").hexdigest(), 2) == b"{}"
        if change == "bytes":
            file.write_bytes(b"[]")
        elif change == "extra":
            (operation / "control" / "extra.json").write_bytes(b"{}")
        elif change == "symlink":
            file.unlink()
            file.symlink_to(tmp_path / "outside")
        elif change == "hardlink":
            os.link(file, tmp_path / "alias")
        elif change == "directory":
            (operation / "unknown").mkdir()
        elif change == "bound":
            monkeypatch.setattr(service, "MAX_FILES", 1)
        elif change == "deadline":
            physical.deadline = 0
        if change is None:
            physical.recheck()
        else:
            with pytest.raises((ValueError, OSError)):
                physical.recheck()


@pytest.mark.parametrize(
    "key", ["../outside", "/etc/passwd", "x//y", "x/./y", "x/../y", ""]
)
def test_retained_key_never_escapes_scope(tmp_path, key):
    (tmp_path / "factors").mkdir()
    with native_storage_guard(tmp_path), pytest.raises(ValueError):
        service._Physical(tmp_path).add(key, 10)


@pytest.mark.parametrize("limit", ["rows", "bytes"])
def test_metadata_loader_has_shared_stream_bounds_and_closes_cursor(monkeypatch, limit):
    closed = []

    class Cursor:
        def scalars(self):
            return self

        def __iter__(self):
            return iter([{"id": "a"}, {"id": "b"}])

        def close(self):
            closed.append(True)

    queries = []

    def execute(stmt, params):
        queries.append((str(stmt), params))
        return Cursor()

    monkeypatch.setattr(
        service, "MAX_ROWS" if limit == "rows" else "MAX_METADATA_BYTES", 1
    )
    rows = service._Rows(NS(execute=execute), uuid4(), uuid4())
    with pytest.raises(ValueError):
        rows.read("oe_structured_candidates", "id", "owner_id=:owner AND run_id=:run")
    assert closed == [True]
    assert "owner_id=:owner AND run_id=:run" in queries[0][0]
    assert set(queries[0][1]) == {"owner", "run", "limit"}


@pytest.mark.parametrize(
    "change",
    ["bad_id", "old_schema", "missing", "running", "no_attempt", "sdk_unstopped"],
)
def test_public_factory_rejects_before_loading_values(monkeypatch, tmp_path, change):
    from backend.app import execution_snapshot_history as historical
    from backend.app import oe_structured_identity as identity
    from backend.app import oe_structured_provider_containment as barrier

    owner, execution_id, run_id, attempt_id = (uuid4() for _ in range(4))
    execution = NS(run_id=run_id, attempt_id=attempt_id, generation=1)
    run = NS(id=run_id, status="running" if change == "running" else "succeeded")
    attempt = NS(id=attempt_id, generation=1, finished_at="now", sandbox_stopped=True)
    scalar_rows = iter([None if change == "missing" else execution, run])

    class Session:
        def scalars(self, stmt):
            if "alembic_version" in str(stmt):
                return NS(
                    all=lambda: [
                        "026_rd_seeded_submissions"
                        if change == "old_schema"
                        else "028_oe_structured_search"
                    ]
                )
            return [] if change == "no_attempt" else [attempt]

        def scalar(self, stmt):
            return next(scalar_rows)

    @contextmanager
    def context(*args):
        yield Session()

    monkeypatch.setattr(historical, "historical_session", context)
    monkeypatch.setattr(identity, "require_run_identity", lambda run: NS())
    monkeypatch.setattr(barrier, "provider_attempt_stopped", lambda *args: False)
    monkeypatch.setattr(
        service,
        "_load",
        lambda *args: pytest.fail("source values read before terminal authority"),
    )
    with pytest.raises(DomainError):
        service.read_result(
            NS(),
            NS(storage_root=tmp_path),
            owner_id=str(owner) if change == "bad_id" else owner,
            execution_id=execution_id,
        )


from tests.openevolve.test_structured_backup import physical_graph  # noqa: F401


def test_actual_scoped_controller_inventory_reuses_five_record_validator(
    physical_graph,
):
    from uuid import UUID

    root, admission, indexes, _ = physical_graph
    (root / "factors").mkdir(exist_ok=True)
    with native_storage_guard(root):
        physical = service._Physical(root)
        physical.process(UUID(admission["owner_id"]), UUID(admission["id"]))

        def check(key, sha, size):
            assert len(physical.read(key, sha, size)) == size

        facts = service.cold.validate_controller_closure_unit(
            admission, indexes, physical.records, check, physical.read, set(), set()
        )
        assert facts["completion"] is not None
        physical.recheck()


@pytest.mark.parametrize(
    "source_schema", ["mineagent.factor-research.v2", "mineagent.factor-research.v3"]
)
@pytest.mark.parametrize(
    "research_schema",
    [
        "mineagent.research-restore-closure.v13",
        "mineagent.research-restore-closure.v14",
        "mineagent.research-restore-closure.v15",
        "mineagent.research-restore-closure.v16",
    ],
)
def test_loader_reads_one_run_and_only_referenced_source_rows(
    monkeypatch, research_schema, source_schema
):
    import json

    from tests.openevolve.test_structured_persisted_identity import graph

    _, run, spec, version, _, _ = graph()
    run.status = "failed"
    from backend.app.oe_structured_identity import require_run_identity

    config = require_run_identity(run)
    queries = []

    def row(value):
        return json.loads(
            json.dumps(
                {
                    key: value
                    for key, value in vars(value).items()
                    if not key.startswith("_")
                },
                default=str,
            )
        )

    class Cursor:
        def __init__(self, values):
            self.values = values

        def scalars(self):
            return self

        def __iter__(self):
            return iter(self.values)

        def close(self):
            pass

    def execute(stmt, params):
        query = str(stmt)
        queries.append((query, params))
        table = query.split(" FROM ")[1].split()[0]
        # The outer SELECT has a subquery; choose its actual literal table.
        table = query.rsplit(" FROM ", 1)[1].split()[0]
        values = {
            "research_runs": [
                {
                    "id": str(uuid4()),
                    "purpose": "factor_research",
                    "config": {"schema_version": source_schema},
                    "engine": "rdagent",
                    "status": "succeeded",
                    "experiment_id": None,
                    "split_version_id": None,
                }
            ]
            if "source_run" in params
            else [row(run)],
            "oe_structured_specs": [row(spec)],
            "factor_execution_snapshot_publications": [
                {"id": str(config.fixed_inputs.snapshot_id), "run_id": str(uuid4())}
            ],
            "factor_execution_snapshot_outcomes": [
                {"publication_id": str(config.fixed_inputs.snapshot_id)}
            ],
            "dataset_versions": [{"id": str(config.fixed_inputs.dataset_version_id)}],
            "structured_strategy_versions": [row(version)],
        }.get(table, [])
        return Cursor(values)

    release = []
    monkeypatch.setattr(
        service.cold, "validate_source_release", lambda *args: release.append(args)
    )
    if source_schema == "mineagent.factor-research.v3":
        with pytest.raises(DomainError):
            service._load(
                NS(execute=execute),
                run.owner_id,
                run,
                config,
                research_schema=research_schema,
            )
        assert release == []
        return
    own, shared, _snapshots, _direct, structured = service._load(
        NS(execute=execute), run.owner_id, run, config, research_schema=research_schema
    )
    assert own["schema_version"] == (
        "mineagent.oe-structured-restore-closure.v2"
        if research_schema == "mineagent.research-restore-closure.v16"
        else "mineagent.oe-structured-restore-closure.v1"
    )
    assert own["tables"]["oe_structured_specs"][0]["run_id"] == str(run.id)
    assert shared["schema_version"] == research_schema
    assert set(own["tables"]) == set(service.cold.FIELDS) and len(release) == 1
    assert len(shared["external_bindings"]["runs"]) == 1
    for query, params in queries:
        assert " WHERE " in query and " LIMIT :limit" in query
        assert params["owner"] == run.owner_id and params["run"] == run.id
        if any(f" FROM {table} " in query for table in service.cold.FIELDS):
            assert "run_id=:run" in query
    source_queries = [
        q
        for q, _ in queries
        if " FROM dataset_versions " in q or " FROM data_usage_exposures " in q
    ]
    assert all(
        "=:dataset" in q or "source_key=:source_key" in q for q in source_queries
    )
    assert structured["tables"]["structured_strategy_versions"][0]["id"] == str(
        version.id
    )


@pytest.mark.parametrize("foreign", ["owner", "run"])
def test_reverse_transport_alias_is_loaded_then_rejected(foreign):
    from backend.app.oe_structured_identity import require_run_identity
    from tests.openevolve.test_structured_persisted_identity import graph

    _, run, _, _, _, _ = graph()
    queries = []
    operation_id = str(uuid4())

    class Cursor:
        def __init__(self, values):
            self.values = values

        def scalars(self):
            return self

        def __iter__(self):
            return iter(self.values)

        def close(self):
            pass

    def execute(stmt, params):
        query = str(stmt)
        if " FROM research_model_operations " in query:
            return Cursor(
                [
                    {
                        "id": operation_id,
                        "owner_id": str(run.owner_id),
                        "run_id": str(run.id),
                    }
                ]
            )
        if " FROM research_model_transports " in query:
            queries.append((query, params))
            return Cursor(
                [
                    {
                        "id": str(uuid4()),
                        "owner_id": str(uuid4())
                        if foreign == "owner"
                        else str(run.owner_id),
                        "run_id": str(uuid4()) if foreign == "run" else str(run.id),
                        "operation_id": operation_id,
                    }
                ]
            )
        return Cursor([])

    with pytest.raises(ValueError, match="foreign run/owner"):
        service._load(NS(execute=execute), run.owner_id, run, require_run_identity(run))
    assert queries[0][1]["operation_ids"] == [operation_id]
    assert "OR operation_id=ANY" in queries[0][0]


@pytest.mark.parametrize("extra", [False, True])
def test_scoped_process_pass_keeps_central_reverse_file_check(physical_graph, extra):
    from uuid import UUID

    from backend.app.research_backup import _validate_processes
    from packages.strategy_sdk import parse_json

    root, admission, indexes, _ = physical_graph
    (root / "factors").mkdir(exist_ok=True)
    indexes["research_process_admissions"] = {admission["id"]: admission}
    if extra:
        (
            root
            / "research-processes"
            / admission["owner_id"]
            / admission["id"]
            / "control"
            / "unregistered.json"
        ).write_bytes(b"{}")
    with native_storage_guard(root):
        physical = service._Physical(root)
        physical.process(UUID(admission["owner_id"]), UUID(admission["id"]))

        def check(key, sha, size):
            assert len(physical.read(key, sha, size)) == size

        def read_json(key):
            return parse_json(
                physical.read(key, physical.records[key]["sha256"], 16384)
            )

        if extra:
            with pytest.raises(ValueError, match="Unknown process"):
                _validate_processes(
                    indexes,
                    physical.records,
                    check,
                    read_json,
                    research_schema=service.cold.RESEARCH_SCHEMA,
                    read_bytes=physical.read,
                )
        else:
            result = _validate_processes(
                indexes,
                physical.records,
                check,
                read_json,
                research_schema=service.cold.RESEARCH_SCHEMA,
                read_bytes=physical.read,
            )
            assert result["structured"][admission["id"]]["completion"] is not None


def test_normal_history_fresh_import_has_no_model_registration_cycle():
    import subprocess
    import sys

    subprocess.run(
        [sys.executable, "-c", "import backend.app.oe_structured_result_history"],
        check=True,
        capture_output=True,
        timeout=15,
    )


def test_checkpoint_file_base64_uses_typed_document_limit_not_default_json_string(
    tmp_path,
):
    import json

    (tmp_path / "factors").mkdir()
    key = "checkpoint-file.json"
    payload = {"data_base64": "A" * 699052}
    raw = json.dumps(payload).encode()
    (tmp_path / key).write_bytes(raw)
    with native_storage_guard(tmp_path):
        physical = service._Physical(tmp_path)
        physical.add(key, 1048576)
        assert physical.json(key) == payload


@pytest.mark.parametrize(
    "research_schema",
    [
        "mineagent.research-restore-closure.v11",
        "mineagent.research-restore-closure.v17",
        "mineagent.oe-structured-restore-closure.v2",
        None,
        "",
        14,
    ],
)
def test_scoped_reader_rejects_other_versions_before_sql(research_schema):
    from backend.app.oe_structured_identity import require_run_identity
    from tests.openevolve.test_structured_persisted_identity import graph

    _, run, _, _, _, _ = graph()

    def forbidden(*args, **kwargs):
        raise AssertionError("Unsupported closure must reject before SQL")

    with pytest.raises(ValueError, match="Unsupported scoped normal research schema"):
        service._load(
            NS(execute=forbidden),
            run.owner_id,
            run,
            require_run_identity(run),
            research_schema=research_schema,
        )


@pytest.mark.parametrize(
    "change",
    [
        None,
        "old_schema",
        "wrong_owner",
        "wrong_dataset",
        "wrong_path",
        "changed_csv",
        "missing_csv",
        "file_limit",
    ],
)
def test_declared_source_inventory_retains_actual_original_files(
    tmp_path, monkeypatch, change
):
    import json

    from tests.rdagent.test_declared_upload_source import OWNER, TARGET, build

    basket = build()
    key = f"datasets/{OWNER}/{TARGET}"
    manifest = json.loads(basket.manifest_bytes)
    for name, raw in basket.files:
        path = tmp_path / key / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    (tmp_path / "factors").mkdir()
    dataset = {
        "id": str(TARGET),
        "owner_id": str(OWNER),
        "storage_key": key,
        "manifest": manifest,
    }
    config = NS(fixed_inputs=NS(dataset_version_id=TARGET))
    schema = "mineagent.research-restore-closure.v15"
    if change == "old_schema":
        schema = "mineagent.research-restore-closure.v14"
    if change == "wrong_owner":
        dataset["owner_id"] = str(uuid4())
    if change == "wrong_dataset":
        dataset["id"] = str(uuid4())
    if change == "wrong_path":
        dataset["storage_key"] += "/../other"
    raw_csv = tmp_path / key / "sources/0/source.csv"
    if change == "changed_csv":
        raw_csv.write_bytes(raw_csv.read_bytes() + b" ")
    if change == "missing_csv":
        raw_csv.unlink()
    if change == "file_limit":
        monkeypatch.setattr(service, "MAX_FILES", 1)
    with native_storage_guard(tmp_path):
        physical = service._Physical(tmp_path)
        args = (
            physical,
            {"tables": {"dataset_versions": [dataset]}},
            config,
            OWNER,
            schema,
        )
        if change is not None:
            with pytest.raises((ValueError, OSError)):
                service._add_declared_source_inventory(*args)
        else:
            service._add_declared_source_inventory(*args)
            assert set(physical.records) == {
                key + "/" + name for name, _ in basket.files
            }
            for name, raw in basket.files:
                record = physical.records[key + "/" + name]
                assert (
                    physical.read(
                        record["path"], record["sha256"], record["size_bytes"]
                    )
                    == raw
                )
            physical.recheck()
