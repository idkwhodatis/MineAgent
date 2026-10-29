"""Explicit normal027 retained closure. Never a live Peer or execution grant."""

import hashlib
from uuid import UUID

from sqlalchemy import text

from packages.factors.contracts import canonical_bytes, digest
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.process_contracts import canonical
from packages.research_runtime.process_persistence_codec import decode_row

SCHEMA = "mineagent.oe-structured-restore-closure.v1"
SCHEMA_V2 = "mineagent.oe-structured-restore-closure.v2"
RESEARCH_SCHEMA = "mineagent.research-restore-closure.v13"
SCOPE = ("owner_id", "run_id", "attempt_id", "generation")
RUN_FIELDS = "id owner_id generation purpose engine status strategy_version_id dataset_version_id config request_sha256"
FIELDS = {
    "oe_structured_candidates": "epoch_id upstream_program_id ordinal parent_candidate_id provider_exchange_id program_version_id candidate_canonical candidate_sha256 program_sha256 origin id owner_id run_id attempt_id generation created_at",
    "oe_structured_checkpoint_chunks": "epoch_id upload_id sequence file_name offset_bytes size_bytes chunk_sha256 object_id object_kind object_sha256 id owner_id run_id attempt_id generation created_at",
    "oe_structured_checkpoint_completions": "epoch_id upload_id inventory_object_id inventory_object_kind inventory_object_sha256 id owner_id run_id attempt_id generation created_at",
    "oe_structured_checkpoint_intents": "epoch_id control_state_id save_ordinal frontier state_sha256 id owner_id run_id attempt_id generation created_at",
    "oe_structured_checkpoint_publications": "epoch_id intent_id completion_id control_state_id state_sha256 id owner_id run_id attempt_id generation created_at",
    "oe_structured_checkpoint_uploads": "epoch_id intent_id reservation_id inventory_canonical inventory_sha256 total_bytes file_count chunk_count id owner_id run_id attempt_id generation created_at",
    "oe_structured_control_states": "epoch_id previous_id sequence frontier phase state_canonical state_sha256 id owner_id run_id attempt_id generation created_at",
    "oe_structured_engine_outcomes": "execution_id candidate_id admission_id stop_receipt_id stop_receipt_kind stop_receipt_sha256 extraction_receipt_id extraction_receipt_kind extraction_receipt_sha256 result_canonical result_sha256 result_size_bytes measurement_canonical measurement_sha256 objective_sha256 id owner_id run_id attempt_id generation created_at",
    "oe_structured_epochs": "controller_admission_id bootstrap_canonical bootstrap_sha256 id owner_id run_id attempt_id generation created_at",
    "oe_structured_executions": "candidate_id program_version_id snapshot_id strategy_input strategy_input_canonical strategy_input_sha256 strategy_input_size_bytes market_sha256 market_size_bytes id owner_id run_id attempt_id generation created_at",
    "oe_structured_iteration_intents": "epoch_id sampling_state_id ordinal parent_candidate_id selection_canonical selection_sha256 context_canonical context_sha256 rendered_canonical rendered_sha256 worker_ticket_sha256 worker_identity_canonical worker_identity_sha256 id owner_id run_id attempt_id generation created_at",
    "oe_structured_provider_exchanges": "provider_transport_id epoch_id iteration_intent_id parent_candidate_id context context_canonical context_sha256 request_object_id request_kind request_sha256 response_object_id response_kind response_sha256 id owner_id run_id attempt_id generation created_at",
    "oe_structured_provider_receipts": "membership_id kind receipt_object_id receipt_object_kind receipt_sha256 size_bytes id owner_id run_id attempt_id generation created_at",
    "oe_structured_provider_transports": "iteration_intent_id transport_id operation_id request_sha256 model_binding_sha256 id owner_id run_id attempt_id generation created_at",
    "oe_structured_sampling_intents": "epoch_id sampling_state_id parent_candidate_id mutation_reservation_id ordinal selection_canonical selection_sha256 event_sha256 worker_ticket_sha256 id owner_id run_id attempt_id generation created_at",
    "oe_structured_specs": "run_id owner_id submission_id request request_canonical request_sha256 preflight_sha256 config config_canonical config_sha256 snapshot_id dataset_id seed_program_version_id created_at",
    "oe_structured_submissions": "id owner_id run_id idempotency_key request_canonical request_sha256 preflight_sha256 config_sha256 origin created_at",
    "oe_structured_terminal_applications": "epoch_id ordinal control_state_id worker_outcome_id outcome event_canonical event_sha256 id owner_id run_id attempt_id generation created_at",
    "oe_structured_worker_outcomes": "epoch_id iteration_intent_id status candidate_id execution_id engine_outcome_id evidence_canonical evidence_sha256 event_sha256 id owner_id run_id attempt_id generation created_at",
    "oe_structured_worker_placements": "sampling_intent_id placement_canonical placement_sha256 worker_identity_canonical worker_identity_sha256 controller_identity_sha256 id owner_id run_id attempt_id generation created_at",
}
KEYS = {name: ("run_id" if name == "oe_structured_specs" else "id") for name in FIELDS}


def _bound(maximum_rows, maximum_bytes):
    if (
        type(maximum_rows) is not int
        or not 0 < maximum_rows <= 100000
        or type(maximum_bytes) is not int
        or not 0 < maximum_bytes <= 512 * 1024**2
    ):
        raise ValueError("Normal OE closure bounds")


def read_closure(connection, maximum_rows, *, maximum_bytes=32_000_000, schema=SCHEMA):
    if schema not in (SCHEMA, SCHEMA_V2):
        raise ValueError("Exact normal OE closure schema required")
    _bound(maximum_rows, maximum_bytes)
    tables, runs = {}, []
    count = size = 0
    for name, fields in {**FIELDS, "research_runs": RUN_FIELDS}.items():
        normal_run = name == "research_runs"
        where = (
            (
                " WHERE purpose='strategy_search' AND engine='openevolve'"
                + (
                    " AND config->>'schema_version'='mineagent.oe-structured-provider-run.v1'"
                    if schema == SCHEMA
                    else " AND config->>'schema_version' IN ('mineagent.oe-structured-provider-run.v1','mineagent.oe-structured-provider-run.v2')"
                )
            )
            if normal_run
            else ""
        )
        order = "id" if normal_run else KEYS[name]
        cursor = connection.execute(
            text(
                f"SELECT to_jsonb(t) FROM (SELECT {','.join(fields.split())} FROM {name}{where} ORDER BY {order} LIMIT :limit) t"
            ).execution_options(stream_results=True, yield_per=1),
            {"limit": maximum_rows + 1},
        )
        rows = []
        try:
            for row in cursor.scalars():
                count += 1
                size += len(canonical_bytes(row)) + 1
                if count > maximum_rows or size > maximum_bytes:
                    raise ValueError("Normal OE closure bounds")
                rows.append(row)
        finally:
            cursor.close()
        if normal_run:
            runs = rows
        else:
            tables[name] = rows
    result = {
        "schema_version": schema,
        "tables": tables,
        "external_bindings": {"runs": runs},
    }
    if len(canonical_bytes(result)) > maximum_bytes:
        raise ValueError("Normal OE closure envelope bounds")
    return result


def indexes(closure, maximum_rows=100000, *, schema=SCHEMA):
    if schema not in (SCHEMA, SCHEMA_V2):
        raise ValueError("Exact normal OE closure schema required")
    _bound(maximum_rows, 512 * 1024**2)
    if (
        type(closure) is not dict
        or set(closure) != {"schema_version", "tables", "external_bindings"}
        or closure["schema_version"] != schema
        or type(closure["tables"]) is not dict
        or set(closure["tables"]) != set(FIELDS)
        or type(closure["external_bindings"]) is not dict
        or set(closure["external_bindings"]) != {"runs"}
    ):
        raise ValueError("Exact normal OE closure envelope required")
    result, count = {}, 0
    for name, fields in {**FIELDS, "research_runs": RUN_FIELDS}.items():
        rows = (
            closure["external_bindings"]["runs"]
            if name == "research_runs"
            else closure["tables"][name]
        )
        if type(rows) is not list:
            raise ValueError("Normal OE table must be a bounded list")
        key = "id" if name == "research_runs" else KEYS[name]
        items = {}
        for row in rows:
            count += 1
            if (
                count > maximum_rows
                or type(row) is not dict
                or set(row) != set(fields.split())
            ):
                raise ValueError("Normal OE exact row shape/bound")
            identity = row[key]
            if (
                type(identity) is not str
                or str(UUID(identity)) != identity
                or identity in items
            ):
                raise ValueError("Normal OE duplicate/noncanonical row identity")
            items[identity] = row
        result[name] = items
    return result


def _same(row, other):
    return (
        other is not None
        and type(other.get("generation")) is int
        and all(row[k] == other[k] for k in SCOPE)
    )


def _full_charge(row, indexes, identity):
    reservation = indexes["research_budget_reservations"].get(identity)
    settlements = [
        s
        for s in indexes["research_budget_settlements"].values()
        if s["reservation_id"] == identity
    ]
    if (
        not _same(row, reservation)
        or reservation["state"] != "settled"
        or len(settlements) != 1
        or not _same(row, settlements[0])
        or settlements[0]["outcome"] != "conservative_unknown"
        or settlements[0]["charge"] != reservation["amounts"]
    ):
        raise ValueError("Normal OE full conservative settlement required")
    return reservation


def validate_controller_closure_unit(
    row, indexes, physical, check, read_bytes, allowed, reservation_ids
):
    """Read actual protected five-record success chain; Stop alone never qualifies."""
    from runtimes.openevolve_structured.profile import MAX_WALL_MS, STORAGE_FLOOR
    from runtimes.openevolve_structured_host.completion_journal import (
        ControllerCompletion,
        validate_completion,
    )
    from runtimes.openevolve_structured_host.process_journal import (
        ControllerChild,
        ControllerIdentity,
        ControllerLaunch,
        ControllerStop,
        controller_admission,
        validate_records,
    )

    values = {k: v for k, v in row.items() if k != "created_at"}
    for key in (
        "id",
        "owner_id",
        "run_id",
        "attempt_id",
        "logical_operation_id",
        "reservation_id",
    ):
        values[key] = UUID(values[key])
    admission = controller_admission(decode_row(values))
    reservation = indexes["research_budget_reservations"].get(row["reservation_id"])
    if not _same(row, reservation):
        raise ValueError("Normal OE controller hold scope")
    if reservation["state"] == "settled":
        _full_charge(row, indexes, row["reservation_id"])
    elif reservation["state"] not in {"reserved", "uncertain"} or any(
        v["reservation_id"] == row["reservation_id"]
        for v in indexes["research_budget_settlements"].values()
    ):
        raise ValueError("Normal OE unresolved controller must retain its full hold")
    amounts = BudgetVector.model_validate(reservation["amounts"])
    minimum = BudgetVector(
        process_slots=1, wall_milliseconds=MAX_WALL_MS, storage_bytes=STORAGE_FLOOR
    )
    if (
        reservation["purpose"] != "process_unit"
        or reservation["logical_key"]
        != "oe-structured-controller:" + row["logical_operation_id"]
        or reservation["id"] in reservation_ids
        or not amounts.covers(minimum)
    ):
        raise ValueError("Normal OE controller reservation differs")
    state = indexes["research_process_states"].get(row["id"])
    if not _same(row, state):
        raise ValueError("Normal OE controller state scope")
    receipts = [
        r
        for r in indexes["research_process_receipts"].values()
        if r["admission_id"] == row["id"]
    ]
    kinds = {r["kind"]: r for r in receipts}
    prefixes = (
        {"launch_intent"},
        {"launch_intent", "launch_identity"},
        {"launch_intent", "launch_identity", "controller_child"},
        {"launch_intent", "launch_identity", "stop"},
        {"launch_intent", "launch_identity", "controller_child", "stop"},
        {
            "launch_intent",
            "launch_identity",
            "controller_child",
            "stop",
            "controller_completion",
        },
    )
    if len(kinds) != len(receipts) or set(kinds) not in prefixes:
        raise ValueError("Normal OE exact protected receipt prefix required")
    if ("stop" in kinds) != (state["status"] == "stopped") or (
        "stop" not in kinds
        and (
            reservation["state"] == "settled"
            or state["status"]
            not in {"admitted", "launch_intended", "running", "uncertain"}
        )
    ):
        raise ValueError("Normal OE incomplete launch must remain unresolved")
    if any(
        not _same(row, r) or r["payload_sha256"] != digest(r["payload"])
        for r in receipts
    ):
        raise ValueError("Normal OE receipt identity differs")
    prefix = f"research-processes/{row['owner_id']}/{row['id']}/"
    # The committed intent precedes supervisor file creation. Preserve this
    # unlaunched/uncertain prefix without synthesizing a launch/root identity.
    if (
        set(kinds) == {"launch_intent"}
        and prefix + "control/launch.json" not in physical
    ):
        from runtimes.openevolve_structured_host.process_journal import ControllerIntent

        intent = ControllerIntent.model_validate_json(
            canonical(kinds["launch_intent"]["payload"])
        )
        if intent.admission != admission or any(
            key.startswith(prefix) for key in physical
        ):
            raise ValueError(
                "Normal OE intent-only prefix has unexpected physical evidence"
            )
        reservation_ids.add(reservation["id"])
        return {
            "intent": intent,
            "launch": None,
            "identity": None,
            "child": None,
            "stop": None,
            "completion": None,
        }
    models = {
        "launch": ControllerLaunch,
        "identity": ControllerIdentity,
        "child": ControllerChild,
        "stop": ControllerStop,
        "completion": ControllerCompletion,
    }
    mapped = {
        "launch": "launch_intent",
        "identity": "launch_identity",
        "child": "controller_child",
        "stop": "stop",
        "completion": "controller_completion",
    }
    for name in tuple(models):
        if mapped[name] not in kinds:
            if prefix + "control/" + name + ".json" in physical:
                raise ValueError("Normal OE unregistered protected record")
            del models[name]
    records = {}
    for name, model in models.items():
        key = prefix + "control/" + name + ".json"
        if key not in physical:
            raise ValueError("Normal OE protected record missing")
        # The physical inventory's size/hash is only a bound; actual bytes are
        # required, parsed and compared with the registered immutable payload.
        item = physical[key]
        sha, size = item["sha256"], item["size_bytes"]
        if type(size) is not int or not 0 < size <= 16384:
            raise ValueError("Normal OE protected record bound")
        raw = read_bytes(key, sha, size)
        if (
            type(raw) is not bytes
            or len(raw) != size
            or hashlib.sha256(raw).hexdigest() != sha
        ):
            raise ValueError("Normal OE protected bytes differ")
        records[name] = model.model_validate_json(raw)
        if canonical(records[name].model_dump(mode="json")) != raw:
            raise ValueError("Normal OE protected bytes are not canonical")
        check(key, sha, size)
        allowed.add(key)
    launch = records["launch"]
    identity, child, stop, completion = (
        records.get(k) for k in ("identity", "child", "stop", "completion")
    )
    if identity is not None:
        validate_records(launch, identity, child, stop)
    if completion is not None:
        validate_completion(completion, launch, identity, child, stop)
    if launch.intent.admission != admission:
        raise ValueError("Normal OE physical admission differs")
    expected = {
        "launch_intent": launch.intent,
        "launch_identity": identity,
        "controller_child": child,
        "stop": stop,
        "controller_completion": completion,
    }
    if any(
        kinds[k]["payload"] != value.model_dump(mode="json")
        for k, value in expected.items()
        if value is not None
    ):
        raise ValueError("Normal OE physical/registered receipts differ")
    for filename, sha, size in (
        (
            "bootstrap.json",
            launch.intent.bootstrap_sha256,
            launch.intent.bootstrap_size_bytes,
        ),
        ("seed.json", launch.intent.seed_sha256, launch.intent.seed_size_bytes),
    ):
        key = prefix + "inputs/" + filename
        if key in physical:
            check(key, sha, size)
            allowed.add(key)
    reservation_ids.add(reservation["id"])
    return {
        "intent": launch.intent,
        "launch": launch,
        "identity": identity,
        "child": child,
        "stop": stop,
        "completion": completion,
    }


