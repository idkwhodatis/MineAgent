"""Actual bounded initial-epoch upload: reserve, retain chunks, assemble, commit ACK.

The complete additive assembly hold precedes any payload. Each retained object is
independently pre-admitted/journaled by the existing publisher. Interrupted holds
are never refunded here. No filesystem path comes from controller input.
"""

import base64
import hashlib
from uuid import UUID, uuid4

from sqlalchemy import select

from backend.app.openevolve_controller_services import require_search_controller
from backend.app.openevolve_models import SearchCheckpointIntent
from backend.app.openevolve_object_publication import publish_search_object
from backend.app.openevolve_operational_models import (
    SearchCheckpointChunk,
    SearchCheckpointUpload,
    SearchCheckpointUploadCompletion,
)
from backend.app.openevolve_services import _latest_control, _owned_row, _scope
from backend.app.research_broker_services import reserve_budget
from backend.app.research_lifecycle import neutral_transaction
from backend.app.research_object_services import read_research_object
from packages.openevolve_adapter.checkpoint_native import canonical
from packages.openevolve_adapter.host_contracts import (
    CheckpointFileEnvelope,
    CheckpointFileRef,
    CheckpointInventory,
    EvidenceRef,
)
from packages.openevolve_adapter.upload_contracts import (
    Begin,
    Chunk,
    ChunkDocument,
    parse,
)
from packages.research_runtime.budget_contracts import BudgetVector


def _upload(session, fence, peer, epoch_id, token):
    require_search_controller(session, fence, peer, epoch_id=epoch_id)
    intent = _owned_row(session, SearchCheckpointIntent, token, fence)
    if intent.epoch_id != epoch_id:
        raise ValueError("checkpoint intent epoch")
    upload = session.scalar(
        select(SearchCheckpointUpload).filter_by(checkpoint_intent_id=token)
    )
    if upload is not None and any(
        getattr(upload, k) != v for k, v in _scope(fence).items()
    ):
        raise ValueError("checkpoint upload scope")
    return intent, upload


def _current(session, epoch_id, intent):
    control = _latest_control(session, epoch_id)
    if control is None or control.id != intent.control_transition_id:
        raise ValueError("checkpoint upload frontier changed")


def begin_upload(factory, fence, peer, *, epoch_id, payload, settings):
    begin = parse(Begin, payload)
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        intent, previous = _upload(session, fence, peer, epoch_id, begin.token)
        value = begin.model_dump(mode="json")
        if previous is not None:
            if previous.inventory != value:
                raise ValueError("checkpoint begin replay drift")
            return {"token": str(begin.token), "reserved": True}
        _current(session, epoch_id, intent)
        # One immutable hold covers incoming chunk buffers and final assembly.
        # Publication objects/journals consume additional reservations, never this
        # one. Global family quota checks are mandatory before accepting payload.
        from backend.app.openevolve_upload_quota import require_upload_capacity

        require_upload_capacity(session, fence, begin, settings)
        reservation = reserve_budget(
            session,
            fence,
            purpose="object_storage",
            logical_key=f"oe-upload:{begin.token}",
            amounts=BudgetVector(storage_bytes=begin.transfer_peak_bytes),
        )
        row = SearchCheckpointUpload(
            id=uuid4(),
            **_scope(fence),
            epoch_id=epoch_id,
            checkpoint_intent_id=begin.token,
            reservation_id=reservation.id,
            inventory=value,
            inventory_sha256=hashlib.sha256(canonical(value)).hexdigest(),
            total_bytes=begin.total_bytes,
            file_count=len(begin.files),
            chunk_count=len(begin.chunks),
        )
        session.add(row)
        session.flush()
    return {"token": str(begin.token), "reserved": True}


def _rows(session, upload_id):
    return session.scalars(
        select(SearchCheckpointChunk)
        .filter_by(upload_id=upload_id)
        .order_by(SearchCheckpointChunk.sequence)
    ).all()


def _chunk_match(row, chunk):
    return (
        row.sequence,
        row.file_name,
        row.offset_bytes,
        row.size_bytes,
        row.chunk_sha256,
    ) == (chunk.sequence, chunk.name, chunk.offset, len(chunk.bytes()), chunk.sha256)


def accept_chunk(factory, fence, peer, *, epoch_id, payload, settings):
    chunk = parse(Chunk, payload)
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        intent, upload = _upload(session, fence, peer, epoch_id, chunk.token)
        if upload is None:
            raise ValueError("checkpoint upload was not reserved")
        begin = parse(Begin, upload.inventory)
        chunk.require_inventory(begin)
        rows = _rows(session, upload.id)
        if chunk.sequence < len(rows):
            row = rows[chunk.sequence]
            if not _chunk_match(row, chunk):
                raise ValueError("checkpoint chunk replay drift")
            raw = read_research_object(
                session,
                fence,
                row.object_id,
                row.object_kind,
                row.object_sha256,
                settings,
            )
            expected = ChunkDocument(
                **_scope(fence), epoch_id=epoch_id, upload_id=upload.id, chunk=chunk
            )
            if raw != canonical(expected.model_dump(mode="json")):
                raise ValueError("checkpoint retained replay differs")
            return {
                "token": str(chunk.token),
                "sequence": chunk.sequence,
                "sha256": chunk.sha256,
            }
        if (
            chunk.sequence != len(rows)
            or session.scalar(
                select(SearchCheckpointUploadCompletion).filter_by(upload_id=upload.id)
            )
            is not None
        ):
            raise ValueError("checkpoint chunk out of order or closed")
        _current(session, epoch_id, intent)
        upload_id = upload.id
        document = ChunkDocument(
            **_scope(fence), epoch_id=epoch_id, upload_id=upload_id, chunk=chunk
        )
    raw = canonical(document.model_dump(mode="json"))
    obj = publish_search_object(
        factory,
        fence,
        kind="search_checkpoint_chunk",
        content=raw,
        logical_key=f"oe-chunk:{upload_id}:{chunk.sequence}",
        settings=settings,
    )
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        intent, upload = _upload(session, fence, peer, epoch_id, chunk.token)
        rows = _rows(session, upload.id)
        if chunk.sequence < len(rows):
            row = rows[chunk.sequence]
            if not _chunk_match(row, chunk) or (row.object_id, row.object_sha256) != (
                obj.id,
                obj.sha256,
            ):
                raise ValueError("checkpoint concurrent chunk drift")
        else:
            _current(session, epoch_id, intent)
            if chunk.sequence != len(rows):
                raise ValueError("checkpoint concurrent gap")
            session.add(
                SearchCheckpointChunk(
                    id=uuid4(),
                    **_scope(fence),
                    epoch_id=epoch_id,
                    upload_id=upload.id,
                    sequence=chunk.sequence,
                    file_name=chunk.name,
                    offset_bytes=chunk.offset,
                    size_bytes=len(chunk.bytes()),
                    chunk_sha256=chunk.sha256,
                    object_id=obj.id,
                    object_kind=obj.object_kind,
                    object_sha256=obj.sha256,
                )
            )
            session.flush()
    return {
        "token": str(chunk.token),
        "sequence": chunk.sequence,
        "sha256": chunk.sha256,
    }


