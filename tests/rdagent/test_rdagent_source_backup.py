"""RD closure/parser/file unit evidence, not a PostgreSQL or cold-restore claim."""

import copy
import hashlib
import json
from types import SimpleNamespace
from uuid import UUID

import pytest
from test_rdagent_contracts import research_dict

from backend.app import rdagent_backup as backup
from backend.app.factor_backup import TABLE_NAMES
from backend.app.research_backup import FIELDS as RESEARCH_FIELDS
from packages.factors.contracts import digest
from packages.rdagent_adapter.contracts import FactorResearchConfig
from packages.rdagent_adapter.host_evidence import (
    ModelResponsePart,
    ResponseAssemblyDocument,
    research_definition,
)
from packages.rdagent_adapter.operational_bootstrap import RDOperationalBootstrap
from packages.rdagent_adapter.response_parser import (
    UPSTREAM_FRAGMENT_SHA256,
    parse_selected_responses,
)
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.engine_broker_contracts import (
    RDChatResponse,
    parse_request,
    request_sha256,
)
from packages.research_runtime.engine_broker_contracts import (
    canonical_bytes as broker_bytes,
)

NOW = "2026-10-26T00:00:00+00:00"


def empty():
    return {
        "schema_version": backup.SCHEMA,
        "tables": {k: [] for k in backup.FIELDS},
        "consumer_claims": [],
        "external_bindings": {"runs": [], "datasets": []},
    }


