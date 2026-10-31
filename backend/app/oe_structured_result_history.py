"""Bounded owner-only normal027 result integrity, separate from disclosure.

No live fence, Peer, current worker grant, release creation or 025 run identity.
The fixed loader retains one normal run and its immutable source dependencies.
"""

import hashlib
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import UUID

from sqlalchemy import select, text

from backend.app import oe_structured_backup as cold
from backend.app.core.atomic_storage import _open_directory
from backend.app.factor_native_io import require_native_storage_guard
from backend.app.services import DomainError
from backend.app.structured_factor_history import HistoricalStructuredExecution
from packages.factors.contracts import canonical_bytes
from packages.openevolve_structured.objective import StructuredMeasurement
from packages.research_runtime.process_contracts import ProcessError

MAX_ROWS = 8192
# Three bounded 16 MiB results may each be escaped once in SQL JSON rows.
MAX_METADATA_BYTES = 128 * 1024**2
MAX_PHYSICAL_BYTES = 512 * 1024**2
MAX_FILES = 2048


@dataclass(frozen=True)
class HistoricalNormalExecution:
    execution: HistoricalStructuredExecution
    candidate_id: UUID
    measurement: StructuredMeasurement
    measurement_sha256: str
    config_sha256: str
    qualification: str = (
        "owned_normal_search_integrity_not_disclosure_or_live_authority"
    )


def fail():
    raise DomainError(
        "oe_structured_history_unavailable",
        "Complete owned normal result is unavailable",
        409,
    )


class _Rows:
    """One read transaction's finite literal SQL projections, not a graph framework."""

    def __init__(self, session, owner, run):
        self.session, self.params = (
            session,
            {"owner": owner, "run": run, "limit": MAX_ROWS + 1},
        )
        self.count = self.size = 0

    def read(self, table, fields, where, **params):
        cursor = self.session.execute(
            text(
                f"SELECT to_jsonb(t) FROM (SELECT {','.join(fields.split())} FROM {table} WHERE {where} LIMIT :limit) t"
            ).execution_options(stream_results=True, yield_per=1),
            {**self.params, **params},
        ).scalars()
        rows = []
        try:
            for row in cursor:
                self.count += 1
                self.size += len(canonical_bytes(row))
                if self.count > MAX_ROWS or self.size > MAX_METADATA_BYTES:
                    raise ValueError(
                        "Normal result retained graph exceeds finite bound"
                    )
                rows.append(row)
        finally:
            cursor.close()
        return rows