def _registered_read(shared, read_bytes, owner, identity, kind, sha):
    row = shared["research_objects"].get(identity)
    if (
        row is None
        or row["owner_id"] != owner
        or row["object_kind"] != kind
        or row["sha256"] != sha
        or type(row["size_bytes"]) is not int
        or not 0 < row["size_bytes"] <= 16 * 1024**2
    ):
        raise ValueError("Normal OE registered object differs")
    raw = read_bytes(row["storage_key"], sha, row["size_bytes"])
    if (
        type(raw) is not bytes
        or len(raw) != row["size_bytes"]
        or hashlib.sha256(raw).hexdigest() != sha
    ):
        raise ValueError("Normal OE actual registered bytes differ")
    return raw


def validate_provider_exchange(exchange, owned, shared, binding, read_bytes):
    """Original request/response and charged actual SDK receipts, no live context."""
    from packages.openevolve_structured.provider_accounting import (
        validate_completed_usage,
    )
    from packages.openevolve_structured.provider_contracts import (
        OEChatRequest,
        OEChatResponse,
    )
    from packages.openevolve_structured.provider_receipts import (
        OBJECT_KIND,
        parse_receipt,
        validate_chain,
    )
    from packages.openevolve_structured.provider_response import response_bytes

    membership = owned["oe_structured_provider_transports"].get(
        exchange["provider_transport_id"]
    )
    if not _same(exchange, membership):
        raise ValueError("Normal OE exchange membership scope")
    operation = shared["research_model_operations"].get(membership["operation_id"])
    transport = shared["research_model_transports"].get(membership["transport_id"])
    if not _same(exchange, operation) or not _same(exchange, transport):
        raise ValueError("Normal OE operation/transport scope")
    if (
        membership["iteration_intent_id"] != exchange["iteration_intent_id"]
        or transport["operation_id"] != operation["id"]
        or transport["ordinal"] != 1
        or transport["state"] != "completed"
        or operation["state"] != "completed"
        or operation["protocol"] != "mineagent.oe-structured-provider-chat.v1"
        or operation["purpose"] != "chat"
        or operation["ordinal"] != 0
        or operation["request_object_id"] != exchange["request_object_id"]
        or operation["request_object_kind"] != exchange["request_kind"]
        or operation["request_sha256"] != exchange["request_sha256"]
        or membership["request_sha256"] != exchange["request_sha256"]
        or membership["model_binding_sha256"] != binding.sha256
        or [
            t["id"]
            for t in shared["research_model_transports"].values()
            if t["operation_id"] == operation["id"]
        ]
        != [transport["id"]]
    ):
        raise ValueError("Normal OE exact single transport/request binding")
    context = shared["research_operation_contexts"].get(operation["context_id"])
    if (
        not _same(exchange, context)
        or context["context"] != exchange["context"]
        or context["context_sha256"] != exchange["context_sha256"]
        or any(
            a["context_id"] == context["id"]
            for a in shared["research_context_aborts"].values()
        )
    ):
        raise ValueError("Normal OE original context differs")
    raw = _registered_read(
        shared,
        read_bytes,
        exchange["owner_id"],
        exchange["request_object_id"],
        exchange["request_kind"],
        exchange["request_sha256"],
    )
    request = OEChatRequest.model_validate_json(raw)
    if (
        canonical_bytes(request) != raw
        or request.sha256 != exchange["request_sha256"]
        or request.model_dump(mode="json") != operation["request"]
    ):
        raise ValueError("Normal OE original request bytes differ")
    outcomes = [
        o
        for o in shared["research_model_outcomes"].values()
        if o["transport_id"] == transport["id"]
    ]
    if len(outcomes) != 1 or not _same(exchange, outcomes[0]):
        raise ValueError("Normal OE exact completed outcome scope")
    outcome = outcomes[0]
    if (
        outcome["outcome"],
        outcome["response_object_id"],
        outcome["response_object_kind"],
        outcome["response_sha256"],
    ) != (
        "completed",
        exchange["response_object_id"],
        exchange["response_kind"],
        exchange["response_sha256"],
    ):
        raise ValueError("Normal OE completed response differs")
    raw = _registered_read(
        shared,
        read_bytes,
        exchange["owner_id"],
        exchange["response_object_id"],
        exchange["response_kind"],
        exchange["response_sha256"],
    )
    response = OEChatResponse.model_validate_json(raw)
    if response_bytes(response) != raw:
        raise ValueError("Normal OE noncanonical response")
    response, held = validate_completed_usage(request, response, binding)
    reservation = _full_charge(exchange, shared, transport["reservation_id"])
    if (
        reservation["purpose"] != "model_transport"
        or reservation["logical_key"]
        != "oe-structured-provider:" + exchange["iteration_intent_id"]
        or BudgetVector.model_validate(reservation["amounts"]) != held
        or BudgetVector.model_validate(outcome["usage"]) != held
    ):
        raise ValueError("Normal OE actual usage/full charge differs")
    rows = [
        r
        for r in owned["oe_structured_provider_receipts"].values()
        if r["membership_id"] == membership["id"]
    ]
    if len(rows) != 3 or {r["kind"] for r in rows} != {"intent", "started", "stopped"}:
        raise ValueError("Normal OE exact three SDK receipts required")
    documents = {}
    for row in rows:
        if not _same(exchange, row) or row["receipt_object_kind"] != OBJECT_KIND:
            raise ValueError("Normal OE SDK receipt scope/kind")
        raw = _registered_read(
            shared,
            read_bytes,
            exchange["owner_id"],
            row["receipt_object_id"],
            OBJECT_KIND,
            row["receipt_sha256"],
        )
        document = parse_receipt(raw)
        if (
            len(raw) != row["size_bytes"]
            or document.kind != row["kind"]
            or str(document.transport_id) != transport["id"]
            or document.request_sha256 != request.sha256
            or document.model_binding_sha256 != binding.sha256
            or any(str(getattr(document, k)) != str(exchange[k]) for k in SCOPE)
        ):
            raise ValueError("Normal OE SDK physical receipt identity")
        documents[row["kind"]] = document
    if documents["intent"].maximum_duration_ms != binding.request_deadline_ms:
        raise ValueError("Normal OE SDK original duration differs")
    validate_chain(
        documents["intent"], documents["started"], documents["stopped"], completed=True
    )
    return request, response


def validate_checkpoint_prefix(upload, owned, shared, read_bytes):
    """Validate an actual incomplete transfer without inventing its publication."""
    from packages.openevolve_structured.checkpoint import canonical as native_canonical
    from packages.openevolve_structured.upload_contracts import Begin, ChunkDocument

    intent = owned["oe_structured_checkpoint_intents"][upload["intent_id"]]
    cursor = owned["oe_structured_control_states"][intent["control_state_id"]]
    if (
        not _same(upload, intent)
        or not _same(upload, cursor)
        or upload["epoch_id"] != intent["epoch_id"]
        or upload["epoch_id"] != cursor["epoch_id"]
        or intent["state_sha256"] != cursor["state_sha256"]
        or intent["frontier"] != cursor["frontier"]
        or cursor["phase"] not in {"seed", "applied"}
    ):
        raise ValueError("Normal OE pending checkpoint cursor differs")
    begin = Begin.model_validate_json(upload["inventory_canonical"])
    if (
        native_canonical(begin.model_dump(mode="json")).decode()
        != upload["inventory_canonical"]
        or hashlib.sha256(upload["inventory_canonical"].encode()).hexdigest()
        != upload["inventory_sha256"]
        or (str(begin.token), begin.total_bytes, len(begin.files), len(begin.chunks))
        != (
            intent["id"],
            upload["total_bytes"],
            upload["file_count"],
            upload["chunk_count"],
        )
    ):
        raise ValueError("Normal OE pending transfer declaration differs")
    held = shared["research_budget_reservations"][upload["reservation_id"]]
    if (
        not _same(upload, held)
        or held["purpose"] != "object_storage"
        or held["logical_key"] != "oe-structured-upload:" + intent["id"]
        or BudgetVector.model_validate(held["amounts"])
        != BudgetVector(storage_bytes=begin.transfer_peak_bytes)
    ):
        raise ValueError("Normal OE pending transfer hold differs")
    if held["state"] == "settled":
        _full_charge(upload, shared, held["id"])
    elif held["state"] not in {"reserved", "uncertain"} or any(
        row["reservation_id"] == held["id"]
        for row in shared["research_budget_settlements"].values()
    ):
        raise ValueError("Normal OE pending transfer unresolved charge")
    chunks = sorted(
        (
            r
            for r in owned["oe_structured_checkpoint_chunks"].values()
            if r["upload_id"] == upload["id"]
        ),
        key=lambda r: r["sequence"],
    )
    if len(chunks) > len(begin.chunks) or [r["sequence"] for r in chunks] != list(
        range(len(chunks))
    ):
        raise ValueError("Normal OE pending transfer chunk prefix gap")
    for row in chunks:
        if (
            not _same(upload, row)
            or row["epoch_id"] != upload["epoch_id"]
            or row["object_kind"] != "oe_structured_checkpoint_chunk"
            or (row["file_name"], row["offset_bytes"], row["size_bytes"])
            != begin.chunks[row["sequence"]]
        ):
            raise ValueError("Normal OE pending chunk declaration differs")
        raw = _registered_read(
            shared,
            read_bytes,
            upload["owner_id"],
            row["object_id"],
            row["object_kind"],
            row["object_sha256"],
        )
        document = ChunkDocument.model_validate_json(raw)
        chunk = document.chunk
        if (
            native_canonical(document.model_dump(mode="json")) != raw
            or any(str(getattr(document, k)) != str(upload[k]) for k in SCOPE)
            or (
                str(document.epoch_id),
                str(document.upload_id),
                str(chunk.token),
                chunk.sequence,
                chunk.name,
                chunk.offset,
                chunk.sha256,
                len(chunk.bytes()),
            )
            != (
                upload["epoch_id"],
                upload["id"],
                intent["id"],
                row["sequence"],
                row["file_name"],
                row["offset_bytes"],
                row["chunk_sha256"],
                row["size_bytes"],
            )
        ):
            raise ValueError("Normal OE pending chunk actual bytes differ")
    return begin


def validate_checkpoint_publication(publication, owned, shared, domain, read_bytes):
    intent = owned["oe_structured_checkpoint_intents"][publication["intent_id"]]
    completion = owned["oe_structured_checkpoint_completions"][
        publication["completion_id"]
    ]
    upload = owned["oe_structured_checkpoint_uploads"][completion["upload_id"]]
    if (
        not _same(publication, intent)
        or not _same(publication, completion)
        or publication["epoch_id"] != intent["epoch_id"]
        or publication["epoch_id"] != completion["epoch_id"]
        or upload["intent_id"] != intent["id"]
        or publication["control_state_id"] != intent["control_state_id"]
        or publication["state_sha256"] != intent["state_sha256"]
    ):
        raise ValueError("Normal OE checkpoint publication scope/cursor")
    return validate_checkpoint_completion(completion, owned, shared, domain, read_bytes)