@pytest.fixture
def retained(tmp_path):
    """Synthetic relational rows + actual files; shared-ledger validator not mocked."""
    closure = empty()
    research = {
        "schema_version": "mineagent.research-restore-closure.v4",
        "tables": {k: [] for k in RESEARCH_FIELDS},
        "external_bindings": {"runs": [], "attempts": []},
    }
    factors = {
        "schema_version": "mineagent.factor-restore-closure.v2",
        "tables": {k: [] for k in TABLE_NAMES},
        "external_bindings": {},
    }
    seq = 100

    def uid():
        nonlocal seq
        seq += 1
        return str(UUID(int=seq))

    owner, runid, attemptid = uid(), uid(), uid()
    scope = {
        "owner_id": owner,
        "run_id": runid,
        "attempt_id": attemptid,
        "generation": 1,
    }
    config = FactorResearchConfig.model_validate(research_dict())

    def row(table, **attrs):
        values = {k: None for k in backup.FIELDS[table].split()}
        values.update({k: v for k, v in scope.items() if k in values})
        values.update(id=uid(), created_at=NOW)
        values.update(attrs)
        closure["tables"][table].append(values)
        return values

    def shared(table, **attrs):
        values = {"id": uid(), **scope, **attrs}
        target = (
            research["tables"] if table in research["tables"] else factors["tables"]
        )
        target[table].append(values)
        return values

    run = {k: None for k in backup.RUN_FIELDS.split()}
    run.update(
        id=runid,
        owner_id=owner,
        generation=1,
        purpose="factor_research",
        engine="rdagent",
        config=config.model_dump(mode="json"),
        request_sha256=config.sha256,
        dataset_version_id=str(config.input.dataset.dataset_version_id),
        research_image_digest=config.runtime.orchestrator_image,
        created_at=NOW,
    )
    closure["external_bindings"]["runs"].append(run)
    closure["external_bindings"]["datasets"].append(
        {
            "id": run["dataset_version_id"],
            "owner_id": owner,
            "content_sha256": config.input.dataset.content_sha256,
        }
    )
    research["external_bindings"]["attempts"].append({"id": attemptid, **scope})
    shared("research_budget_controls", limits=config.budget.model_dump())
    spec = row(
        "factor_research_specs",
        config=config.model_dump(mode="json"),
        config_sha256=config.sha256,
        input_sha256=config.input.sha256,
    )
    binding = row(
        "factor_research_attempt_bindings",
        spec_id=spec["id"],
        resumed_checkpoint_id=None,
    )
    bootstrap = RDOperationalBootstrap(
        **scope,
        spec_id=spec["id"],
        attempt_binding_id=binding["id"],
        config=config,
        config_sha256=config.sha256,
        input_sha256=config.input.sha256,
    )
    admission = shared(
        "research_process_admissions",
        role="rd_orchestrator",
        profile="mineagent.rdagent-orchestrator.v1",
        image_id=config.runtime.orchestrator_image,
        input_sha256=bootstrap.sha256,
        logical_operation_id=binding["id"],
    )
    launch = row(
        "factor_controller_launches",
        attempt_binding_id=binding["id"],
        admission_id=admission["id"],
        bootstrap=bootstrap.model_dump(mode="json"),
        bootstrap_sha256=bootstrap.sha256,
    )
    context_payload = {
        "schema_version": "mineagent.rdagent-context.v1",
        "spec_sha256": config.sha256,
        "input_sha256": config.input.sha256,
        "logical_step": 0,
        "task_index": 0,
    }
    context = shared(
        "research_operation_contexts",
        context=context_payload,
        context_sha256=digest(context_payload),
    )
    mutation = shared(
        "research_budget_reservations",
        purpose="engine_mutation",
        amounts=BudgetVector(mutation_slots=1).model_dump(),
    )
    start = row(
        "factor_generation_starts",
        spec_id=spec["id"],
        logical_step=0,
        task_index=0,
        context_id=context["id"],
        mutation_reservation_id=mutation["id"],
    )
    call = row(
        "factor_upstream_calls",
        start_id=start["id"],
        context_id=context["id"],
        phase="generation",
        kind="chat",
        ordinal=0,
    )
    files = {}

    def object_row(kind, data, factor=False):
        sha = hashlib.sha256(data).hexdigest()
        key = (
            f"factors/{owner}/objects/{sha}.py"
            if factor
            else f"research-objects/{owner}/by-kind/{kind}/{sha}.json"
        )
        path = tmp_path / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        files[key] = data
        return shared(
            "factor_objects" if factor else "research_objects",
            object_kind=kind,
            sha256=sha,
            size_bytes=len(data),
            storage_key=key,
            media_type="text/plain" if factor else "application/json",
        )

    protocol = "mineagent.rdagent-chat.v1"
    request = parse_request(
        protocol,
        {
            "messages": [{"role": "user", "content": "fixture"}],
            "max_output_tokens": 100,
            "response_format": {"type": "json_object"},
        },
    )
    requestobj = object_row("model_request", broker_bytes(protocol, request))
    operation = shared(
        "research_model_operations",
        context_id=context["id"],
        protocol=protocol,
        purpose="chat",
        ordinal=0,
        state="completed",
        request=request.model_dump(mode="json"),
        request_sha256=request_sha256(protocol, request),
        request_object_id=requestobj["id"],
    )
    member = row(
        "factor_generation_model_operations",
        context_id=context["id"],
        start_id=start["id"],
        call_id=call["id"],
        idempotency_key=uid(),
        operation_id=operation["id"],
        request_ordinal=0,
        relation="initial",
        protocol=protocol,
    )
    source_text = "# 中文😀 retained; never executed\nvalue=1\n"
    response = RDChatResponse(
        content=json.dumps({"code": source_text}, ensure_ascii=False),
        finish_reason="stop",
    )
    responseobj = object_row("model_response", broker_bytes(protocol, response))
    held = shared(
        "research_budget_reservations",
        purpose="model_transport",
        state="settled",
        amounts=BudgetVector(calls=1, tokens=100).model_dump(),
    )
    transport = shared(
        "research_model_transports",
        operation_id=operation["id"],
        ordinal=1,
        reservation_id=held["id"],
        state="completed",
    )
    usage = BudgetVector(calls=1, tokens=1).model_dump()
    outcome = shared(
        "research_model_outcomes",
        transport_id=transport["id"],
        outcome="completed",
        usage=usage,
        response_object_id=responseobj["id"],
        response_object_kind="model_response",
        response_sha256=responseobj["sha256"],
    )
    shared(
        "research_budget_settlements",
        reservation_id=held["id"],
        charge=usage,
        outcome="measured",
    )
    row(
        "factor_upstream_call_outcomes",
        call_id=call["id"],
        controller_launch_id=launch["id"],
        status="completed",
        member_count=1,
        member_ids=[member["id"]],
        response_outcome_ids=[outcome["id"]],
    )
    parsed = parse_selected_responses((response,))
    document = ResponseAssemblyDocument(
        call_id=call["id"],
        orchestrator_admission_id=admission["id"],
        parts=(
            ModelResponsePart(
                member_id=member["id"],
                outcome_id=outcome["id"],
                response_object_id=responseobj["id"],
                response_sha256=responseobj["sha256"],
            ),
        ),
        source=source_text,
        source_sha256=parsed.source_sha256,
    )
    assemblyobj = object_row(
        "factor_response_assembly",
        json.dumps(
            document.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode(),
    )
    assembly = row(
        "factor_response_assemblies",
        start_id=start["id"],
        context_id=context["id"],
        call_id=call["id"],
        object_id=assemblyobj["id"],
        object_kind="factor_response_assembly",
        object_sha256=assemblyobj["sha256"],
        part_count=1,
    )
    part = row(
        "factor_response_assembly_parts",
        call_id=call["id"],
        assembly_id=assembly["id"],
        member_id=member["id"],
        outcome_id=outcome["id"],
        response_object_id=responseobj["id"],
        response_object_kind="model_response",
        response_sha256=responseobj["sha256"],
        ordinal=0,
    )
    codeobj = object_row("code", source_text.encode(), factor=True)
    factor = shared("factors")
    definition = research_definition(config, parsed.source_sha256)
    version = shared(
        "factor_versions",
        factor_id=factor["id"],
        parent_version_id=None,
        code=source_text,
        code_sha256=parsed.source_sha256,
        code_object_id=codeobj["id"],
        source_kind="rdagent",
        source_event_key="rdagent:" + start["id"],
        definition=definition.model_dump(mode="json"),
        definition_sha256=definition.sha256,
        source_provenance={
            "schema_version": "mineagent.rdagent-source.v1",
            "run_id": runid,
            "start_id": start["id"],
            "call_id": call["id"],
            "assembly_sha256": assemblyobj["sha256"],
            "explanation_origin": "task_declaration",
        },
    )
    source = row(
        "factor_generation_sources",
        context_id=context["id"],
        start_id=start["id"],
        factor_version_id=version["id"],
        call_id=call["id"],
        assembly_id=assembly["id"],
        source_sha256=parsed.source_sha256,
    )
    reservation = shared(
        "research_budget_reservations",
        purpose="object_storage",
        logical_key="rdsource:" + start["id"],
        amounts=BudgetVector(
            storage_bytes=len(source_text.encode()) * 2 + 8192
        ).model_dump(),
    )
    pub = row(
        "factor_source_publications",
        start_id=start["id"],
        call_id=call["id"],
        assembly_object_id=assemblyobj["id"],
        assembly_object_kind="factor_response_assembly",
        assembly_sha256=assemblyobj["sha256"],
        budget_reservation_id=reservation["id"],
        source_sha256=parsed.source_sha256,
        size_bytes=len(source_text.encode()),
        peak_bytes=len(source_text.encode()) * 2 + 8192,
        factor_id=factor["id"],
        factor_version_id=version["id"],
    )
    closure["consumer_claims"].append(
        {
            "reservation_id": reservation["id"],
            **scope,
            "consumer_kind": "factor_source",
            "consumer_id": pub["id"],
        }
    )
    from backend.app.rdagent_source_publication import source_publication_journal

    journal = source_publication_journal(SimpleNamespace(**pub))
    journalsha = hashlib.sha256(journal).hexdigest()
    journalkey = f"factors/{owner}/objects/{journalsha}.json"
    path = tmp_path / journalkey
    path.write_bytes(journal)
    files[journalkey] = journal
    row(
        "factor_source_publication_outcomes",
        publication_id=pub["id"],
        source_id=source["id"],
        journal_sha256=journalsha,
    )
    witness = row(
        "factor_source_read_witnesses",
        source_id=source["id"],
        assembly_id=assembly["id"],
        call_id=call["id"],
        controller_launch_id=launch["id"],
        parser_profile="mineagent.rdagent-source-parser.v1",
        parser_source_sha256=UPSTREAM_FRAGMENT_SHA256,
        source_sha256=parsed.source_sha256,
        source_size_bytes=parsed.source_size_bytes,
        source_object_id=codeobj["id"],
        source_object_kind="code",
        raw_text_sha256=parsed.raw_text_sha256,
        raw_text_bytes=len(parsed.raw_text.encode()),
        normalized_json_sha256=parsed.normalized_json_sha256,
        normalized_json_bytes=len(parsed.normalized_json.encode()),
        selected_first_ordinal=0,
        part_count=1,
    )
    readpart = row(
        "factor_source_read_parts",
        witness_id=witness["id"],
        assembly_part_id=part["id"],
        member_id=member["id"],
        outcome_id=outcome["id"],
        response_object_id=responseobj["id"],
        response_object_kind="model_response",
        response_sha256=responseobj["sha256"],
        ordinal=0,
        finish_reason="stop",
        content_sha256=hashlib.sha256(response.content.encode()).hexdigest(),
        content_size_bytes=len(response.content.encode()),
    )
    header = {
        k: v
        for k, v in witness.items()
        if k not in {"id", "created_at", *backup.SCOPE, "witness_sha256"}
    }
    partdoc = {
        k: v
        for k, v in readpart.items()
        if k not in {"id", "created_at", *backup.SCOPE, "witness_id"}
    }
    witness["witness_sha256"] = digest(
        {
            "schema_version": "mineagent.source-read-witness.v1",
            "scope": scope,
            "header": header,
            "parts": [partdoc],
        }
    )
    reads = []

    def reader(key, sha, maximum):
        raw = (tmp_path / key).read_bytes()
        reads.append(key)
        assert len(raw) <= maximum
        return raw  # Validator must independently check expected SHA, not trust us.

    return SimpleNamespace(
        closure=closure,
        research=research,
        factors=factors,
        reader=reader,
        root=tmp_path,
        files=files,
        reads=reads,
    )


def check(data):
    return backup.validate_closure(
        data.closure, data.research, data.factors, data.reader, 1000
    )


def test_actual_retained_response_parser_source_and_journal(retained):
    assert check(retained)
    assert set(retained.reads) == set(retained.files)


@pytest.mark.parametrize(
    "kind",
    ["model_request", "model_response", "factor_response_assembly", "code", "journal"],
)
def test_actual_file_bytes_drift_rejected(retained, kind):
    key = next(
        k
        for k in retained.files
        if (
            k.endswith(".py")
            if kind == "code"
            else "/factors/" in "/" + k and k.endswith(".json")
            if kind == "journal"
            else "/" + kind + "/" in k
        )
    )
    (retained.root / key).write_bytes(b"{}")
    with pytest.raises(ValueError):
        check(retained)


@pytest.mark.parametrize(
    ("table", "field", "value"),
    [
        ("factor_source_read_witnesses", "raw_text_sha256", "0" * 64),
        ("factor_source_read_witnesses", "normalized_json_sha256", "0" * 64),
        ("factor_source_read_witnesses", "witness_sha256", "0" * 64),
        ("factor_source_read_parts", "finish_reason", "length"),
        ("factor_source_read_parts", "content_size_bytes", 1),
        ("factor_generation_sources", "source_sha256", "0" * 64),
        ("factor_upstream_calls", "phase", "critique"),
        ("factor_source_publications", "peak_bytes", 8192),
        ("factor_generation_model_operations", "request_ordinal", 1),
    ],
)
def test_metadata_drift_fails_closed(retained, table, field, value):
    retained.closure["tables"][table][0][field] = value
    with pytest.raises(ValueError):
        check(retained)


def test_witness_missing_is_not_a_hash_only_source_claim(retained):
    retained.closure["tables"]["factor_source_read_witnesses"].clear()
    with pytest.raises(ValueError):
        check(retained)


def test_exact_field_inventory_detects_new_uncovered_column(retained):
    retained.closure["tables"]["factor_generation_sources"][0]["future_field"] = None
    with pytest.raises(ValueError):
        check(retained)


def test_consumer_cannot_be_double_counted_or_omitted(retained):
    retained.closure["consumer_claims"].clear()
    with pytest.raises(ValueError):
        check(retained)


def test_row_and_byte_bounds(retained):
    with pytest.raises(ValueError):
        backup._indexes(retained.closure, 1)
    with pytest.raises(ValueError):
        backup._indexes(retained.closure, 1000, 100)

    # The source-only SQL fixture must preserve active precursor facts rather
    # than pretending the independently rejected shared cold closure passed.
    import re

    from tests.integration.rd_source_related_rows import read_admitted_source_rows

    owner, run, attempt = (UUID(int=n) for n in (901, 902, 903))
    actual = {
        "research_runs": [
            {"id": str(run), "owner_id": str(owner), "status": "running"}
        ],
        "execution_attempts": [
            {
                "id": str(attempt),
                "run_id": str(run),
                "owner_id": str(owner),
                "status": "running",
                "sandbox_stopped": False,
                "finished_at": None,
            }
        ],
        "research_process_states": [
            {"admission_id": str(UUID(int=904)), "status": "admitted"}
        ],
    }
    streams = []

    class Rows:
        def __init__(self, values):
            self.values = values
            self.closed = False
            streams.append(self)

        def __iter__(self):
            return iter(self.values)

        def close(self):
            self.closed = True

    def execute(statement, parameters):
        query = str(statement)
        assert "LIMIT :limit" in query and "owner" in query and ":run" in query
        assert parameters["owner"] == owner and parameters["run"] == run
        assert "status=" not in query and "UPDATE" not in query
        table = re.search(r"FROM (research_\w+|execution_attempts)\b", query).group(1)
        stream = Rows(actual.get(table, []))
        return SimpleNamespace(scalars=lambda: stream)

    connection = SimpleNamespace(execute=execute)
    fence = SimpleNamespace(owner_id=owner, run_id=run)
    related = read_admitted_source_rows(connection, fence)
    assert related["external_bindings"]["runs"] == actual["research_runs"]
    assert related["external_bindings"]["attempts"] == actual["execution_attempts"]
    assert (
        related["tables"]["research_process_states"]
        == actual["research_process_states"]
    )
    assert all(stream.closed for stream in streams)
    for bounds in ({"maximum_rows": 1}, {"maximum_bytes": 1}):
        with pytest.raises(ValueError, match="exceed bounds"):
            read_admitted_source_rows(connection, fence, **bounds)
        assert all(stream.closed for stream in streams)


def test_unregistered_outcome_table_cannot_be_dropped_from_shape(retained):
    bad = copy.deepcopy(retained.closure)
    del bad["tables"]["factor_cutoff_outcomes"]
    with pytest.raises(ValueError):
        backup._indexes(bad, 1000)


def test_static_inventory_matches_reviewed_24_model_tables():
    from sqlalchemy import ForeignKeyConstraint, UniqueConstraint

    import backend.app.rdagent_boundary_models
    import backend.app.rdagent_input_models
    import backend.app.rdagent_source_witness_models  # noqa: F401
    from backend.app.models import Base

    assert len(backup.FIELDS) == 24
    for name, fields in backup.FIELDS.items():
        table = Base.metadata.tables[name]
        assert set(fields.split()) == {c.name for c in table.columns}
        assert set(backup.UNIQUE_KEYS[name]) == {
            tuple(c.name for c in key.columns)
            for key in table.constraints
            if isinstance(key, UniqueConstraint)
        }
        actual = {
            (
                tuple(e.parent.name for e in key.elements),
                key.elements[0].target_fullname.rsplit(".", 1)[0],
                tuple(e.target_fullname.rsplit(".", 1)[1] for e in key.elements),
            )
            for key in table.constraints
            if isinstance(key, ForeignKeyConstraint)
        }
        assert set(backup.FOREIGN_KEYS[name]) == actual


def test_boolean_cannot_impersonate_sql_integer_metadata(retained):
    retained.closure["tables"]["factor_source_read_witnesses"][0]["part_count"] = True
    with pytest.raises(ValueError):
        check(retained)


def test_pending_publication_keeps_real_response_without_inventing_source_outcome(
    retained,
):
    for table in (
        "factor_response_assemblies",
        "factor_response_assembly_parts",
        "factor_generation_sources",
        "factor_source_read_witnesses",
        "factor_source_read_parts",
        "factor_source_publication_outcomes",
    ):
        retained.closure["tables"][table].clear()
    retained.factors["tables"]["factor_versions"].clear()
    pub = retained.closure["tables"]["factor_source_publications"][0]
    reservation = next(
        r
        for r in retained.research["tables"]["research_budget_reservations"]
        if r["id"] == pub["budget_reservation_id"]
    )
    reservation["state"] = "reserved"
    assert check(retained)
    reservation["state"] = "settled"
    with pytest.raises(ValueError):
        check(retained)


def test_manifest_keys_include_actual_source_journal_without_research_objects(retained):
    expected = {path for path in retained.files if path.startswith("factors/")}
    assert backup.manifest_keys(retained.closure, retained.factors) == expected
    retained.closure["tables"]["factor_source_publication_outcomes"].clear()
    retained.closure["tables"]["factor_source_read_witnesses"].clear()
    for path in expected:
        (retained.root / path).unlink()
    assert backup.manifest_keys(retained.closure, retained.factors) == expected


@pytest.mark.parametrize("case", ["bad_sha", "foreign_owner", "native_path"])
def test_manifest_keys_fail_closed_on_path_or_scope_substitution(retained, case):
    if case == "bad_sha":
        retained.closure["tables"]["factor_source_publications"][0]["source_sha256"] = (
            "../escape"
        )
    elif case == "foreign_owner":
        retained.factors["tables"]["factor_objects"][0]["owner_id"] = str(UUID(int=999))
    else:
        retained.factors["tables"]["factor_objects"][0]["storage_key"] = (
            "factors/native.h5"
        )
    with pytest.raises(ValueError):
        backup.manifest_keys(retained.closure, retained.factors)