def finish_upload(factory, fence, peer, *, epoch_id, payload, settings):
    if (
        type(payload) is not dict
        or set(payload) != {"token", "chunks"}
        or type(payload["chunks"]) is not int
    ):
        raise ValueError("closed checkpoint finish")
    token = UUID(payload["token"])
    if str(token) != payload["token"]:
        raise ValueError("canonical checkpoint token")
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        intent, upload = _upload(session, fence, peer, epoch_id, token)
        if upload is None or payload["chunks"] != upload.chunk_count:
            raise ValueError("checkpoint incomplete declaration")
        previous = session.scalar(
            select(SearchCheckpointUploadCompletion).filter_by(upload_id=upload.id)
        )
        if previous is not None:
            read_research_object(
                session,
                fence,
                previous.inventory_object_id,
                previous.inventory_kind,
                previous.inventory_sha256,
                settings,
            )
            return {"token": str(token), "receipt_id": str(previous.id)}
        _current(session, epoch_id, intent)
        begin = parse(Begin, upload.inventory)
        rows = _rows(session, upload.id)
        if len(rows) != len(begin.chunks):
            raise ValueError("checkpoint missing chunks")
        assembled = {file.name: bytearray() for file in begin.files}
        for sequence, row in enumerate(rows):
            raw = read_research_object(
                session,
                fence,
                row.object_id,
                row.object_kind,
                row.object_sha256,
                settings,
            )
            document = ChunkDocument.model_validate_json(raw)
            if (
                canonical(document.model_dump(mode="json")) != raw
                or any(getattr(document, k) != v for k, v in _scope(fence).items())
                or (document.epoch_id, document.upload_id) != (epoch_id, upload.id)
            ):
                raise ValueError("retained checkpoint chunk scope")
            chunk = document.chunk
            chunk.require_inventory(begin)
            if sequence != chunk.sequence or not _chunk_match(row, chunk):
                raise ValueError("retained checkpoint chunk order")
            assembled[chunk.name].extend(chunk.bytes())
        upload_id = upload.id
    references = []
    for file in begin.files:
        raw = bytes(assembled[file.name])
        if (
            len(raw) != file.size_bytes
            or hashlib.sha256(raw).hexdigest() != file.sha256
        ):
            raise ValueError("assembled checkpoint file digest")
        envelope = CheckpointFileEnvelope(
            name=file.name,
            sha256=file.sha256,
            size_bytes=len(raw),
            data_base64=base64.b64encode(raw).decode("ascii"),
        )
        obj = publish_search_object(
            factory,
            fence,
            kind="search_checkpoint_file",
            content=canonical(envelope.model_dump(mode="json")),
            logical_key=f"oe-file:{upload_id}:{len(references)}",
            settings=settings,
        )
        references.append(
            CheckpointFileRef(
                name=file.name,
                file_sha256=file.sha256,
                size_bytes=len(raw),
                object=EvidenceRef(id=obj.id, sha256=obj.sha256),
            )
        )
    inventory = CheckpointInventory(files=tuple(references))
    obj = publish_search_object(
        factory,
        fence,
        kind="search_checkpoint_inventory",
        content=canonical(inventory.model_dump(mode="json")),
        logical_key=f"oe-inventory:{upload_id}",
        settings=settings,
    )
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        intent, upload = _upload(session, fence, peer, epoch_id, token)
        previous = session.scalar(
            select(SearchCheckpointUploadCompletion).filter_by(upload_id=upload.id)
        )
        if previous is None:
            _current(session, epoch_id, intent)
            previous = SearchCheckpointUploadCompletion(
                id=uuid4(),
                **_scope(fence),
                epoch_id=epoch_id,
                upload_id=upload.id,
                inventory_object_id=obj.id,
                inventory_kind=obj.object_kind,
                inventory_sha256=obj.sha256,
            )
            session.add(previous)
            session.flush()
        elif (previous.inventory_object_id, previous.inventory_sha256) != (
            obj.id,
            obj.sha256,
        ):
            raise ValueError("checkpoint completion replay drift")
        receipt = previous.id
    # This acknowledges retained upload only. Native semantic checkpoint closure
    # is subsequently validated by publish_checkpoint_snapshot, never inferred.
    return {"token": str(token), "receipt_id": str(receipt)}