def validate_checkpoint_completion(completion, owned, shared, domain, read_bytes):
    """Reassemble retained chunks/files and compare their actual native state."""
    from backend.app.oe_structured_checkpoint_services import native_files_state
    from packages.openevolve_structured.checkpoint import canonical as native_canonical
    from packages.openevolve_structured.checkpoint import state_sha256
    from packages.openevolve_structured.upload_contracts import (
        Begin,
        ChunkDocument,
        FileEnvelope,
        Inventory,
    )

    publication = completion  # Actual retained completion supplies scope, not a synthesized publication.
    upload = owned["oe_structured_checkpoint_uploads"].get(completion["upload_id"])
    intent = owned["oe_structured_checkpoint_intents"][upload["intent_id"]]
    cursor = owned["oe_structured_control_states"].get(intent["control_state_id"])
    if (
        not _same(publication, upload)
        or not _same(publication, cursor)
        or any(
            r["epoch_id"] != publication["epoch_id"]
            for r in (intent, completion, upload, cursor)
        )
        or upload["intent_id"] != intent["id"]
        or intent["control_state_id"] != cursor["id"]
        or intent["state_sha256"] != cursor["state_sha256"]
        or intent["frontier"] != cursor["frontier"]
        or cursor["phase"] not in {"seed", "applied"}
    ):
        raise ValueError("Normal OE checkpoint exact cursor relation")
    begin = Begin.model_validate_json(upload["inventory_canonical"])
    if (
        native_canonical(begin.model_dump(mode="json")).decode()
        != upload["inventory_canonical"]
        or hashlib.sha256(upload["inventory_canonical"].encode()).hexdigest()
        != upload["inventory_sha256"]
        or (str(begin.token), begin.total_bytes, len(begin.files), len(begin.chunks))
        != (
            intent["id"],
            upload["total_bytes"],
            upload["file_count"],
            upload["chunk_count"],
        )
    ):
        raise ValueError("Normal OE original upload declaration")
    held = shared["research_budget_reservations"].get(upload["reservation_id"])
    if (
        not _same(publication, held)
        or held["purpose"] != "object_storage"
        or held["logical_key"] != "oe-structured-upload:" + intent["id"]
        or BudgetVector.model_validate(held["amounts"])
        != BudgetVector(storage_bytes=begin.transfer_peak_bytes)
    ):
        raise ValueError("Normal OE original transfer hold")
    if held["state"] == "settled":
        _full_charge(publication, shared, held["id"])
    elif held["state"] not in {"reserved", "uncertain"} or any(
        v["reservation_id"] == held["id"]
        for v in shared["research_budget_settlements"].values()
    ):
        raise ValueError("Normal OE unresolved transfer must remain held")
    if completion["inventory_object_kind"] != "oe_structured_checkpoint_inventory":
        raise ValueError("Normal OE inventory kind")
    raw = _registered_read(
        shared,
        read_bytes,
        publication["owner_id"],
        completion["inventory_object_id"],
        completion["inventory_object_kind"],
        completion["inventory_object_sha256"],
    )
    inventory = Inventory.model_validate_json(raw)
    if (
        native_canonical(inventory.model_dump(mode="json")) != raw
        or any(str(getattr(inventory, k)) != str(publication[k]) for k in SCOPE)
        or (str(inventory.epoch_id), str(inventory.token), str(inventory.upload_id))
        != (publication["epoch_id"], intent["id"], upload["id"])
        or [(f.name, f.sha256, f.size_bytes) for f in inventory.files]
        != [(f.name, f.sha256, f.size_bytes) for f in begin.files]
    ):
        raise ValueError("Normal OE retained inventory relation")
    rows = sorted(
        (
            r
            for r in owned["oe_structured_checkpoint_chunks"].values()
            if r["upload_id"] == upload["id"]
        ),
        key=lambda r: r["sequence"],
    )
    if [r["sequence"] for r in rows] != list(range(len(begin.chunks))):
        raise ValueError("Normal OE complete contiguous checkpoint chunks required")
    pieces = {f.name: bytearray() for f in begin.files}
    for row, expected in zip(rows, begin.chunks, strict=True):
        if (
            not _same(publication, row)
            or row["epoch_id"] != publication["epoch_id"]
            or row["object_kind"] != "oe_structured_checkpoint_chunk"
            or (row["file_name"], row["offset_bytes"], row["size_bytes"]) != expected
        ):
            raise ValueError("Normal OE chunk row scope/offset")
        raw = _registered_read(
            shared,
            read_bytes,
            publication["owner_id"],
            row["object_id"],
            row["object_kind"],
            row["object_sha256"],
        )
        doc = ChunkDocument.model_validate_json(raw)
        chunk = doc.chunk
        if (
            native_canonical(doc.model_dump(mode="json")) != raw
            or any(str(getattr(doc, k)) != str(publication[k]) for k in SCOPE)
            or (
                str(doc.epoch_id),
                str(doc.upload_id),
                str(chunk.token),
                chunk.sequence,
                chunk.name,
                chunk.offset,
                chunk.sha256,
            )
            != (
                publication["epoch_id"],
                upload["id"],
                intent["id"],
                row["sequence"],
                row["file_name"],
                row["offset_bytes"],
                row["chunk_sha256"],
            )
        ):
            raise ValueError("Normal OE actual chunk bytes identity")
        value = chunk.bytes()
        if len(value) != row["size_bytes"]:
            raise ValueError("Normal OE chunk size")
        pieces[chunk.name].extend(value)
    files = {}
    for ref in inventory.files:
        raw = _registered_read(
            shared,
            read_bytes,
            publication["owner_id"],
            str(ref.object_id),
            "oe_structured_checkpoint_file",
            ref.object_sha256,
        )
        envelope = FileEnvelope.model_validate_json(raw)
        if native_canonical(envelope.model_dump(mode="json")) != raw or (
            envelope.name,
            envelope.sha256,
            envelope.size_bytes,
        ) != (ref.name, ref.sha256, ref.size_bytes):
            raise ValueError("Normal OE exact retained file envelope")
        files[ref.name] = envelope.bytes()
        if files[ref.name] != bytes(pieces[ref.name]):
            raise ValueError("Normal OE file differs from original retained chunks")
    state = native_files_state(files, domain)
    if (
        state_sha256(state) != cursor["state_sha256"]
        or native_canonical(state).decode() != cursor["state_canonical"]
    ):
        raise ValueError("Normal OE actual native checkpoint differs from cursor")
    return state


def manifest_keys(closure):
    from packages.openevolve_structured.provider_run import ProviderStructuredRunConfig

    schema = closure.get("schema_version") if type(closure) is dict else None
    own = indexes(closure, schema=schema)
    result = set()
    for row in own["oe_structured_specs"].values():
        if schema == SCHEMA_V2:
            from packages.openevolve_structured.workflow_run import parse_provider_run

            config = parse_provider_run(row["config_canonical"].encode())
        else:
            config = ProviderStructuredRunConfig.model_validate_json(
                row["config_canonical"]
            )
        if (
            canonical_bytes(config).decode() != row["config_canonical"]
            or digest(config) != row["config_sha256"]
            or config.model_dump(mode="json") != row["config"]
        ):
            raise ValueError("Normal OE manifest config commitment")
        prefix = f"datasets/{row['owner_id']}/{config.fixed_inputs.dataset_version_id}/"
        result.update(
            prefix + name
            for name in (
                "manifest.json",
                "preview.json",
                "sessions.json",
                "actions.json",
                "corporate-action-coverage.json",
            )
        )
    for row in own["oe_structured_engine_outcomes"].values():
        result.add(
            f"research-processes/{row['owner_id']}/{row['admission_id']}/output/result.json"
        )
    return result


def validate_declarations(owned, shared_runs, versions, *, workflow_context=None):
    """Exact fresh normal user declarations, not old025 profile relabeling."""
    from collections.abc import Mapping

    from backend.app.oe_structured_provider_submission import (
        ProviderStructuredSearchRequest,
    )
    from backend.app.oe_workflow_submission_schemas import (
        WorkflowStructuredSearchRequest,
    )
    from packages.openevolve_structured.provider_run import ProviderStructuredRunConfig
    from packages.openevolve_structured.workflow_run import WorkflowStructuredRunConfig
    from packages.structured_factor_strategy.contracts import StructuredProgram

    if workflow_context is not None and not isinstance(workflow_context, Mapping):
        raise ValueError("Exact reconstructed workflow OE context mapping required")
    versions_allowed = {"mineagent.oe-structured-provider-run.v1"}
    if workflow_context is not None:
        versions_allowed.add("mineagent.oe-structured-provider-run.v2")
    runs = owned["research_runs"]
    if set(runs) != {
        key
        for key, row in shared_runs.items()
        if row.get("engine") == "openevolve"
        and row.get("config_schema_version") in versions_allowed
    }:
        raise ValueError("Normal OE full/shared run inventory differs")
    configs = {}
    if set(owned["oe_structured_specs"]) != set(runs):
        raise ValueError("Normal OE exact spec/run inventory")
    submissions = owned["oe_structured_submissions"]
    if {r["run_id"] for r in submissions.values()} != set(runs) or len(
        submissions
    ) != len(runs):
        raise ValueError("Normal OE exact submission/run inventory")
    for run_id, run in runs.items():
        if any(
            run.get(k) != v
            for k, v in shared_runs[run_id].items()
            if k in RUN_FIELDS.split()
        ):
            raise ValueError("Normal OE shared/full run identity")
        spec = owned["oe_structured_specs"][run_id]
        workflow = (
            run["config"].get("schema_version")
            == "mineagent.oe-structured-provider-run.v2"
        )
        if workflow and (workflow_context is None or run_id not in workflow_context):
            raise ValueError("Workflow OE requires actual declared context")
        config = (
            WorkflowStructuredRunConfig if workflow else ProviderStructuredRunConfig
        ).model_validate_json(spec["config_canonical"])
        request = (
            WorkflowStructuredSearchRequest
            if workflow
            else ProviderStructuredSearchRequest
        ).model_validate_json(spec["request_canonical"])
        if workflow and (
            workflow_context[run_id] != config
            or request.workflow_context != config.workflow_context
        ):
            raise ValueError("Workflow OE cold context differs from actual declaration")
        request.require_binding(config.model_binding)
        version = versions.get(spec["seed_program_version_id"])
        submission = submissions.get(spec["submission_id"])
        if version is None or submission is None:
            raise ValueError("Normal OE seed/submission missing")
        seed = StructuredProgram.model_validate_json(version["program_canonical"])
        if (
            (
                run["purpose"],
                run["engine"],
                run["generation"],
                run["strategy_version_id"],
            )
            != ("strategy_search", "openevolve", 1, None)
            or run["owner_id"] != str(config.search.owner_id)
            or run_id != str(config.search.run_id)
            or run["dataset_version_id"] != str(config.fixed_inputs.dataset_version_id)
            or run["config"] != config.model_dump(mode="json")
            or run["request_sha256"] != digest(config)
            or run["status"] not in {"succeeded", "failed", "cancelled", "interrupted"}
            or spec["owner_id"] != run["owner_id"]
            or spec["config"] != run["config"]
            or canonical_bytes(config).decode() != spec["config_canonical"]
            or digest(config) != spec["config_sha256"]
            or spec["request"] != request.model_dump(mode="json")
            or canonical_bytes(request).decode() != spec["request_canonical"]
            or request.sha256 != spec["request_sha256"]
            or request.sha256
            != (
                config.workflow_request_sha256
                if workflow
                else config.authenticated_request_sha256
            )
            or str(config.search.execution_binding_id) != run_id
            or spec["snapshot_id"] != str(request.snapshot_id)
            or spec["snapshot_id"] != str(config.fixed_inputs.snapshot_id)
            or spec["dataset_id"] != str(config.fixed_inputs.dataset_version_id)
            or request.objective != config.search.objective
            or request.maximum_mutation_slots != config.search.maximum_mutation_slots
            or request.budget != config.budget
            or seed != config.search.domain.seed_program
            or seed != request.seed_parameters.program(seed.signal_window)
            or version["owner_id"] != run["owner_id"]
            or version["parent_version_id"] is not None
            or version["program"] != seed.model_dump(mode="json")
            or canonical_bytes(seed).decode() != version["program_canonical"]
            or digest(seed) != version["program_sha256"]
            or submission["owner_id"] != run["owner_id"]
            or submission["run_id"] != run_id
            or submission["origin"]
            != ("workflow_stage" if workflow else "authenticated_user_submission")
            or any(
                submission[k] != spec[k]
                for k in (
                    "request_canonical",
                    "request_sha256",
                    "preflight_sha256",
                    "config_sha256",
                )
            )
        ):
            raise ValueError("Normal OE exact retained declaration differs")
        configs[run_id] = config
    if workflow_context is not None and set(workflow_context) != {
        key
        for key, value in configs.items()
        if type(value) is WorkflowStructuredRunConfig
    }:
        raise ValueError("Workflow OE context inventory differs")
    return configs


