"""Exact retained checkpoint transfer, including incomplete charged uploads."""

import hashlib

from packages.openevolve_adapter.checkpoint_native import canonical
from packages.openevolve_adapter.host_contracts import (
    CheckpointFileEnvelope,
    CheckpointInventory,
)
from packages.openevolve_adapter.upload_contracts import Begin, ChunkDocument

SCOPE = ("owner_id", "run_id", "attempt_id", "generation")


def validate_uploads(tables, research, read_object):
    """read_object(owner,id,kind,sha) must return verified registered raw bytes."""
    reservations = {
        r["id"]: r for r in research["tables"]["research_budget_reservations"]
    }
    intents = {r["id"]: r for r in tables["search_checkpoint_intents"]}
    completions = {
        r["upload_id"]: r for r in tables["search_checkpoint_upload_completions"]
    }
    if len(completions) != len(tables["search_checkpoint_upload_completions"]):
        raise ValueError("Duplicate upload completion")
    consumed = set()
    for upload in tables["search_checkpoint_uploads"]:
        begin = Begin.model_validate_json(canonical(upload["inventory"]))
        intent = intents.get(upload["checkpoint_intent_id"])
        if (
            canonical(begin.model_dump(mode="json")) != canonical(upload["inventory"])
            or hashlib.sha256(canonical(upload["inventory"])).hexdigest()
            != upload["inventory_sha256"]
            or str(begin.token) != upload["checkpoint_intent_id"]
            or intent is None
            or intent["epoch_id"] != upload["epoch_id"]
            or any(intent[k] != upload[k] for k in SCOPE)
            or (upload["total_bytes"], upload["file_count"], upload["chunk_count"])
            != (begin.total_bytes, len(begin.files), len(begin.chunks))
        ):
            raise ValueError("Checkpoint upload immutable inventory differs")
        budget = reservations.get(upload["reservation_id"])
        if (
            budget is None
            or budget["id"] in consumed
            or any(budget[k] != upload[k] for k in SCOPE)
            or budget["purpose"] != "object_storage"
            or budget["logical_key"] != "oe-upload:" + str(begin.token)
            or budget["amounts"]
            != {
                "calls": 0,
                "cost_microunits": 0,
                "tokens": 0,
                "wall_milliseconds": 0,
                "storage_bytes": begin.transfer_peak_bytes,
                "mutation_slots": 0,
                "evaluator_executions": 0,
                "process_slots": 0,
            }
        ):
            raise ValueError("Checkpoint upload has no exact additive capacity hold")
        consumed.add(budget["id"])
        rows = sorted(
            (
                r
                for r in tables["search_checkpoint_chunks"]
                if r["upload_id"] == upload["id"]
            ),
            key=lambda r: r["sequence"],
        )
        if len(rows) > len(begin.chunks):
            raise ValueError("Checkpoint has excess chunks")
        assembled = {item.name: bytearray() for item in begin.files}
        for ordinal, row in enumerate(rows):
            if row["sequence"] != ordinal or any(
                row[k] != upload[k] for k in (*SCOPE, "epoch_id")
            ):
                raise ValueError("Checkpoint chunks are not a contiguous scoped prefix")
            raw = read_object(
                row["owner_id"],
                row["object_id"],
                "search_checkpoint_chunk",
                row["object_sha256"],
            )
            document = ChunkDocument.model_validate_json(raw)
            chunk = document.chunk
            chunk.require_inventory(begin)
            if (
                canonical(document.model_dump(mode="json")) != raw
                or any(
                    str(getattr(document, k)) != str(upload[k])
                    for k in (*SCOPE, "epoch_id")
                )
                or str(document.upload_id) != upload["id"]
                or (
                    chunk.sequence,
                    chunk.name,
                    chunk.offset,
                    len(chunk.bytes()),
                    chunk.sha256,
                )
                != (
                    row["sequence"],
                    row["file_name"],
                    row["offset_bytes"],
                    row["size_bytes"],
                    row["chunk_sha256"],
                )
            ):
                raise ValueError("Retained checkpoint chunk differs")
            assembled[chunk.name].extend(chunk.bytes())
        complete = completions.get(upload["id"])
        if complete is None:
            if budget["state"] not in {"reserved", "uncertain"}:
                raise ValueError("Incomplete checkpoint upload cannot release hold")
            continue
        if len(rows) != len(begin.chunks) or any(
            complete[k] != upload[k] for k in (*SCOPE, "epoch_id")
        ):
            raise ValueError("Completed upload omits retained chunks")
        raw = read_object(
            upload["owner_id"],
            complete["inventory_object_id"],
            "search_checkpoint_inventory",
            complete["inventory_sha256"],
        )
        inventory = CheckpointInventory.model_validate_json(raw)
        if canonical(inventory.model_dump(mode="json")) != raw or {
            f.name for f in inventory.files
        } != set(assembled):
            raise ValueError("Completed upload file inventory differs")
        declared = {f.name: f for f in begin.files}
        for file in inventory.files:
            data = bytes(assembled[file.name])
            original = declared[file.name]
            if (
                len(data) != original.size_bytes
                or hashlib.sha256(data).hexdigest() != original.sha256
                or (file.file_sha256, file.size_bytes)
                != (original.sha256, original.size_bytes)
            ):
                raise ValueError("Assembled checkpoint native bytes differ")
            raw = read_object(
                upload["owner_id"],
                str(file.object.id),
                "search_checkpoint_file",
                file.object.sha256,
            )
            envelope = CheckpointFileEnvelope.model_validate_json(raw)
            if (
                canonical(envelope.model_dump(mode="json")) != raw
                or envelope.name != file.name
                or envelope.bytes() != data
            ):
                raise ValueError(
                    "Completed checkpoint envelope differs from exact transfer"
                )
    if set(completions) - {r["id"] for r in tables["search_checkpoint_uploads"]}:
        raise ValueError("Upload completion has no admitted transfer")