class _Physical:
    """Pinned actual files only from the selected run or exact retained source keys."""

    def __init__(self, root):
        require_native_storage_guard(root)
        self.root = Path(root)
        self.deadline = time.monotonic() + 60
        self.records = {}
        self.total = 0
        self.prefixes = {}

    def check(self):
        require_native_storage_guard(self.root)
        if time.monotonic() >= self.deadline:
            raise ValueError("Normal historical read deadline")

    @staticmethod
    def key(key):
        if (
            type(key) is not str
            or not key
            or PurePosixPath(key).is_absolute()
            or any(part in {"", ".", ".."} for part in key.split("/"))
        ):
            raise ValueError("Noncanonical retained path")
        return key

    def _read(self, key, maximum, *, content):
        self.check()
        self.key(key)
        if type(maximum) is not int or not 0 <= maximum <= MAX_PHYSICAL_BYTES:
            raise ValueError("Normal historical file bound")
        parent = _open_directory(self.root.absolute() / Path(key).parent)
        try:
            fd = os.open(
                Path(key).name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent,
            )
            try:
                before = os.fstat(fd)
                if (
                    not stat.S_ISREG(before.st_mode)
                    or before.st_nlink != 1
                    or before.st_size > maximum
                ):
                    raise ValueError("Unsafe normal retained inode")
                total, sha, pieces = 0, hashlib.sha256(), []
                while block := os.read(fd, 65536):
                    self.check()
                    total += len(block)
                    if total > maximum:
                        raise ValueError("Retained file grew")
                    sha.update(block)
                    if content:
                        pieces.append(block)
                after = os.fstat(fd)
                named = os.stat(Path(key).name, dir_fd=parent, follow_symlinks=False)
                identity = lambda info: (
                    info.st_dev,
                    info.st_ino,
                    info.st_mode,
                    info.st_nlink,
                    info.st_size,
                    info.st_mtime_ns,
                    info.st_ctime_ns,
                )
                if (
                    identity(before) != identity(after)
                    or identity(after) != identity(named)
                    or total != before.st_size
                ):
                    raise ValueError("Normal retained file changed during read")
                return {
                    "path": key,
                    "sha256": sha.hexdigest(),
                    "size_bytes": total,
                }, b"".join(pieces)
            finally:
                os.close(fd)
        finally:
            os.close(parent)

    def add(self, key, maximum):
        record, _ = self._read(key, maximum, content=False)
        old = self.records.get(key)
        if old is not None and old != record:
            raise ValueError("Retained inventory changed")
        if old is None:
            self.total += record["size_bytes"]
            self.records[key] = record
            if len(self.records) > MAX_FILES or self.total > MAX_PHYSICAL_BYTES:
                raise ValueError("Normal physical graph exceeds bound")
        return record

    def read(self, key, sha, maximum):
        record, raw = self._read(key, maximum, content=True)
        if record["sha256"] != sha or (
            key in self.records and self.records[key] != record
        ):
            raise ValueError("Normal actual retained bytes differ")
        return raw

    def json(self, key):
        from packages.strategy_sdk import JsonLimits, parse_json

        record = self.records[key]
        # Native checkpoint FileDocument base64 legitimately exceeds 64 KiB;
        # its own typed codec retains the stricter document-specific bound.
        return parse_json(
            self.read(key, record["sha256"], 1048576),
            limits=JsonLimits(
                max_bytes=1048576, max_nodes=100000, max_items=10000, max_string=1048576
            ),
        )

    def process(self, owner, admission):
        prefix = f"research-processes/{owner}/{admission}"
        self.key(prefix)
        base = _open_directory(self.root.absolute() / prefix)
        entries = 0
        observed = set()

        def scan(fd, key, depth):
            nonlocal entries
            self.check()
            if depth > 3:
                raise ValueError("Unexpected process directory depth")
            before = os.fstat(fd)
            with os.scandir(fd) as names:
                for item in names:
                    entries += 1
                    if entries > MAX_FILES:
                        raise ValueError("Process inventory exceeds bound")
                    child = os.open(
                        item.name,
                        os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                        dir_fd=fd,
                    )
                    try:
                        info = os.fstat(child)
                        path = key + "/" + item.name
                        if stat.S_ISDIR(info.st_mode):
                            if depth != 0 or item.name not in {
                                "control",
                                "inputs",
                                "output",
                            }:
                                raise ValueError("Unknown process directory")
                            scan(child, path, depth + 1)
                        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                            self.add(path, MAX_PHYSICAL_BYTES)
                            observed.add(path)
                            named = os.stat(item.name, dir_fd=fd, follow_symlinks=False)
                            if (
                                info.st_dev,
                                info.st_ino,
                                info.st_size,
                                info.st_mtime_ns,
                                info.st_ctime_ns,
                            ) != (
                                named.st_dev,
                                named.st_ino,
                                named.st_size,
                                named.st_mtime_ns,
                                named.st_ctime_ns,
                            ):
                                raise ValueError(
                                    "Process file changed during inventory"
                                )
                        else:
                            raise ValueError("Unsafe process inventory inode")
                    finally:
                        os.close(child)
            after = os.fstat(fd)
            if (
                before.st_ino,
                before.st_dev,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (after.st_ino, after.st_dev, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError("Process inventory changed")

        try:
            scan(base, prefix, 0)
            current = _open_directory(self.root.absolute() / prefix)
            try:
                before, after = os.fstat(base), os.fstat(current)
                if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                    raise ValueError("Process directory replaced")
            finally:
                os.close(current)
            previous = self.prefixes.get((owner, admission))
            if previous is not None and previous != observed:
                raise ValueError("Process physical inventory changed")
            self.prefixes[(owner, admission)] = observed
        finally:
            os.close(base)

    def recheck(self):
        for owner, admission in tuple(self.prefixes):
            self.process(owner, admission)
        for key, record in tuple(self.records.items()):
            self.add(key, record["size_bytes"])
        self.check()


def _one(rows):
    if len(rows) != 1:
        raise ValueError("One exact historical row required")
    return rows[0]


def _closure_schema_for_research(research_schema):
    if research_schema in {
        "mineagent.research-restore-closure.v13",
        "mineagent.research-restore-closure.v14",
        "mineagent.research-restore-closure.v15",
    }:
        return cold.SCHEMA
    if research_schema == "mineagent.research-restore-closure.v16":
        return cold.SCHEMA_V2
    raise ValueError("Unsupported scoped normal research schema")


def _load(session, owner_id, run, config, *, research_schema=cold.RESEARCH_SCHEMA):
    """One fixed normal run; no owner-wide graph or whole-install backup scan."""
    from backend.app import (
        execution_snapshot_backup,
        factor_analysis_backup,
        research_backup,
        structured_factor_backup,
    )

    closure_schema = _closure_schema_for_research(research_schema)
    reader = _Rows(session, owner_id, run.id)
    own = {
        name: reader.read(name, fields, "run_id=:run")
        for name, fields in cold.FIELDS.items()
    }
    full_run = reader.read(
        "research_runs", cold.RUN_FIELDS, "owner_id=:owner AND id=:run"
    )
    closure = {
        "schema_version": closure_schema,
        "tables": own,
        "external_bindings": {"runs": full_run},
    }
    shared = {}
    for name, fields in research_backup.FIELDS_V4.items():
        if name == "research_model_transports":
            shared[name] = reader.read(
                name,
                fields,
                "run_id=:run OR operation_id=ANY(CAST(:operation_ids AS uuid[]))",
                operation_ids=[r["id"] for r in shared["research_model_operations"]],
            )
        elif name not in {"research_objects", "research_object_outcomes"}:
            shared[name] = reader.read(name, fields, "run_id=:run")
    if any(
        row["owner_id"] != str(owner_id) or row["run_id"] != str(run.id)
        for rows in (*own.values(), *shared.values())
        for row in rows
    ):
        raise ValueError("Normal scoped graph contains foreign run/owner aliases")
    publication_ids = [r["id"] for r in shared["research_object_publications"]]
    shared["research_object_outcomes"] = reader.read(
        "research_object_outcomes",
        research_backup.FIELDS_V4["research_object_outcomes"],
        "publication_id=ANY(CAST(:ids AS uuid[]))",
        ids=publication_ids,
    )
    if any(r["owner_id"] != str(owner_id) for r in shared["research_object_outcomes"]):
        raise ValueError("Foreign object publication outcome")
    object_ids = set()
    for rows in (*own.values(), *shared.values()):
        for row in rows:
            for key, value in row.items():
                if value is not None and (
                    key == "object_id" or key.endswith("_object_id")
                ):
                    object_ids.add(value)
    shared["research_objects"] = reader.read(
        "research_objects",
        research_backup.FIELDS_V4["research_objects"],
        "owner_id=:owner AND id=ANY(CAST(:ids AS uuid[]))",
        ids=sorted(object_ids),
    )
    attempts = reader.read(
        "execution_attempts",
        "id run_id generation status sandbox_stopped finished_at",
        "run_id=:run",
    )
    research = {
        "schema_version": research_schema,
        "tables": shared,
        "external_bindings": {
            "runs": [
                {
                    key: row[key]
                    for key in (
                        "id",
                        "owner_id",
                        "generation",
                        "purpose",
                        "engine",
                        "status",
                    )
                }
                | {"config_schema_version": row["config"]["schema_version"]}
                for row in full_run
            ],
            "attempts": [{"owner_id": str(owner_id), **row} for row in attempts],
        },
    }
    source_id = config.fixed_inputs.snapshot_id
    publication = _one(
        reader.read(
            "factor_execution_snapshot_publications",
            execution_snapshot_backup.FIELDS["factor_execution_snapshot_publications"],
            "owner_id=:owner AND id=:source",
            source=source_id,
        )
    )
    source_outcome = _one(
        reader.read(
            "factor_execution_snapshot_outcomes",
            execution_snapshot_backup.FIELDS["factor_execution_snapshot_outcomes"],
            "owner_id=:owner AND publication_id=:source",
            source=source_id,
        )
    )
    dataset = _one(
        reader.read(
            "dataset_versions",
            factor_analysis_backup.FIELDS["dataset_versions"],
            "owner_id=:owner AND id=:dataset",
            dataset=config.fixed_inputs.dataset_version_id,
        )
    )
    exposures = reader.read(
        "data_usage_exposures",
        factor_analysis_backup.FIELDS["data_usage_exposures"],
        "owner_id=:owner AND source_key=:source_key",
        source_key=f"engine-result:execution_snapshot:{source_id}:factor:v1",
    )
    source_run = _one(
        reader.read(
            "research_runs",
            "id owner_id experiment_id split_version_id purpose engine status config",
            "owner_id=:owner AND id=:source_run",
            source_run=publication["run_id"],
        )
    )
    from backend.app.oe_structured_source_qualification import require_source_config

    require_source_config(source_run["config"])
    if (
        source_run["purpose"] != "factor_research"
        or source_run["engine"] != "rdagent"
        or source_run["status"]
        not in {"succeeded", "failed", "cancelled", "interrupted"}
        or any(
            (row["experiment_id"], row["split_version_id"])
            != (source_run["experiment_id"], source_run["split_version_id"])
            for row in exposures
        )
    ):
        raise ValueError("Normal original source release scope differs")
    # Ledger/scope proof precedes any retained source value read.
    cold.validate_source_release(
        str(owner_id), config, publication, source_outcome, dataset, exposures
    )
    versions = reader.read(
        "structured_strategy_versions",
        structured_factor_backup.FIELDS["structured_strategy_versions"],
        "owner_id=:owner AND id=ANY(CAST(:ids AS uuid[]))",
        ids=sorted(
            {r["program_version_id"] for r in own["oe_structured_candidates"]}
            | {r["seed_program_version_id"] for r in own["oe_structured_specs"]}
        ),
    )
    execution = {
        "tables": {
            "factor_execution_snapshot_publications": [publication],
            "factor_execution_snapshot_outcomes": [source_outcome],
        }
    }
    direct = {
        "tables": {"dataset_versions": [dataset], "data_usage_exposures": exposures}
    }
    structured = {"tables": {"structured_strategy_versions": versions}}
    return closure, research, execution, direct, structured


def _add_declared_source_inventory(physical, direct, config, owner_id, research_schema):
    """Bind every retained original byte before the full source reconstruction."""
    datasets = direct["tables"]["dataset_versions"]
    dataset = _one(datasets)
    if dataset["manifest"].get("schema_version") != 3:
        return
    if (
        research_schema
        not in {
            "mineagent.research-restore-closure.v15",
            "mineagent.research-restore-closure.v16",
        }
        or dataset["owner_id"] != str(owner_id)
        or dataset["id"] != str(config.fixed_inputs.dataset_version_id)
        or dataset["storage_key"]
        != f"datasets/{owner_id}/{config.fixed_inputs.dataset_version_id}"
    ):
        raise ValueError("Declared normal source scope/capability differs")
    from backend.app.declared_upload_backup import retained_artifacts

    def retained(key, maximum):
        record = physical.add(key, maximum)
        return physical.read(key, record["sha256"], record["size_bytes"])

    retained_artifacts(
        dataset["manifest"], dataset["storage_key"], retained, declared_upload=True
    )


def read_result(factory, settings, *, owner_id, execution_id):
    """Owned standalone transaction; other internal readers reuse its real fence."""
    from backend.app.execution_snapshot_history import historical_session

    with historical_session(factory, owner_id, settings) as session:
        return read_result_in_session(
            session, settings, owner_id=owner_id, execution_id=execution_id
        )


def read_result_in_session(session, settings, *, owner_id, execution_id):
    """Actual private result buffer. Caller must separately authorize disclosure."""
    from backend.app.execution_snapshot_history import (
        read_snapshot_in_session,
    )
    from backend.app.factor_models import Factor, FactorVersion
    from backend.app.models import ExecutionAttempt, ResearchRun
    from backend.app.oe_structured_identity import require_run_identity
    from backend.app.oe_structured_models import OEStructuredExecution
    from backend.app.oe_structured_provider_containment import provider_attempt_stopped
    from backend.app.research_backup import (
        _indexes,
        _validate_objects,
        _validate_processes,
    )
    from backend.app.research_broker_services import validate_accounting_closure
    from backend.app.schema_capabilities import capabilities_for
    from packages.factors.contracts import FactorDefinition, digest
    from packages.structured_factor_strategy.wire import StructuredResult

    if type(owner_id) is not UUID or type(execution_id) is not UUID:
        fail()
    try:
        require_native_storage_guard(settings.storage_root)
        versions = session.scalars(
            text("SELECT version_num FROM alembic_version")
        ).all()
        if versions not in (
            ["028_oe_structured_search"],
            ["029_research_workflow"],
            ["030_declared_upload_basket"],
            ["031_workflow_structured_stage"],
            ["032_normal_oe_note_pins"],
            ["032a_rd_terminal_results"],
        ) or capabilities_for(
            versions[0]
        ).oe_structured_closure_schema != _closure_schema_for_research(
            capabilities_for(versions[0]).research_closure_schema
        ):
            fail()
        execution = session.scalar(
            select(OEStructuredExecution).filter_by(owner_id=owner_id, id=execution_id)
        )
        if execution is None:
            fail()
        run = session.scalar(
            select(ResearchRun)
            .filter_by(owner_id=owner_id, id=execution.run_id)
            .with_for_update(nowait=True)
        )
        if run is None or run.status not in {
            "succeeded",
            "failed",
            "cancelled",
            "interrupted",
        }:
            fail()
        config = require_run_identity(run)
        from packages.openevolve_structured.workflow_run import (
            WorkflowStructuredRunConfig,
        )

        workflow_context = None
        if type(config) is WorkflowStructuredRunConfig:
            if versions not in (
                ["031_workflow_structured_stage"],
                ["032_normal_oe_note_pins"],
                ["032a_rd_terminal_results"],
            ):
                fail()
            from backend.app.oe_workflow_models import OEWorkflowSubmission
            from backend.app.oe_workflow_saved import verify_workflow_oe_submission

            attachment = session.scalar(
                select(OEWorkflowSubmission).filter_by(owner_id=owner_id, run_id=run.id)
            )
            if attachment is None:
                fail()
            verified = verify_workflow_oe_submission(
                session, owner_id, attachment.submission_id, settings
            )
            if verified.id != attachment.id or verified.config_sha256 != config.sha256:
                fail()
            workflow_context = {str(run.id): config}
        elif versions in (
            ["031_workflow_structured_stage"],
            ["032_normal_oe_note_pins"],
            ["032a_rd_terminal_results"],
        ):
            workflow_context = {}
        attempts = list(
            session.scalars(
                select(ExecutionAttempt)
                .filter_by(run_id=run.id)
                .with_for_update(nowait=True)
                .limit(2)
            )
        )
        if (
            len(attempts) != 1
            or attempts[0].id != execution.attempt_id
            or attempts[0].generation != execution.generation
            or attempts[0].finished_at is None
            or not attempts[0].sandbox_stopped
        ):
            fail()
        if not provider_attempt_stopped(session, run, attempts[0], settings):
            fail()
        research_schema = capabilities_for(versions[0]).research_closure_schema
        own, research, snapshots, direct, structured = _load(
            session, owner_id, run, config, research_schema=research_schema
        )
        idx = _indexes(research, MAX_ROWS)
        validate_accounting_closure(
            research["tables"],
            idx,
            rd_source=True,
            openevolve=True,
            rd_provider=True,
            oe_structured=True,
            model_bindings={str(run.id): config.model_binding},
        )
        states = research["tables"]["research_process_states"]
        admissions = research["tables"]["research_process_admissions"]
        if (
            not 1 <= len(admissions) <= 4
            or {r["admission_id"] for r in states} != {r["id"] for r in admissions}
            or any(r["status"] != "stopped" for r in states)
        ):
            fail()
        controllers = [
            r
            for r in admissions
            if r["profile"] == "mineagent.oe-structured-orchestrator.v1"
        ]
        if len(controllers) != 1:
            fail()
        controller_receipts = [
            r
            for r in research["tables"]["research_process_receipts"]
            if r["admission_id"] == controllers[0]["id"]
        ]
        kinds = {r["kind"] for r in controller_receipts}
        if len(controller_receipts) != len(kinds) or kinds not in (
            {"launch_intent", "launch_identity", "controller_child", "stop"},
            {
                "launch_intent",
                "launch_identity",
                "controller_child",
                "stop",
                "controller_completion",
            },
        ):
            fail()
        snapshot = read_snapshot_in_session(
            session, owner_id, config.fixed_inputs.snapshot_id, settings
        )
        physical = _Physical(settings.storage_root)
        for admission in admissions:
            physical.process(owner_id, UUID(admission["id"]))
        for obj in research["tables"]["research_objects"]:
            physical.add(obj["storage_key"], obj["size_bytes"])
        published = {
            r["publication_id"] for r in research["tables"]["research_object_outcomes"]
        }
        for publication_row in research["tables"]["research_object_publications"]:
            for key, maximum in (
                (
                    f"research-objects/{owner_id}/journals/{publication_row['id']}.json",
                    8192,
                ),
                (publication_row["storage_key"], 1048576),
            ):
                try:
                    physical.add(key, maximum)
                except FileNotFoundError:
                    if publication_row["id"] in published:
                        raise
        for key in cold.manifest_keys(own):
            physical.add(key, 64 * 1024**2)
        _add_declared_source_inventory(
            physical, direct, config, owner_id, research_schema
        )

        def check(key, sha, size):
            if len(physical.read(key, sha, size)) != size:
                raise ValueError("Protected process byte bound")

        read_json = physical.json

        _validate_objects(
            idx,
            physical.records,
            check,
            read_json,
            rd_source=True,
            rd_execution=True,
            rd_analysis=True,
            openevolve=True,
            rd_provider=True,
            oe_structured=True,
        )
        _validate_processes(
            idx,
            physical.records,
            check,
            read_json,
            research_schema=research_schema,
            read_bytes=physical.read,
            **(
                {"workflow_context": {}}
                if versions
                in (
                    ["029_research_workflow"],
                    ["030_declared_upload_basket"],
                    ["031_workflow_structured_stage"],
                    ["032_normal_oe_note_pins"],
                    ["032a_rd_terminal_results"],
                )
                else {}
            ),
        )
        facts = cold.validate_closure(
            own,
            research_closure=research,
            execution_closure=snapshots,
            direct_analysis_closure=direct,
            structured_closure=structured,
            inventory=list(physical.records.values()),
            read_bytes=physical.read,
            maximum_rows=MAX_ROWS,
            **(
                {"workflow_context": workflow_context}
                if workflow_context is not None
                else {}
            ),
        )
        outcome = _one(
            [
                r
                for r in own["tables"]["oe_structured_engine_outcomes"]
                if r["execution_id"] == str(execution_id)
            ]
        )
        candidate = _one(
            [
                r
                for r in own["tables"]["oe_structured_candidates"]
                if r["id"] == outcome["candidate_id"]
            ]
        )
        result = StructuredResult.model_validate_json(outcome["result_canonical"])
        measurement = facts["measurements"][candidate["id"]]
        if (
            digest(measurement) != outcome["measurement_sha256"]
            or snapshot.snapshot_id != execution.snapshot_id
        ):
            fail()
        publication = snapshots["tables"]["factor_execution_snapshot_publications"][0]
        version = session.scalar(
            select(FactorVersion).filter_by(
                owner_id=owner_id, id=snapshot.factor_version_id
            )
        )
        factor = (
            None
            if version is None
            else session.scalar(
                select(Factor).filter_by(owner_id=owner_id, id=version.factor_id)
            )
        )
        if version is None or factor is None:
            fail()
        definition = FactorDefinition.model_validate_json(
            canonical_bytes(version.definition)
        )
        if (
            definition.sha256 != version.definition_sha256
            or version.code_sha256 != definition.code_sha256
        ):
            fail()
        historical = HistoricalStructuredExecution(
            execution_id=execution_id,
            run_id=run.id,
            structured_strategy_version_id=execution.program_version_id,
            factor_version_id=version.id,
            factor_id=factor.id,
            parent_factor_version_id=version.parent_version_id,
            snapshot_id=execution.snapshot_id,
            execution_snapshot_run_id=UUID(publication["run_id"]),
            admission_id=UUID(outcome["admission_id"]),
            result_sha256=outcome["result_sha256"],
            result=result,
            factor_definition=definition,
            factor_code_sha256=version.code_sha256,
            snapshot_input_sha256=snapshot.input_sha256,
        )
        physical.recheck()
        if not provider_attempt_stopped(session, run, attempts[0], settings):
            fail()
        return HistoricalNormalExecution(
            historical,
            UUID(candidate["id"]),
            measurement,
            outcome["measurement_sha256"],
            digest(config),
        )
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        OSError,
        ProcessError,
    ) as exc:
        raise DomainError(
            "oe_structured_history_unavailable",
            "Complete owned normal result is unavailable",
            409,
        ) from exc