def reconstruct_execution(
    execution,
    candidate,
    config,
    publication,
    dataset,
    read_retained,
    *,
    declared_upload=False,
):
    """Rebuild027 inputs with the unchanged pure assembler, never fake025 IDs."""
    from types import SimpleNamespace

    from backend.app.oe_structured_execution_input import _ReconstructionDeclaration
    from packages.factors.contracts import FeatureRow
    from packages.factors.execution_snapshot import ExecutionSnapshotManifest
    from packages.marketdata.normalize import canonical_json as marketdata_bytes
    from packages.openevolve_structured.candidate import project_candidate
    from packages.rdagent_adapter.configured_source import ARTIFACTS as SOURCE_ARTIFACTS
    from packages.rdagent_adapter.configured_source import parse_retained_source
    from packages.strategy_sdk import JsonLimits, parse_json
    from packages.structured_factor_strategy.assembly import assemble_input
    from packages.structured_factor_strategy.market import ARTIFACTS, stage_market
    from packages.structured_factor_strategy.wire import write_input

    fixed = config.fixed_inputs
    program = project_candidate(
        candidate["candidate_canonical"].encode("ascii"), config.search.domain
    ).program
    manifest = ExecutionSnapshotManifest.model_validate_json(
        canonical_bytes(publication["manifest"])
    )
    if (
        not _same(execution, candidate)
        or execution["candidate_id"] != candidate["id"]
        or execution["program_version_id"] != candidate["program_version_id"]
        or execution["snapshot_id"] != str(fixed.snapshot_id)
        or publication["id"] != str(fixed.snapshot_id)
        or publication["owner_id"] != execution["owner_id"]
        or publication["manifest_sha256"] != digest(manifest)
        or digest(manifest) != fixed.snapshot_manifest_sha256
        or manifest.input_sha256 != fixed.snapshot_input_sha256
        or manifest.output_rows_sha256 != fixed.snapshot_output_sha256
        or manifest.input.version.factor_version_id != fixed.factor_version_id
        or dataset["id"] != str(fixed.dataset_version_id)
        or dataset["owner_id"] != execution["owner_id"]
        or dataset["content_sha256"] != fixed.dataset_sha256
        or dataset["market"] != "US"
        or dataset["storage_key"] != f"datasets/{execution['owner_id']}/{dataset['id']}"
    ):
        raise ValueError("Normal OE actual fixed snapshot/dataset relation")
    key = dataset["storage_key"]
    raw_manifest = read_retained(key + "/manifest.json", 2 * 1024**2)
    if raw_manifest != marketdata_bytes(dataset["manifest"]):
        raise ValueError("Normal OE retained dataset declaration differs")
    from backend.app.declared_upload_backup import retained_artifacts

    artifacts = (
        retained_artifacts(
            dataset["manifest"], key, read_retained, declared_upload=declared_upload
        )
        if dataset["manifest"].get("schema_version") == 3
        else {
            name: read_retained(key + "/" + name, 32 * 1024**2)
            for name in sorted(ARTIFACTS)
        }
    )
    if len(raw_manifest) + sum(map(len, artifacts.values())) > 64 * 1024**2:
        raise ValueError("Normal OE retained source bound")
    source = parse_retained_source(
        dataset_id=fixed.dataset_version_id,
        manifest_sha256=fixed.dataset_sha256,
        manifest_bytes=raw_manifest,
        artifacts=artifacts
        if dataset["manifest"].get("schema_version") == 3
        else {k: artifacts[k] for k in SOURCE_ARTIFACTS},
        declared_upload=declared_upload,
        owner_id=UUID(dataset["owner_id"]),
        policy=fixed.source_policy,
        input_names=tuple(x.name for x in manifest.input.definition.input_fields),
    )
    if (str(source.source_start), str(source.source_end)) != (
        dataset["start_date"],
        dataset["end_date"],
    ):
        raise ValueError("Normal OE actual dataset extent differs")
    market = stage_market(
        raw_manifest,
        artifacts,
        dataset_sha256=fixed.dataset_sha256,
        window=config.search.objective.window,
        declared_upload=declared_upload,
        dataset_id=UUID(dataset["id"]),
        owner_id=UUID(dataset["owner_id"]),
    )
    raw = read_retained(manifest.assembled_rows.storage_key, 16 * 1024**2)
    rows_doc = parse_json(
        raw,
        limits=JsonLimits(max_bytes=16 * 1024**2, max_nodes=1000000, max_items=100000),
    )
    if (
        type(rows_doc) is not dict
        or set(rows_doc) != {"schema_version", "input_sha256", "rows"}
        or rows_doc["schema_version"] != "mineagent.execution-feature-rows.v1"
        or rows_doc["input_sha256"] != manifest.input_sha256
        or canonical_bytes(rows_doc) != raw
        or hashlib.sha256(raw).hexdigest() != manifest.assembled_rows.sha256
        or len(raw) != manifest.assembled_rows.size_bytes
    ):
        raise ValueError("Normal OE original factor rows differ")
    rows = tuple(
        FeatureRow.model_validate_json(canonical_bytes(r)) for r in rows_doc["rows"]
    )
    declaration = _ReconstructionDeclaration(
        UUID(candidate["program_version_id"]),
        candidate["program_sha256"],
        fixed.dataset_version_id,
        fixed.dataset_sha256,
        (fixed.snapshot_id,),
        config.search.objective.window,
        config.search.objective.initial_cash,
        fixed.source_policy,
        config.runtime.engine_image_id,
        config.runtime.engine_installed_parent_image_id,
        config.runtime.engine_dependency_lock_sha256,
    )
    scope = SimpleNamespace(
        **{k: UUID(execution[k]) for k in ("owner_id", "run_id", "attempt_id")},
        generation=execution["generation"],
    )
    value = assemble_input(
        fence=scope,
        execution_id=UUID(execution["id"]),
        config=declaration,
        program=program,
        snapshot=manifest,
        rows=rows,
        source=source,
        market=market,
    )
    raw = write_input(value)
    if (
        raw.decode(),
        value.model_dump(mode="json"),
        hashlib.sha256(raw).hexdigest(),
        len(raw),
        hashlib.sha256(market.content).hexdigest(),
        len(market.content),
    ) != (
        execution["strategy_input_canonical"],
        execution["strategy_input"],
        execution["strategy_input_sha256"],
        execution["strategy_input_size_bytes"],
        execution["market_sha256"],
        execution["market_size_bytes"],
    ):
        raise ValueError("Normal OE actual engine input reconstruction differs")
    if (execution["market_sha256"], execution["market_size_bytes"]) != (
        fixed.market_sha256,
        fixed.market_size_bytes,
    ):
        raise ValueError("Normal OE fixed retained market differs")
    return value, market.content


def validate_engine_outcome(
    outcome,
    execution,
    candidate,
    config,
    publication,
    dataset,
    shared,
    physical,
    read_bytes,
    *,
    declared_upload=False,
):
    from backend.app.structured_process_backup import validate_structured_closure_unit
    from packages.openevolve_structured.objective import project_result
    from packages.strategy_sdk import JsonLimits, parse_json
    from packages.structured_factor_strategy.wire import read_result, write_input
    from runtimes.research_supervisor.structured_factor_supervisor import (
        check_input_bytes,
    )
    from runtimes.structured_factor_engine.journal import StructuredIntent

    def retained(key, maximum):
        record = physical.get(key)
        if (
            record is None
            or type(record["size_bytes"]) is not int
            or not 0 < record["size_bytes"] <= maximum
        ):
            raise ValueError("Normal OE retained physical input missing/oversized")
        raw = read_bytes(key, record["sha256"], record["size_bytes"])
        if (
            type(raw) is not bytes
            or len(raw) != record["size_bytes"]
            or hashlib.sha256(raw).hexdigest() != record["sha256"]
        ):
            raise ValueError("Normal OE actual physical input bytes differ")
        return raw

    admission = shared["research_process_admissions"].get(outcome["admission_id"])
    if (
        not _same(execution, outcome)
        or not _same(execution, admission)
        or outcome["execution_id"] != execution["id"]
        or outcome["candidate_id"] != candidate["id"]
        or admission["logical_operation_id"] != execution["id"]
        or admission["image_id"] != config.runtime.engine_image_id
    ):
        raise ValueError("Normal OE actual engine outcome/admission identity")

    def check(key, sha, size):
        raw = retained(key, max(size, 1))
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != sha:
            raise ValueError("Normal OE engine protected commitment differs")

    def read_json(key):
        return parse_json(
            retained(key, 16384),
            limits=JsonLimits(max_bytes=16384, max_nodes=10000, max_items=2000),
        )

    validate_structured_closure_unit(
        admission, shared, physical, check, read_json, set(), set()
    )
    receipts = {
        r["kind"]: r
        for r in shared["research_process_receipts"].values()
        if r["admission_id"] == admission["id"]
    }
    if set(receipts) != {"launch_intent", "launch_identity", "stop", "extraction"}:
        raise ValueError("Normal OE actual extracted engine receipt chain required")
    for kind in ("stop", "extraction"):
        receipt = receipts[kind]
        if (
            outcome[kind + "_receipt_id"],
            outcome[kind + "_receipt_kind"],
            outcome[kind + "_receipt_sha256"],
        ) != (receipt["id"], kind, receipt["payload_sha256"]):
            raise ValueError(
                "Normal OE retained outcome references wrong process receipt"
            )
    value, market = reconstruct_execution(
        execution,
        candidate,
        config,
        publication,
        dataset,
        retained,
        declared_upload=declared_upload,
    )
    intent = StructuredIntent.model_validate_json(
        canonical(receipts["launch_intent"]["payload"])
    )
    if (
        str(intent.inputs.execution_id),
        str(intent.inputs.structured_strategy_version_id),
        str(intent.inputs.snapshot_id),
    ) != (execution["id"], execution["program_version_id"], execution["snapshot_id"]):
        raise ValueError("Normal OE protected native input identities differ")
    strategy = write_input(value)
    check_input_bytes(
        intent, {"/input/strategy.json": strategy, "/input/market.json": market}
    )
    raw = retained(
        f"research-processes/{outcome['owner_id']}/{outcome['admission_id']}/output/result.json",
        16 * 1024**2,
    )
    result = read_result(raw, value)
    extraction = receipts["extraction"]["payload"]
    if (
        canonical_bytes(result) != raw
        or raw.decode() != outcome["result_canonical"]
        or hashlib.sha256(raw).hexdigest() != outcome["result_sha256"]
        or len(raw) != outcome["result_size_bytes"]
        or extraction["sha256"] != outcome["result_sha256"]
        or extraction["size_bytes"] != outcome["result_size_bytes"]
    ):
        raise ValueError("Normal OE original native result differs")
    measurement = project_result(strategy, raw, config.search.objective)
    if (
        canonical_bytes(measurement).decode() != outcome["measurement_canonical"]
        or digest(measurement) != outcome["measurement_sha256"]
        or digest(config.search.objective) != outcome["objective_sha256"]
    ):
        raise ValueError("Normal OE actual measurement differs")
    return measurement


REFERENCES = (
    (
        "oe_structured_candidates",
        ("owner_id", "program_version_id"),
        "structured_strategy_versions",
        ("owner_id", "id"),
    ),
    (
        "oe_structured_candidates",
        ("owner_id", "run_id"),
        "oe_structured_specs",
        ("owner_id", "run_id"),
    ),
    (
        "oe_structured_candidates",
        ("owner_id", "run_id", "attempt_id", "generation", "epoch_id"),
        "oe_structured_epochs",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_candidates",
        ("owner_id", "run_id", "attempt_id", "generation", "parent_candidate_id"),
        "oe_structured_candidates",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_candidates",
        ("owner_id", "run_id", "attempt_id", "generation", "provider_exchange_id"),
        "oe_structured_provider_exchanges",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_candidates",
        ("run_id", "generation", "attempt_id"),
        "execution_attempts",
        ("run_id", "generation", "id"),
    ),
    (
        "oe_structured_checkpoint_chunks",
        ("owner_id", "object_id", "object_kind", "object_sha256"),
        "research_objects",
        ("owner_id", "id", "object_kind", "sha256"),
    ),
    (
        "oe_structured_checkpoint_chunks",
        ("owner_id", "run_id"),
        "oe_structured_specs",
        ("owner_id", "run_id"),
    ),
    (
        "oe_structured_checkpoint_chunks",
        ("owner_id", "run_id", "attempt_id", "generation", "epoch_id"),
        "oe_structured_epochs",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_checkpoint_chunks",
        ("owner_id", "run_id", "attempt_id", "generation", "upload_id"),
        "oe_structured_checkpoint_uploads",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_checkpoint_chunks",
        ("run_id", "generation", "attempt_id"),
        "execution_attempts",
        ("run_id", "generation", "id"),
    ),
    (
        "oe_structured_checkpoint_completions",
        (
            "owner_id",
            "inventory_object_id",
            "inventory_object_kind",
            "inventory_object_sha256",
        ),
        "research_objects",
        ("owner_id", "id", "object_kind", "sha256"),
    ),
    (
        "oe_structured_checkpoint_completions",
        ("owner_id", "run_id"),
        "oe_structured_specs",
        ("owner_id", "run_id"),
    ),
    (
        "oe_structured_checkpoint_completions",
        ("owner_id", "run_id", "attempt_id", "generation", "epoch_id"),
        "oe_structured_epochs",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_checkpoint_completions",
        ("owner_id", "run_id", "attempt_id", "generation", "upload_id"),
        "oe_structured_checkpoint_uploads",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_checkpoint_completions",
        ("run_id", "generation", "attempt_id"),
        "execution_attempts",
        ("run_id", "generation", "id"),
    ),
    (
        "oe_structured_checkpoint_intents",
        ("owner_id", "run_id"),
        "oe_structured_specs",
        ("owner_id", "run_id"),
    ),
    (
        "oe_structured_checkpoint_intents",
        ("owner_id", "run_id", "attempt_id", "generation", "control_state_id"),
        "oe_structured_control_states",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_checkpoint_intents",
        ("owner_id", "run_id", "attempt_id", "generation", "epoch_id"),
        "oe_structured_epochs",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_checkpoint_intents",
        ("run_id", "generation", "attempt_id"),
        "execution_attempts",
        ("run_id", "generation", "id"),
    ),
    (
        "oe_structured_checkpoint_publications",
        ("owner_id", "run_id"),
        "oe_structured_specs",
        ("owner_id", "run_id"),
    ),
    (
        "oe_structured_checkpoint_publications",
        ("owner_id", "run_id", "attempt_id", "generation", "completion_id"),
        "oe_structured_checkpoint_completions",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_checkpoint_publications",
        ("owner_id", "run_id", "attempt_id", "generation", "control_state_id"),
        "oe_structured_control_states",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_checkpoint_publications",
        ("owner_id", "run_id", "attempt_id", "generation", "epoch_id"),
        "oe_structured_epochs",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_checkpoint_publications",
        ("owner_id", "run_id", "attempt_id", "generation", "intent_id"),
        "oe_structured_checkpoint_intents",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_checkpoint_publications",
        ("run_id", "generation", "attempt_id"),
        "execution_attempts",
        ("run_id", "generation", "id"),
    ),
    (
        "oe_structured_checkpoint_uploads",
        ("owner_id", "run_id"),
        "oe_structured_specs",
        ("owner_id", "run_id"),
    ),
    (
        "oe_structured_checkpoint_uploads",
        ("owner_id", "run_id", "attempt_id", "generation", "epoch_id"),
        "oe_structured_epochs",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_checkpoint_uploads",
        ("owner_id", "run_id", "attempt_id", "generation", "intent_id"),
        "oe_structured_checkpoint_intents",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_checkpoint_uploads",
        ("owner_id", "run_id", "attempt_id", "generation", "reservation_id"),
        "research_budget_reservations",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_checkpoint_uploads",
        ("run_id", "generation", "attempt_id"),
        "execution_attempts",
        ("run_id", "generation", "id"),
    ),
    (
        "oe_structured_control_states",
        ("owner_id", "run_id"),
        "oe_structured_specs",
        ("owner_id", "run_id"),
    ),
    (
        "oe_structured_control_states",
        ("owner_id", "run_id", "attempt_id", "generation", "epoch_id"),
        "oe_structured_epochs",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_control_states",
        ("owner_id", "run_id", "attempt_id", "generation", "previous_id"),
        "oe_structured_control_states",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_control_states",
        ("run_id", "generation", "attempt_id"),
        "execution_attempts",
        ("run_id", "generation", "id"),
    ),
    (
        "oe_structured_engine_outcomes",
        ("owner_id", "run_id"),
        "oe_structured_specs",
        ("owner_id", "run_id"),
    ),
    (
        "oe_structured_engine_outcomes",
        ("owner_id", "run_id", "attempt_id", "generation", "admission_id"),
        "research_process_admissions",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_engine_outcomes",
        (
            "owner_id",
            "run_id",
            "attempt_id",
            "generation",
            "admission_id",
            "extraction_receipt_id",
            "extraction_receipt_kind",
        ),
        "research_process_receipts",
        (
            "owner_id",
            "run_id",
            "attempt_id",
            "generation",
            "admission_id",
            "id",
            "kind",
        ),
    ),
    (
        "oe_structured_engine_outcomes",
        (
            "owner_id",
            "run_id",
            "attempt_id",
            "generation",
            "admission_id",
            "stop_receipt_id",
            "stop_receipt_kind",
        ),
        "research_process_receipts",
        (
            "owner_id",
            "run_id",
            "attempt_id",
            "generation",
            "admission_id",
            "id",
            "kind",
        ),
    ),
    (
        "oe_structured_engine_outcomes",
        ("owner_id", "run_id", "attempt_id", "generation", "candidate_id"),
        "oe_structured_candidates",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_engine_outcomes",
        ("owner_id", "run_id", "attempt_id", "generation", "execution_id"),
        "oe_structured_executions",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_engine_outcomes",
        ("run_id", "generation", "attempt_id"),
        "execution_attempts",
        ("run_id", "generation", "id"),
    ),
    (
        "oe_structured_epochs",
        ("owner_id", "run_id"),
        "oe_structured_specs",
        ("owner_id", "run_id"),
    ),
    (
        "oe_structured_epochs",
        ("owner_id", "run_id", "attempt_id", "generation", "controller_admission_id"),
        "research_process_admissions",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_epochs",
        ("run_id", "generation", "attempt_id"),
        "execution_attempts",
        ("run_id", "generation", "id"),
    ),
    (
        "oe_structured_executions",
        ("owner_id", "program_version_id"),
        "structured_strategy_versions",
        ("owner_id", "id"),
    ),
    (
        "oe_structured_executions",
        ("owner_id", "run_id"),
        "oe_structured_specs",
        ("owner_id", "run_id"),
    ),
    (
        "oe_structured_executions",
        ("owner_id", "run_id", "attempt_id", "generation", "candidate_id"),
        "oe_structured_candidates",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_executions",
        ("owner_id", "snapshot_id"),
        "factor_execution_snapshot_outcomes",
        ("owner_id", "publication_id"),
    ),
    (
        "oe_structured_executions",
        ("run_id", "generation", "attempt_id"),
        "execution_attempts",
        ("run_id", "generation", "id"),
    ),
    (
        "oe_structured_iteration_intents",
        ("owner_id", "run_id"),
        "oe_structured_specs",
        ("owner_id", "run_id"),
    ),
    (
        "oe_structured_iteration_intents",
        ("owner_id", "run_id", "attempt_id", "generation", "epoch_id"),
        "oe_structured_epochs",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_iteration_intents",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
        "oe_structured_sampling_intents",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_iteration_intents",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
        "oe_structured_worker_placements",
        ("owner_id", "run_id", "attempt_id", "generation", "sampling_intent_id"),
    ),
    (
        "oe_structured_iteration_intents",
        ("owner_id", "run_id", "attempt_id", "generation", "parent_candidate_id"),
        "oe_structured_candidates",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_iteration_intents",
        ("owner_id", "run_id", "attempt_id", "generation", "sampling_state_id"),
        "oe_structured_control_states",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_iteration_intents",
        ("run_id", "generation", "attempt_id"),
        "execution_attempts",
        ("run_id", "generation", "id"),
    ),
    (
        "oe_structured_provider_exchanges",
        ("owner_id", "request_object_id", "request_kind", "request_sha256"),
        "research_objects",
        ("owner_id", "id", "object_kind", "sha256"),
    ),
    (
        "oe_structured_provider_exchanges",
        ("owner_id", "response_object_id", "response_kind", "response_sha256"),
        "research_objects",
        ("owner_id", "id", "object_kind", "sha256"),
    ),
    (
        "oe_structured_provider_exchanges",
        ("owner_id", "run_id"),
        "oe_structured_specs",
        ("owner_id", "run_id"),
    ),
    (
        "oe_structured_provider_exchanges",
        ("owner_id", "run_id", "attempt_id", "generation", "epoch_id"),
        "oe_structured_epochs",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_provider_exchanges",
        ("owner_id", "run_id", "attempt_id", "generation", "iteration_intent_id"),
        "oe_structured_iteration_intents",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_provider_exchanges",
        ("owner_id", "run_id", "attempt_id", "generation", "parent_candidate_id"),
        "oe_structured_candidates",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_provider_exchanges",
        ("owner_id", "run_id", "attempt_id", "generation", "provider_transport_id"),
        "oe_structured_provider_transports",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_provider_exchanges",
        ("run_id", "generation", "attempt_id"),
        "execution_attempts",
        ("run_id", "generation", "id"),
    ),
    (
        "oe_structured_provider_receipts",
        ("owner_id", "receipt_object_id", "receipt_object_kind", "receipt_sha256"),
        "research_objects",
        ("owner_id", "id", "object_kind", "sha256"),
    ),
    (
        "oe_structured_provider_receipts",
        ("owner_id", "run_id"),
        "oe_structured_specs",
        ("owner_id", "run_id"),
    ),
    (
        "oe_structured_provider_receipts",
        ("owner_id", "run_id", "attempt_id", "generation", "membership_id"),
        "oe_structured_provider_transports",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_provider_receipts",
        ("run_id", "generation", "attempt_id"),
        "execution_attempts",
        ("run_id", "generation", "id"),
    ),
    (
        "oe_structured_provider_transports",
        ("owner_id", "run_id"),
        "oe_structured_specs",
        ("owner_id", "run_id"),
    ),
    (
        "oe_structured_provider_transports",
        ("owner_id", "run_id", "attempt_id", "generation", "iteration_intent_id"),
        "oe_structured_iteration_intents",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_provider_transports",
        ("owner_id", "run_id", "attempt_id", "generation", "operation_id"),
        "research_model_operations",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_provider_transports",
        ("owner_id", "run_id", "attempt_id", "generation", "transport_id"),
        "research_model_transports",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_provider_transports",
        ("run_id", "generation", "attempt_id"),
        "execution_attempts",
        ("run_id", "generation", "id"),
    ),
    (
        "oe_structured_sampling_intents",
        ("owner_id", "run_id"),
        "oe_structured_specs",
        ("owner_id", "run_id"),
    ),
    (
        "oe_structured_sampling_intents",
        ("owner_id", "run_id", "attempt_id", "generation", "epoch_id"),
        "oe_structured_epochs",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_sampling_intents",
        ("owner_id", "run_id", "attempt_id", "generation", "mutation_reservation_id"),
        "research_budget_reservations",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_sampling_intents",
        ("owner_id", "run_id", "attempt_id", "generation", "parent_candidate_id"),
        "oe_structured_candidates",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_sampling_intents",
        ("owner_id", "run_id", "attempt_id", "generation", "sampling_state_id"),
        "oe_structured_control_states",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_sampling_intents",
        ("run_id", "generation", "attempt_id"),
        "execution_attempts",
        ("run_id", "generation", "id"),
    ),
    (
        "oe_structured_specs",
        ("owner_id", "dataset_id"),
        "dataset_versions",
        ("owner_id", "id"),
    ),
    (
        "oe_structured_specs",
        ("owner_id", "run_id"),
        "research_runs",
        ("owner_id", "id"),
    ),
    (
        "oe_structured_specs",
        ("owner_id", "run_id", "submission_id"),
        "oe_structured_submissions",
        ("owner_id", "run_id", "id"),
    ),
    (
        "oe_structured_specs",
        ("owner_id", "seed_program_version_id"),
        "structured_strategy_versions",
        ("owner_id", "id"),
    ),
    (
        "oe_structured_specs",
        ("owner_id", "snapshot_id"),
        "factor_execution_snapshot_outcomes",
        ("owner_id", "publication_id"),
    ),
    (
        "oe_structured_submissions",
        ("owner_id", "run_id"),
        "research_runs",
        ("owner_id", "id"),
    ),
    (
        "oe_structured_terminal_applications",
        ("owner_id", "run_id"),
        "oe_structured_specs",
        ("owner_id", "run_id"),
    ),
    (
        "oe_structured_terminal_applications",
        ("owner_id", "run_id", "attempt_id", "generation", "control_state_id"),
        "oe_structured_control_states",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_terminal_applications",
        ("owner_id", "run_id", "attempt_id", "generation", "epoch_id"),
        "oe_structured_epochs",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_terminal_applications",
        ("owner_id", "run_id", "attempt_id", "generation", "worker_outcome_id"),
        "oe_structured_worker_outcomes",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_terminal_applications",
        ("run_id", "generation", "attempt_id"),
        "execution_attempts",
        ("run_id", "generation", "id"),
    ),
    (
        "oe_structured_worker_outcomes",
        ("owner_id", "run_id"),
        "oe_structured_specs",
        ("owner_id", "run_id"),
    ),
    (
        "oe_structured_worker_outcomes",
        ("owner_id", "run_id", "attempt_id", "generation", "epoch_id"),
        "oe_structured_epochs",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_worker_outcomes",
        (
            "owner_id",
            "run_id",
            "attempt_id",
            "generation",
            "execution_id",
            "candidate_id",
            "engine_outcome_id",
        ),
        "oe_structured_engine_outcomes",
        (
            "owner_id",
            "run_id",
            "attempt_id",
            "generation",
            "execution_id",
            "candidate_id",
            "id",
        ),
    ),
    (
        "oe_structured_worker_outcomes",
        ("owner_id", "run_id", "attempt_id", "generation", "iteration_intent_id"),
        "oe_structured_iteration_intents",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_worker_outcomes",
        ("run_id", "generation", "attempt_id"),
        "execution_attempts",
        ("run_id", "generation", "id"),
    ),
    (
        "oe_structured_worker_placements",
        ("owner_id", "run_id"),
        "oe_structured_specs",
        ("owner_id", "run_id"),
    ),
    (
        "oe_structured_worker_placements",
        ("owner_id", "run_id", "attempt_id", "generation", "sampling_intent_id"),
        "oe_structured_sampling_intents",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_worker_placements",
        ("run_id", "generation", "attempt_id"),
        "execution_attempts",
        ("run_id", "generation", "id"),
    ),
)


def _table_rows(closure, name):
    rows = closure["tables"][name]
    if type(rows) is not list:
        raise ValueError("Exact predecessor table list required")
    return rows


def _by_id(rows):
    result = {r["id"]: r for r in rows}
    if len(result) != len(rows):
        raise ValueError("Duplicate predecessor identity")
    return result


UNIQUE_KEYS = (
    ("oe_structured_candidates", ("epoch_id", "upstream_program_id")),
    (
        "oe_structured_candidates",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    ("oe_structured_candidates", ("provider_exchange_id",)),
    ("oe_structured_candidates", ("run_id", "ordinal")),
    (
        "oe_structured_checkpoint_chunks",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    ("oe_structured_checkpoint_chunks", ("upload_id", "file_name", "offset_bytes")),
    ("oe_structured_checkpoint_chunks", ("upload_id", "sequence")),
    (
        "oe_structured_checkpoint_completions",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    ("oe_structured_checkpoint_completions", ("upload_id",)),
    ("oe_structured_checkpoint_intents", ("epoch_id", "save_ordinal")),
    (
        "oe_structured_checkpoint_intents",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    ("oe_structured_checkpoint_publications", ("completion_id",)),
    ("oe_structured_checkpoint_publications", ("intent_id",)),
    (
        "oe_structured_checkpoint_publications",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    ("oe_structured_checkpoint_uploads", ("intent_id",)),
    (
        "oe_structured_checkpoint_uploads",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    ("oe_structured_checkpoint_uploads", ("reservation_id",)),
    ("oe_structured_control_states", ("epoch_id", "sequence")),
    (
        "oe_structured_control_states",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    ("oe_structured_engine_outcomes", ("admission_id",)),
    ("oe_structured_engine_outcomes", ("candidate_id",)),
    ("oe_structured_engine_outcomes", ("execution_id",)),
    (
        "oe_structured_engine_outcomes",
        (
            "owner_id",
            "run_id",
            "attempt_id",
            "generation",
            "execution_id",
            "candidate_id",
            "id",
        ),
    ),
    (
        "oe_structured_engine_outcomes",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    ("oe_structured_epochs", ("controller_admission_id",)),
    ("oe_structured_epochs", ("owner_id", "run_id", "attempt_id", "generation", "id")),
    ("oe_structured_epochs", ("run_id",)),
    ("oe_structured_executions", ("candidate_id",)),
    (
        "oe_structured_executions",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    ("oe_structured_iteration_intents", ("epoch_id", "ordinal")),
    (
        "oe_structured_iteration_intents",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    ("oe_structured_iteration_intents", ("sampling_state_id",)),
    ("oe_structured_iteration_intents", ("worker_ticket_sha256",)),
    ("oe_structured_provider_exchanges", ("iteration_intent_id",)),
    (
        "oe_structured_provider_exchanges",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    ("oe_structured_provider_exchanges", ("provider_transport_id",)),
    ("oe_structured_provider_receipts", ("membership_id", "kind")),
    (
        "oe_structured_provider_receipts",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    ("oe_structured_provider_transports", ("iteration_intent_id",)),
    ("oe_structured_provider_transports", ("operation_id",)),
    (
        "oe_structured_provider_transports",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    ("oe_structured_provider_transports", ("transport_id",)),
    ("oe_structured_sampling_intents", ("epoch_id", "ordinal")),
    ("oe_structured_sampling_intents", ("mutation_reservation_id",)),
    (
        "oe_structured_sampling_intents",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    ("oe_structured_sampling_intents", ("sampling_state_id",)),
    ("oe_structured_sampling_intents", ("worker_ticket_sha256",)),
    ("oe_structured_specs", ("owner_id", "run_id")),
    ("oe_structured_specs", ("submission_id",)),
    ("oe_structured_submissions", ("owner_id", "idempotency_key")),
    ("oe_structured_submissions", ("owner_id", "run_id", "id")),
    ("oe_structured_submissions", ("run_id",)),
    ("oe_structured_terminal_applications", ("control_state_id",)),
    ("oe_structured_terminal_applications", ("epoch_id", "ordinal")),
    (
        "oe_structured_terminal_applications",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    ("oe_structured_terminal_applications", ("worker_outcome_id",)),
    ("oe_structured_worker_outcomes", ("iteration_intent_id",)),
    (
        "oe_structured_worker_outcomes",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_worker_placements",
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ),
    (
        "oe_structured_worker_placements",
        ("owner_id", "run_id", "attempt_id", "generation", "sampling_intent_id"),
    ),
    ("oe_structured_worker_placements", ("sampling_intent_id",)),
)


def validate_unique_keys(own):
    for table, columns in UNIQUE_KEYS:
        seen = set()
        for row in own[table].values():
            key = tuple(row[column] for column in columns)
            if any(value is None for value in key):
                continue  # PostgreSQL ordinary UNIQUE uses distinct NULLs.
            if key in seen:
                raise ValueError("Normal OE duplicate immutable unique key")
            seen.add(key)


def validate_source_release(owner_id, config, publication, outcome, dataset, exposures):
    """Exact existing snapshot disclosure ledger; never creates authorization."""
    from types import SimpleNamespace

    from backend.app.experiment_services import dataset_identities
    from packages.factors.execution_snapshot import ExecutionSnapshotManifest

    fixed = config.fixed_inputs
    if publication is None or outcome is None or dataset is None:
        raise ValueError("Normal OE retained source release bindings missing")
    manifest = ExecutionSnapshotManifest.model_validate_json(
        canonical_bytes(publication["manifest"])
    )
    if (
        str(manifest.owner_id) != str(owner_id)
        or str(manifest.snapshot_id) != str(fixed.snapshot_id)
        or digest(manifest) != fixed.snapshot_manifest_sha256
        or manifest.input_sha256 != fixed.snapshot_input_sha256
        or manifest.output_rows_sha256 != fixed.snapshot_output_sha256
        or manifest.input.version.factor_version_id != fixed.factor_version_id
        or manifest.input.dataset.dataset_version_id != fixed.dataset_version_id
        or manifest.input.dataset.content_sha256 != fixed.dataset_sha256
        or publication["owner_id"] != str(owner_id)
        or publication["run_id"] != str(manifest.run_id)
        or outcome["publication_id"] != publication["id"]
        or outcome["owner_id"] != publication["owner_id"]
        or outcome["run_id"] != publication["run_id"]
        or (
            outcome["manifest_sha256"],
            outcome["input_sha256"],
            outcome["output_sha256"],
        )
        != (
            fixed.snapshot_manifest_sha256,
            fixed.snapshot_input_sha256,
            fixed.snapshot_output_sha256,
        )
        or dataset["id"] != str(fixed.dataset_version_id)
        or dataset["owner_id"] != str(owner_id)
        or dataset["content_sha256"] != fixed.dataset_sha256
    ):
        raise ValueError("Normal OE released snapshot commitment differs")
    expected_key = f"engine-result:execution_snapshot:{fixed.snapshot_id}:factor:v1"
    matches = [
        row
        for row in exposures
        if row["owner_id"] == str(owner_id) and row["source_key"] == expected_key
    ]
    if len(matches) != 1 or tuple(str(x) for x in fixed.release_receipt_ids) != (
        matches[0]["id"],
    ):
        raise ValueError("Normal OE actual source release inventory differs")
    row = matches[0]
    provenance = {
        "run_id": publication["run_id"],
        "execution_snapshot_id": publication["id"],
        "manifest_sha256": fixed.snapshot_manifest_sha256,
        "input_sha256": fixed.snapshot_input_sha256,
        "output_sha256": fixed.snapshot_output_sha256,
        "extent": "conservative_full_retained_dataset",
        "qualification": "historical_disclosure_not_live_execution_authority",
        "role": "factor",
        "dataset_version_id": dataset["id"],
        "dataset_content_sha256": fixed.dataset_sha256,
    }
    if (
        row["run_id"] != publication["run_id"]
        or row["dataset_version_id"] != dataset["id"]
        or row["purpose"] != "engine_execution_snapshot_release"
        or row["provenance"] != provenance
        or (row["start_date"], row["end_date"])
        != (dataset["start_date"], dataset["end_date"])
        or row["identities"]
        != dataset_identities(
            SimpleNamespace(manifest=dataset["manifest"], market=dataset["market"])
        )
    ):
        raise ValueError("Normal OE existing disclosure evidence differs")


def validate_provider_inventory(configs, memberships, shared):
    """Every normal operation and transport has exactly one protocol membership."""
    operations = {
        r["id"]
        for r in shared["research_model_operations"].values()
        if r["run_id"] in configs
    }
    transports = {
        r["id"]
        for r in shared["research_model_transports"].values()
        if r["run_id"] in configs or r["operation_id"] in operations
    }
    if (
        operations != {m["operation_id"] for m in memberships.values()}
        or transports != {m["transport_id"] for m in memberships.values()}
        or len(operations) != len(memberships)
        or len(transports) != len(memberships)
    ):
        raise ValueError("Normal OE operation/transport/member reverse inventory")


def validate_closure(
    closure,
    *,
    research_closure,
    execution_closure,
    direct_analysis_closure,
    structured_closure,
    inventory,
    read_bytes,
    maximum_rows=100000,
    workflow_context=None,
):
    """After predecessor validators, close actual027 graph and retained bytes."""

    from backend.app.oe_structured_config_manifest import expected_config_manifest
    from backend.app.oe_structured_epoch_services import HOOK_MANIFEST_SHA256
    from packages.openevolve_structured.candidate import (
        CandidateDocument,
        project_candidate,
    )
    from packages.openevolve_structured.provider_bootstrap import (
        ProviderStructuredBootstrap,
    )
    from packages.openevolve_structured.provider_response import parse_response
    from runtimes.openevolve_structured.profile import profile_sha256

    if research_closure.get("schema_version") not in {
        "mineagent.research-restore-closure.v16",
        RESEARCH_SCHEMA,
        "mineagent.research-restore-closure.v14",
        "mineagent.research-restore-closure.v15",
    } or not callable(read_bytes):
        raise ValueError("Exact research-v11 and actual bounded bytes required")
    successor = (
        research_closure.get("schema_version")
        == "mineagent.research-restore-closure.v16"
    )
    if not successor and workflow_context is not None:
        raise ValueError(
            "Historical normal closure cannot acquire workflow OE authority"
        )
    own = indexes(closure, maximum_rows, schema=SCHEMA_V2 if successor else SCHEMA)
    from packages.openevolve_structured.workflow_bootstrap import (
        parse_provider_bootstrap,
    )
    from packages.openevolve_structured.workflow_host_binding import (
        require_bootstrap_context,
        runtime_profile_for,
    )
    from packages.openevolve_structured.workflow_run import WorkflowStructuredRunConfig

    validate_unique_keys(own)
    physical = {r["path"]: r for r in inventory}
    if len(physical) != len(inventory):
        raise ValueError("Duplicate actual physical inventory path")
    shared = {
        name: _by_id(rows)
        for name, rows in research_closure["tables"].items()
        if all("id" in r for r in rows)
    }
    # Projection states use admission identity rather than their optional row id.
    shared["research_process_states"] = {
        r["admission_id"]: r
        for r in research_closure["tables"]["research_process_states"]
    }
    if len(shared["research_process_states"]) != len(
        research_closure["tables"]["research_process_states"]
    ):
        raise ValueError("Duplicate process projection")
    runs = _by_id(research_closure["external_bindings"]["runs"])
    versions = _by_id(_table_rows(structured_closure, "structured_strategy_versions"))
    datasets = _by_id(_table_rows(direct_analysis_closure, "dataset_versions"))
    publications = _by_id(
        _table_rows(execution_closure, "factor_execution_snapshot_publications")
    )
    configs = validate_declarations(
        own, runs, versions, workflow_context=workflow_context
    )
    declared_upload = research_closure["schema_version"] in {
        "mineagent.research-restore-closure.v16",
        "mineagent.research-restore-closure.v15",
    }
    if not declared_upload and any(
        c.fixed_inputs.schema_version != "mineagent.oe-structured-retained-inputs.v1"
        for c in configs.values()
    ):
        raise ValueError("Historical closure cannot admit declared upload inputs")
    snapshot_outcomes = {
        r["publication_id"]: r
        for r in _table_rows(execution_closure, "factor_execution_snapshot_outcomes")
    }
    exposures = _table_rows(direct_analysis_closure, "data_usage_exposures")
    for run_id, config in configs.items():
        validate_source_release(
            own["research_runs"][run_id]["owner_id"],
            config,
            publications.get(str(config.fixed_inputs.snapshot_id)),
            snapshot_outcomes.get(str(config.fixed_inputs.snapshot_id)),
            datasets.get(str(config.fixed_inputs.dataset_version_id)),
            exposures,
        )
    foreign = {
        **{k: list(v.values()) for k, v in own.items()},
        **research_closure["tables"],
        "research_runs": list(runs.values()),
        "execution_attempts": research_closure["external_bindings"]["attempts"],
        "structured_strategy_versions": list(versions.values()),
        "dataset_versions": list(datasets.values()),
        "factor_execution_snapshot_outcomes": _table_rows(
            execution_closure, "factor_execution_snapshot_outcomes"
        ),
    }
    reference_indexes = {}
    for source, columns, target, target_columns in REFERENCES:
        key = (target, target_columns)
        if key not in reference_indexes:
            values = [tuple(r[k] for k in target_columns) for r in foreign[target]]
            if len(set(values)) != len(values):
                raise ValueError("Ambiguous normal OE referenced identity")
            reference_indexes[key] = set(values)
        for row in own[source].values():
            values = tuple(row[k] for k in columns)
            if None not in values and values not in reference_indexes[key]:
                raise ValueError("Normal OE retained scoped reference missing")
    attempts = _by_id(research_closure["external_bindings"]["attempts"])
    for table, rows in own.items():
        if table == "research_runs":
            continue
        for row in rows.values():
            if (
                row["run_id"] not in configs
                or row["owner_id"] != own["research_runs"][row["run_id"]]["owner_id"]
            ):
                raise ValueError("Orphan normal OE owned row")
            if "attempt_id" in row:
                attempt = attempts.get(row["attempt_id"])
                if (
                    attempt is None
                    or (
                        attempt["owner_id"],
                        attempt["run_id"],
                        attempt["id"],
                        attempt["generation"],
                    )
                    != (
                        row["owner_id"],
                        row["run_id"],
                        row["attempt_id"],
                        row["generation"],
                    )
                    or row["generation"] != 1
                ):
                    raise ValueError("Normal OE initial-attempt scope differs")
    responses = {}
    for exchange in own["oe_structured_provider_exchanges"].values():
        responses[exchange["id"]] = validate_provider_exchange(
            exchange, own, shared, configs[exchange["run_id"]].model_binding, read_bytes
        )
    memberships = own["oe_structured_provider_transports"]
    validate_provider_inventory(configs, memberships, shared)
    completed_members = {
        e["provider_transport_id"]
        for e in own["oe_structured_provider_exchanges"].values()
    }
    if len(completed_members) != len(own["oe_structured_provider_exchanges"]):
        raise ValueError("Normal OE duplicate completed provider exchange")
    for member in memberships.values():
        if member["id"] not in completed_members:
            validate_pending_provider(
                member, own, shared, configs[member["run_id"]].model_binding, read_bytes
            )
    measurements = {}
    for candidate in own["oe_structured_candidates"].values():
        config = configs[candidate["run_id"]]
        projected = project_candidate(
            candidate["candidate_canonical"].encode("ascii"), config.search.domain
        )
        version = versions[candidate["program_version_id"]]
        if (
            type(candidate["ordinal"]) is not int
            or not 0 <= candidate["ordinal"] <= config.search.maximum_mutation_slots
            or projected.candidate_sha256 != candidate["candidate_sha256"]
            or projected.program_sha256 != candidate["program_sha256"]
            or version["owner_id"] != candidate["owner_id"]
            or version["program_canonical"].encode() != projected.program_bytes
            or version["program"] != projected.program.model_dump(mode="json")
            or version["program_sha256"] != projected.program_sha256
        ):
            raise ValueError("Normal OE immutable candidate/version differs")
        if candidate["ordinal"] == 0:
            if (
                candidate["origin"]
                != (
                    "workflow_seed"
                    if type(config) is WorkflowStructuredRunConfig
                    else "authenticated_user_seed"
                )
                or candidate["parent_candidate_id"] is not None
                or candidate["provider_exchange_id"] is not None
                or candidate["program_version_id"]
                != own["oe_structured_specs"][candidate["run_id"]][
                    "seed_program_version_id"
                ]
                or candidate["candidate_canonical"].encode()
                != canonical_bytes(
                    CandidateDocument(program=config.search.domain.seed_program)
                )
            ):
                raise ValueError("Normal OE actual authenticated seed differs")
        else:
            parent = own["oe_structured_candidates"].get(
                candidate["parent_candidate_id"]
            )
            exchange = own["oe_structured_provider_exchanges"].get(
                candidate["provider_exchange_id"]
            )
            if (
                candidate["origin"] != "model_proposal"
                or not _same(candidate, parent)
                or not _same(candidate, exchange)
                or parent["epoch_id"] != candidate["epoch_id"]
                or exchange["epoch_id"] != candidate["epoch_id"]
                or not 0 <= parent["ordinal"] < candidate["ordinal"]
                or exchange["parent_candidate_id"] != parent["id"]
                or version["parent_version_id"] != parent["program_version_id"]
            ):
                raise ValueError("Normal OE actual candidate ancestry differs")
            request, response = responses[exchange["id"]]
            parsed = parse_response(
                request=request,
                response=response,
                binding=config.model_binding,
                domain=config.search.domain,
            )
            if (
                parsed.candidate_bytes != candidate["candidate_canonical"].encode()
                or request.parent_candidate_sha256 != parent["candidate_sha256"]
            ):
                raise ValueError(
                    "Normal OE candidate is not original completed response"
                )
    execution_by_candidate = {}
    for execution in own["oe_structured_executions"].values():
        if execution["candidate_id"] in execution_by_candidate:
            raise ValueError("Normal OE candidate reexecution forbidden")
        execution_by_candidate[execution["candidate_id"]] = execution
    for outcome in own["oe_structured_engine_outcomes"].values():
        if outcome["candidate_id"] in measurements:
            raise ValueError("Normal OE duplicate engine outcome")
        execution = execution_by_candidate[outcome["candidate_id"]]
        candidate = own["oe_structured_candidates"][outcome["candidate_id"]]
        config = configs[outcome["run_id"]]
        measurements[candidate["id"]] = validate_engine_outcome(
            outcome,
            execution,
            candidate,
            config,
            publications[str(config.fixed_inputs.snapshot_id)],
            datasets[str(config.fixed_inputs.dataset_version_id)],
            shared,
            physical,
            read_bytes,
            declared_upload=declared_upload,
        )

    # Prepared executions remain immutable declarations even if no process ever
    # launched. Reconstruct them too; absence of an outcome is not permission to
    # restore arbitrary input bytes under an otherwise valid candidate.
    def retained_input(key, maximum):
        record = physical.get(key)
        if (
            record is None
            or type(record["size_bytes"]) is not int
            or not 0 < record["size_bytes"] <= maximum
        ):
            raise ValueError("Normal OE pending execution input missing/oversized")
        raw = read_bytes(key, record["sha256"], record["size_bytes"])
        if (
            type(raw) is not bytes
            or len(raw) != record["size_bytes"]
            or hashlib.sha256(raw).hexdigest() != record["sha256"]
        ):
            raise ValueError("Normal OE pending execution original bytes differ")
        return raw

    for candidate_id, execution in execution_by_candidate.items():
        if candidate_id not in measurements:
            config = configs[execution["run_id"]]
            reconstruct_execution(
                execution,
                own["oe_structured_candidates"][candidate_id],
                config,
                publications[str(config.fixed_inputs.snapshot_id)],
                datasets[str(config.fixed_inputs.dataset_version_id)],
                retained_input,
                declared_upload=declared_upload,
            )
    epoch_results = {}
    for epoch in own["oe_structured_epochs"].values():
        config = configs[epoch["run_id"]]
        bootstrap = (
            parse_provider_bootstrap(epoch["bootstrap_canonical"].encode())
            if successor
            else ProviderStructuredBootstrap.model_validate_json(
                epoch["bootstrap_canonical"]
            )
        )
        require_bootstrap_context(config, bootstrap)
        seed = canonical_bytes(
            CandidateDocument(program=config.search.domain.seed_program)
        )
        if (
            canonical_bytes(bootstrap).decode() != epoch["bootstrap_canonical"]
            or digest(bootstrap) != epoch["bootstrap_sha256"]
            or str(bootstrap.epoch_id) != epoch["id"]
            or str(bootstrap.admission_id) != epoch["controller_admission_id"]
            or bootstrap.spec != config.search
            or bootstrap.model_binding != config.model_binding
            or bootstrap.model_binding_sha256 != config.model_binding.sha256
            or bootstrap.adapter_patch_sha256 != HOOK_MANIFEST_SHA256
            or config.runtime.controller_inventory_sha256 != HOOK_MANIFEST_SHA256
            or bootstrap.physical_profile_sha256 != profile_sha256()
            or bootstrap.runtime_profile_sha256 != runtime_profile_for(config)
            or bootstrap.config_sha256
            != digest(
                expected_config_manifest(config.search, seed, config.model_binding)
            )
            or hashlib.sha256(seed).hexdigest() != config.search.seed_candidate_sha256
        ):
            raise ValueError("Normal OE exact retained controller bootstrap differs")
        admission = shared["research_process_admissions"][
            epoch["controller_admission_id"]
        ]
        if (
            not _same(epoch, admission)
            or admission["logical_operation_id"] != epoch["id"]
            or admission["image_id"] != config.runtime.controller_image_id
        ):
            raise ValueError("Normal OE epoch/controller binding differs")

        def check(key, sha, size):
            raw = read_bytes(key, sha, size)
            if (
                type(raw) is not bytes
                or len(raw) != size
                or hashlib.sha256(raw).hexdigest() != sha
            ):
                raise ValueError("Normal OE exact original controller bytes")

        records = validate_controller_closure_unit(
            admission, shared, physical, check, read_bytes, set(), set()
        )
        intent = records["intent"]
        if (
            intent.bootstrap_sha256,
            intent.bootstrap_size_bytes,
            intent.seed_sha256,
            intent.seed_size_bytes,
        ) != (
            epoch["bootstrap_sha256"],
            len(epoch["bootstrap_canonical"].encode()),
            hashlib.sha256(seed).hexdigest(),
            len(seed),
        ):
            raise ValueError("Normal OE controller original mounted input differs")
        validate_worker_placements(epoch, own, records)
        states = validate_epoch_graph(
            epoch, config, own, shared, measurements, read_bytes
        )
        epoch_results[epoch["id"]] = (records, states)
    expected_controllers = {
        e["controller_admission_id"] for e in own["oe_structured_epochs"].values()
    }
    actual_controllers = {
        a["id"]
        for a in shared["research_process_admissions"].values()
        if a["run_id"] in configs
        and a["profile"] == "mineagent.oe-structured-orchestrator.v1"
    }
    if expected_controllers != actual_controllers:
        raise ValueError("Normal OE controller/epoch reverse inventory")
    for admission in shared["research_process_admissions"].values():
        if (
            admission["run_id"] in configs
            and admission["id"] not in expected_controllers
        ):
            execution = own["oe_structured_executions"].get(
                admission["logical_operation_id"]
            )
            if admission[
                "profile"
            ] != "mineagent.factor-strategy-engine.v1" or not _same(
                admission, execution
            ):
                raise ValueError("Normal OE orphan/foreign process admission")
    for run_id, config in configs.items():
        epochs = [
            e for e in own["oe_structured_epochs"].values() if e["run_id"] == run_id
        ]
        if len(epochs) > 1:
            raise ValueError("Normal OE multiple epochs forbidden")
        if own["research_runs"][run_id]["status"] == "succeeded":
            if len(epochs) != 1:
                raise ValueError("Successful normal OE has no original epoch")
            records, states = epoch_results[epochs[0]["id"]]
            if (
                records["completion"] is None
                or not states
                or len(states) != 2 * config.search.maximum_mutation_slots + 1
            ):
                raise ValueError(
                    "Successful normal OE requires actual completion and full frontier"
                )
            candidates = {
                r["id"]
                for r in own["oe_structured_candidates"].values()
                if r["run_id"] == run_id
            }
            outcomes = [
                r
                for r in own["oe_structured_engine_outcomes"].values()
                if r["run_id"] == run_id
            ]
            admissions = {
                r["id"]
                for r in shared["research_process_admissions"].values()
                if r["run_id"] == run_id
            }
            if (
                candidates != {r["candidate_id"] for r in outcomes}
                or candidates
                != {
                    r["candidate_id"]
                    for r in own["oe_structured_executions"].values()
                    if r["run_id"] == run_id
                }
                or admissions
                != {epochs[0]["controller_admission_id"]}
                | {r["admission_id"] for r in outcomes}
            ):
                raise ValueError(
                    "Successful normal OE has unevaluated/orphan execution inventory"
                )
            for held in shared["research_budget_reservations"].values():
                if held["run_id"] == run_id:
                    _full_charge(held, shared, held["id"])
    return {"configs": configs, "measurements": measurements, "epochs": epoch_results}


def validate_epoch_graph(epoch, config, own, shared, measurements, read_bytes):
    """Historical finite control transitions against actual completed measurements."""
    from types import SimpleNamespace

    from backend.app.oe_structured_population import (
        _selection,
        _state,
        measurement_feedback,
    )
    from backend.app.oe_structured_terminal_services import (
        _event,
        _transition,
        _worker_document,
    )
    from packages.openevolve_structured.checkpoint import _json
    from packages.openevolve_structured.checkpoint import canonical as native_canonical
    from packages.openevolve_structured.contracts import SearchOperationContext
    from packages.openevolve_structured.provider_contracts import RenderedPrompt
    from packages.openevolve_structured.provider_response import build_request

    def rows(name):
        return [r for r in own[name].values() if r.get("epoch_id") == epoch["id"]]

    domain = config.search.domain
    cursors = sorted(rows("oe_structured_control_states"), key=lambda r: r["sequence"])
    if len(cursors) > 2 * config.search.maximum_mutation_slots + 1:
        raise ValueError("Normal OE finite state bound")
    states, previous = {}, None
    candidates = rows("oe_structured_candidates")
    native = {r["upstream_program_id"]: r for r in candidates}
    if len(native) != len(candidates) or len({r["ordinal"] for r in candidates}) != len(
        candidates
    ):
        raise ValueError("Normal OE unique native candidate/ordinal")
    for seq, row in enumerate(cursors, 1):
        phase = "seed" if seq == 1 else "sampling" if seq % 2 == 0 else "applied"
        if (
            row["sequence"] != seq
            or row["phase"] != phase
            or row["frontier"] != (seq - 1) // 2
            or row["previous_id"] != (None if previous is None else previous["id"])
        ):
            raise ValueError("Normal OE immutable contiguous state chain")
        state = _state(SimpleNamespace(**row), domain)
        states[row["id"]] = state
        if phase == "sampling" and (
            state["programs"] != states[previous["id"]]["programs"]
            or state["metadata"]["last_iteration"] != previous["frontier"]
        ):
            raise ValueError("Normal OE sampling changed completed programs")
        for pid, program in state["programs"].items():
            candidate = native.get(pid)
            if candidate is None or candidate["ordinal"] > row["frontier"]:
                raise ValueError(
                    "Normal OE population contains unbound/future candidate"
                )
            measurement = measurements.get(candidate["id"])
            parent = own["oe_structured_candidates"].get(
                candidate["parent_candidate_id"]
            )
            if (
                measurement is None
                or str(measurement.structured_strategy_version_id)
                != candidate["program_version_id"]
                or program["code"] != candidate["candidate_canonical"]
                or program["iteration_found"] != candidate["ordinal"]
                or program["parent_id"]
                != (None if parent is None else parent["upstream_program_id"])
                or native_canonical(program["metrics"])
                != native_canonical(
                    {"combined_score": measurement.combined_score}
                    if measurement.eligible
                    else {}
                )
                or program["artifacts_json"] is None
                or _json(program["artifacts_json"])
                != {"structured_feedback": measurement_feedback(measurement)}
            ):
                raise ValueError(
                    "Normal OE population score/artifact differs from actual engine result"
                )
        previous = row
    samples = rows("oe_structured_sampling_intents")
    if len({r["ordinal"] for r in samples}) != len(samples) or len(
        {r["sampling_state_id"] for r in samples}
    ) != len(samples):
        raise ValueError("Normal OE sampling repeated")
    for sample in samples:
        cursor = own["oe_structured_control_states"][sample["sampling_state_id"]]
        before = own["oe_structured_control_states"].get(cursor["previous_id"])
        parent = own["oe_structured_candidates"][sample["parent_candidate_id"]]
        if (
            cursor["phase"] != "sampling"
            or cursor["sequence"] != 2 * sample["ordinal"]
            or before is None
            or cursor["frontier"] != sample["ordinal"] - 1
            or parent["epoch_id"] != epoch["id"]
        ):
            raise ValueError("Normal OE sampling predecessor differs")
        _selection(
            SimpleNamespace(**sample), states[before["id"]], SimpleNamespace(**parent)
        )
        held = shared["research_budget_reservations"][sample["mutation_reservation_id"]]
        if (
            not _same(sample, held)
            or held["purpose"] != "engine_mutation"
            or held["logical_key"]
            != f"oe-structured-mutation:{epoch['id']}:{sample['ordinal']}"
            or BudgetVector.model_validate(held["amounts"])
            != BudgetVector(mutation_slots=1)
        ):
            raise ValueError("Normal OE exact mutation reservation")
        if held["state"] == "settled":
            _full_charge(sample, shared, held["id"])
        elif held["state"] not in {"reserved", "uncertain"}:
            raise ValueError("Normal OE mutation not retained")
    if {s["sampling_state_id"] for s in samples} != {
        c["id"] for c in cursors if c["phase"] == "sampling"
    }:
        raise ValueError("Normal OE sampling/cursor reverse inventory")
    for intent in rows("oe_structured_iteration_intents"):
        sample = own["oe_structured_sampling_intents"].get(intent["id"])
        if sample is None or any(
            intent[k] != sample[k]
            for k in (
                "sampling_state_id",
                "parent_candidate_id",
                "ordinal",
                "selection_canonical",
                "selection_sha256",
                "worker_ticket_sha256",
            )
        ):
            raise ValueError("Normal OE final rendering not tied to original sampling")
        placements = [
            p
            for p in own["oe_structured_worker_placements"].values()
            if p["sampling_intent_id"] == sample["id"]
        ]
        if len(placements) != 1 or not _same(intent, placements[0]):
            raise ValueError("Normal OE actual worker placement missing")
        placement = placements[0]
        if any(
            intent[k] != placement[k]
            for k in ("worker_identity_canonical", "worker_identity_sha256")
        ):
            raise ValueError("Normal OE rendered worker identity differs")
        parent = own["oe_structured_candidates"][intent["parent_candidate_id"]]
        rendered = RenderedPrompt.model_validate_json(intent["rendered_canonical"])
        if (
            canonical_bytes(rendered).decode() != intent["rendered_canonical"]
            or digest(rendered) != intent["rendered_sha256"]
            or rendered.current_candidate_sha256 != parent["candidate_sha256"]
        ):
            raise ValueError("Normal OE original rendered prompt differs")
        selected = _json(sample["selection_canonical"])["snapshot"]["programs"]
        for ref in rendered.references:
            program = selected.get(str(ref.upstream_program_id))
            if (
                program is None
                or hashlib.sha256(program["code"].encode()).hexdigest()
                != ref.candidate_sha256
            ):
                raise ValueError(
                    "Normal OE rendered reference outside actual population"
                )
        from packages.openevolve_structured.workflow_run import (
            WorkflowStructuredRunConfig,
        )

        if type(config) is WorkflowStructuredRunConfig:
            from packages.openevolve_structured.workflow_prompt import (
                validate_workflow_rendered,
            )

            validate_workflow_rendered(
                config.workflow_context, rendered.system, rendered.user
            )
        context = SearchOperationContext(
            owner_id=UUID(epoch["owner_id"]),
            run_id=UUID(epoch["run_id"]),
            epoch_id=UUID(epoch["id"]),
            iteration_intent_id=UUID(intent["id"]),
            native_iteration=intent["ordinal"],
            parent_candidate_id=UUID(parent["id"]),
            parent_candidate_sha256=parent["candidate_sha256"],
            execution_binding_sha256=config.search.execution_binding_sha256,
            selection_snapshot_sha256=sample["selection_sha256"],
            rendered_context_sha256=digest(rendered),
        )
        if (
            canonical_bytes(context).decode() != intent["context_canonical"]
            or digest(context) != intent["context_sha256"]
        ):
            raise ValueError("Normal OE original context reconstruction differs")
        expected_request = build_request(
            binding=config.model_binding,
            context=context,
            domain=domain,
            parent_candidate=parent["candidate_canonical"].encode(),
            rendered=rendered,
        )
        memberships = [
            m
            for m in own["oe_structured_provider_transports"].values()
            if m["iteration_intent_id"] == intent["id"]
        ]
        if len(memberships) > 1:
            raise ValueError("Normal OE SDK retry forbidden")
        for member in memberships:
            operation = shared["research_model_operations"][member["operation_id"]]
            if (
                operation["request"] != expected_request.model_dump(mode="json")
                or member["request_sha256"] != expected_request.sha256
            ):
                raise ValueError("Normal OE retained request not reconstructed prompt")
    terminals = rows("oe_structured_terminal_applications")
    if len({t["ordinal"] for t in terminals}) != len(terminals) or len(
        {t["control_state_id"] for t in terminals}
    ) != len(terminals):
        raise ValueError("Normal OE repeated terminal application")
    for worker in rows("oe_structured_worker_outcomes"):
        doc = _worker_document(SimpleNamespace(**worker), domain)
        intent = own["oe_structured_iteration_intents"][worker["iteration_intent_id"]]
        if (
            not _same(worker, intent)
            or intent["epoch_id"] != epoch["id"]
            or doc["projection"]["native_iteration"] != intent["ordinal"]
        ):
            raise ValueError("Normal OE original worker evidence scope/iteration")
        if worker["status"] == "evaluated":
            candidate = own["oe_structured_candidates"][worker["candidate_id"]]
            engine = own["oe_structured_engine_outcomes"][worker["engine_outcome_id"]]
            measurement = measurements.get(candidate["id"])
            if (
                not _same(worker, engine)
                or not _same(worker, candidate)
                or measurement is None
                or not measurement.eligible
                or engine["candidate_id"] != candidate["id"]
                or engine["execution_id"] != worker["execution_id"]
                or candidate["epoch_id"] != epoch["id"]
                or candidate["ordinal"] != intent["ordinal"]
                or candidate["parent_candidate_id"] != intent["parent_candidate_id"]
                or doc["observation"]["child"]["id"] != candidate["upstream_program_id"]
                or doc["observation"]["child"]["code"]
                != candidate["candidate_canonical"]
                or doc["observation"]["child"]["metrics"]
                != {"combined_score": measurement.combined_score}
            ):
                raise ValueError("Normal OE pending evaluated worker result differs")
    for terminal in terminals:
        cursor = own["oe_structured_control_states"][terminal["control_state_id"]]
        event = _event(
            terminal["event_canonical"].encode(),
            {"iteration", "outcome", "native_state", "state_sha256"},
        )
        ordinal = terminal["ordinal"]
        if (
            hashlib.sha256(terminal["event_canonical"].encode()).hexdigest()
            != terminal["event_sha256"]
            or event["iteration"] != ordinal
            or event["outcome"] != terminal["outcome"]
            or event["state_sha256"] != cursor["state_sha256"]
            or native_canonical(event["native_state"])
            != cursor["state_canonical"].encode()
            or cursor["sequence"] != 2 * ordinal + 1
            or cursor["phase"] != ("seed" if ordinal == 0 else "applied")
        ):
            raise ValueError("Normal OE exact terminal event/cursor differs")
        child = (
            next((c for c in candidates if c["ordinal"] == 0), None)
            if ordinal == 0
            else None
        )
        before = None if ordinal == 0 else states[cursor["previous_id"]]
        if ordinal == 0:
            if terminal["worker_outcome_id"] is not None or child is None:
                raise ValueError("Normal OE seed terminal identity")
        else:
            worker = own["oe_structured_worker_outcomes"].get(
                terminal["worker_outcome_id"]
            )
            if not _same(terminal, worker):
                raise ValueError("Normal OE terminal worker evidence missing")
            doc = _worker_document(SimpleNamespace(**worker), domain)
            intent = own["oe_structured_iteration_intents"][
                worker["iteration_intent_id"]
            ]
            sample = own["oe_structured_sampling_intents"][intent["id"]]
            if (
                intent["ordinal"] != ordinal
                or intent["sampling_state_id"] != cursor["previous_id"]
                or (worker["status"] == "worker_error")
                != (terminal["outcome"] == "error")
            ):
                raise ValueError("Normal OE actual worker terminal status differs")
            _full_charge(sample, shared, sample["mutation_reservation_id"])
            if worker["status"] == "evaluated":
                child = own["oe_structured_candidates"].get(worker["candidate_id"])
                measurement = measurements.get(worker["candidate_id"])
                engine = own["oe_structured_engine_outcomes"].get(
                    worker["engine_outcome_id"]
                )
                if (
                    child is None
                    or measurement is None
                    or engine is None
                    or not measurement.eligible
                    or engine["execution_id"] != worker["execution_id"]
                    or engine["candidate_id"] != child["id"]
                    or child["ordinal"] != ordinal
                    or child["parent_candidate_id"] != intent["parent_candidate_id"]
                    or doc["observation"]["child"]["id"] != child["upstream_program_id"]
                    or doc["observation"]["child"]["code"]
                    != child["candidate_canonical"]
                    or doc["observation"]["child"]["metrics"]
                    != {"combined_score": measurement.combined_score}
                ):
                    raise ValueError(
                        "Normal OE evaluated worker lacks its actual eligible result"
                    )
        _transition(
            before,
            states[cursor["id"]],
            ordinal,
            terminal["outcome"],
            None if child is None else child["upstream_program_id"],
        )
        if child is not None and (
            child["id"] not in measurements or not measurements[child["id"]].eligible
        ):
            raise ValueError("Normal OE seed/child has no eligible actual result")
    if {t["control_state_id"] for t in terminals} != {
        r["id"] for r in cursors if r["phase"] in {"seed", "applied"}
    }:
        raise ValueError("Normal OE terminal/cursor reverse inventory")
    checkpoint_intents = sorted(
        rows("oe_structured_checkpoint_intents"), key=lambda r: r["save_ordinal"]
    )
    if [r["save_ordinal"] for r in checkpoint_intents] != list(
        range(1, len(checkpoint_intents) + 1)
    ):
        raise ValueError("Normal OE checkpoint save gap")
    for intent in checkpoint_intents:
        cursor = own["oe_structured_control_states"][intent["control_state_id"]]
        if (
            not _same(intent, cursor)
            or cursor["epoch_id"] != epoch["id"]
            or cursor["phase"] not in {"seed", "applied"}
            or intent["state_sha256"] != cursor["state_sha256"]
            or intent["frontier"] != cursor["frontier"]
        ):
            raise ValueError("Normal OE original checkpoint intent differs")
    publications = rows("oe_structured_checkpoint_publications")
    published_uploads = {
        own["oe_structured_checkpoint_completions"][p["completion_id"]]["upload_id"]
        for p in publications
    }
    for upload in rows("oe_structured_checkpoint_uploads"):
        if upload["id"] not in published_uploads:
            validate_checkpoint_prefix(upload, own, shared, read_bytes)
    published_completions = {p["completion_id"] for p in publications}
    for completion in rows("oe_structured_checkpoint_completions"):
        if completion["id"] not in published_completions:
            validate_checkpoint_completion(completion, own, shared, domain, read_bytes)
    for publication in publications:
        validate_checkpoint_publication(publication, own, shared, domain, read_bytes)
    if own["research_runs"][epoch["run_id"]]["status"] == "succeeded":
        maximum = config.search.maximum_mutation_slots
        if (
            len(checkpoint_intents) != maximum + 2
            or len(publications) != len(checkpoint_intents)
            or {p["intent_id"] for p in publications}
            != {i["id"] for i in checkpoint_intents}
            or checkpoint_intents[-1]["control_state_id"] != cursors[-1]["id"]
            or checkpoint_intents[-1]["frontier"] != maximum
            or len(rows("oe_structured_checkpoint_uploads")) != maximum + 2
            or len(rows("oe_structured_checkpoint_completions")) != maximum + 2
        ):
            raise ValueError(
                "Normal OE final success requires every published save and actual frontier"
            )
    return states


def validate_pending_provider(membership, own, shared, binding, read_bytes):
    """Keep incomplete original SDK attempts as held evidence, never completion."""
    from packages.openevolve_structured.provider_accounting import request_reservation
    from packages.openevolve_structured.provider_contracts import OEChatRequest
    from packages.openevolve_structured.provider_receipts import (
        OBJECT_KIND,
        parse_receipt,
        validate_chain,
    )

    operation = shared["research_model_operations"][membership["operation_id"]]
    transport = shared["research_model_transports"][membership["transport_id"]]
    if (
        not _same(membership, operation)
        or not _same(membership, transport)
        or operation["protocol"] != "mineagent.oe-structured-provider-chat.v1"
        or operation["purpose"] != "chat"
        or operation["ordinal"] != 0
        or transport["ordinal"] != 1
        or transport["operation_id"] != operation["id"]
        or transport["state"] == "completed"
        or operation["state"] == "completed"
        or membership["request_sha256"] != operation["request_sha256"]
        or membership["model_binding_sha256"] != binding.sha256
    ):
        raise ValueError("Normal OE pending original transport identity")
    raw = _registered_read(
        shared,
        read_bytes,
        membership["owner_id"],
        operation["request_object_id"],
        operation["request_object_kind"],
        operation["request_sha256"],
    )
    request = OEChatRequest.model_validate_json(raw)
    if (
        canonical_bytes(request) != raw
        or request.model_dump(mode="json") != operation["request"]
    ):
        raise ValueError("Normal OE pending original request bytes")
    held = shared["research_budget_reservations"][transport["reservation_id"]]
    if (
        not _same(membership, held)
        or held["purpose"] != "model_transport"
        or held["logical_key"]
        != "oe-structured-provider:" + membership["iteration_intent_id"]
        or BudgetVector.model_validate(held["amounts"])
        != request_reservation(request, binding)
    ):
        raise ValueError("Normal OE pending original full hold")
    rows = [
        r
        for r in own["oe_structured_provider_receipts"].values()
        if r["membership_id"] == membership["id"]
    ]
    kinds = {r["kind"] for r in rows}
    if len(kinds) != len(rows) or kinds not in (
        set(),
        {"intent"},
        {"intent", "started"},
        {"intent", "started", "stopped"},
    ):
        raise ValueError("Normal OE pending SDK receipt prefix")
    documents = {}
    for row in rows:
        if not _same(membership, row) or row["receipt_object_kind"] != OBJECT_KIND:
            raise ValueError("Normal OE pending SDK scope")
        raw = _registered_read(
            shared,
            read_bytes,
            membership["owner_id"],
            row["receipt_object_id"],
            OBJECT_KIND,
            row["receipt_sha256"],
        )
        doc = parse_receipt(raw)
        if (
            doc.kind != row["kind"]
            or len(raw) != row["size_bytes"]
            or str(doc.transport_id) != transport["id"]
            or doc.request_sha256 != request.sha256
            or doc.model_binding_sha256 != binding.sha256
            or any(str(getattr(doc, k)) != str(membership[k]) for k in SCOPE)
        ):
            raise ValueError("Normal OE pending SDK retained identity")
        documents[doc.kind] = doc
    if (
        "intent" in documents
        and documents["intent"].maximum_duration_ms != binding.request_deadline_ms
    ):
        raise ValueError("Normal OE pending SDK duration")
    if "started" in documents:
        intent, started = documents["intent"], documents["started"]
        if (
            started.identity.boot_id != intent.boot_id
            or started.identity.deadline_monotonic_ns != intent.deadline_monotonic_ns
        ):
            raise ValueError("Normal OE pending SDK original identity/deadline")
    if "stopped" in documents:
        validate_chain(
            documents["intent"],
            documents["started"],
            documents["stopped"],
            completed=False,
        )
    if held["state"] == "settled":
        if "stopped" not in documents:
            raise ValueError("Uncontained SDK cannot release original hold")
        _full_charge(membership, shared, held["id"])
    elif held["state"] not in {"reserved", "uncertain"} or any(
        r["reservation_id"] == held["id"]
        for r in shared["research_budget_settlements"].values()
    ):
        raise ValueError("Normal OE pending SDK hold must remain reserved")
    return documents


def validate_worker_placements(epoch, own, records):
    """Compare retained observations to original controller records, no Peer."""
    from dataclasses import asdict

    from pydantic import TypeAdapter

    from packages.openevolve_structured.host_events import WorkerPlacementObservation
    from runtimes.openevolve_structured_host.peer import (
        ControllerBinding,
        WorkerBinding,
    )
    from runtimes.rdagent_operational.peer import KernelIdentity

    samples = {
        s["id"]: s
        for s in own["oe_structured_sampling_intents"].values()
        if s["epoch_id"] == epoch["id"]
    }
    placements = [
        p
        for p in own["oe_structured_worker_placements"].values()
        if p["sampling_intent_id"] in samples
    ]
    if len({p["sampling_intent_id"] for p in placements}) != len(placements):
        raise ValueError("Normal OE worker placement repeated")
    if not placements:
        return
    identity, child = records["identity"], records["child"]
    if identity is None or child is None:
        raise ValueError("Normal OE worker lacks original controller child evidence")
    # These are immutable identity value objects, not an authenticated socket
    # Peer. No current PID lookup, ticket disclosure, or launch grant is made.
    controller = ControllerBinding(
        identity.container_id,
        identity.admission_sha256,
        identity.leader,
        child.controller,
    )
    for row in placements:
        sample = samples[row["sampling_intent_id"]]
        observation = WorkerPlacementObservation.model_validate_json(
            row["placement_canonical"]
        )
        worker = TypeAdapter(KernelIdentity).validate_json(
            row["worker_identity_canonical"]
        )
        WorkerBinding(
            controller,
            UUID(epoch["id"]),
            UUID(sample["id"]),
            sample["worker_ticket_sha256"],
            worker,
        )
        if (
            not _same(epoch, row)
            or canonical_bytes(observation).decode() != row["placement_canonical"]
            or digest(observation) != row["placement_sha256"]
            or canonical_bytes(asdict(worker)).decode()
            != row["worker_identity_canonical"]
            or digest(asdict(worker)) != row["worker_identity_sha256"]
            or digest(asdict(controller)) != row["controller_identity_sha256"]
            or str(observation.epoch_id) != epoch["id"]
            or str(observation.intent_id) != sample["id"]
            or observation.controller_namespace_pid != child.namespace_pid
            or observation.worker_start_ticks != worker.start_ticks
        ):
            raise ValueError("Normal OE original observed placement differs")
