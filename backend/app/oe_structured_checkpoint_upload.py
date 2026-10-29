"""027 charged retained checkpoint transfer; committed exact ACKs, no refunds.

Original native files stay opaque in envelopes. Every phase reauthenticates the
actual controller and its original deadline under fresh neutral transactions.
Caller retains native storage/lifecycle guard across this whole bounded callback.
"""

import base64
import hashlib
from uuid import UUID, uuid4

from sqlalchemy import select

from backend.app.oe_structured_checkpoint_services import (
    controller,
    latest_control,
    retained_files,
    scope,
)
from backend.app.oe_structured_controller_context import fail, scoped
from backend.app.research_broker_services import reserve_budget
from backend.app.research_lifecycle import neutral_transaction
from backend.app.research_object_models import (
    ResearchObject,
    ResearchObjectOutcome,
    ResearchObjectPublication,
)
from backend.app.research_object_services import (
    publish_reserved_object,
    read_research_object,
    reserve_object,
)
from packages.openevolve_structured.checkpoint import canonical
from packages.openevolve_structured.checkpoint_object_identity import parse_document
from packages.openevolve_structured.upload_contracts import (
    Begin,
    Chunk,
    ChunkDocument,
    FileEnvelope,
    FileRef,
    Inventory,
    parse,
)
from packages.research_runtime.budget_contracts import BudgetVector


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _upload(session, fence, epoch_id, token, settings, peer):
    from backend.app.oe_structured_checkpoint_models import (
        OEStructuredCheckpointIntent,
        OEStructuredCheckpointUpload,
    )

    controller(session, fence, epoch_id, settings, peer)
    intent = scoped(session.get(OEStructuredCheckpointIntent, token), fence)
    if intent.epoch_id != epoch_id:
        fail()
    upload = session.scalar(
        select(OEStructuredCheckpointUpload).filter_by(intent_id=token)
    )
    if upload is not None:
        scoped(upload, fence)
        if upload.epoch_id != epoch_id:
            fail()
        require_upload_hold(session, fence, upload)
    return intent, upload


def _current(session, intent):
    cursor = latest_control(session, intent.epoch_id)
    if cursor is None or cursor.id != intent.control_state_id:
        fail()


def _begin(upload):
    raw = upload.inventory_canonical.encode("utf-8")
    begin = Begin.model_validate_json(raw)
    if (
        canonical(begin.model_dump(mode="json")) != raw
        or _digest(raw) != upload.inventory_sha256
        or (begin.token, begin.total_bytes, len(begin.files), len(begin.chunks))
        != (upload.intent_id, upload.total_bytes, upload.file_count, upload.chunk_count)
    ):
        fail()
    return begin


def require_upload_hold(session, fence, upload):
    from backend.app.research_broker_models import (
        ResearchBudgetReservation,
        ResearchBudgetSettlement,
    )

    held = scoped(session.get(ResearchBudgetReservation, upload.reservation_id), fence)
    begin = _begin(upload)
    if (
        held.purpose != "object_storage"
        or held.logical_key != f"oe-structured-upload:{upload.intent_id}"
        or held.state not in {"reserved", "uncertain"}
        or BudgetVector.model_validate(held.amounts)
        != BudgetVector(storage_bytes=begin.transfer_peak_bytes)
        or session.get(ResearchBudgetSettlement, held.id) is not None
    ):
        fail()
    return held


def upload_begin(factory, fence, epoch_id, payload, settings, *, peer):
    from backend.app.oe_structured_checkpoint_models import OEStructuredCheckpointUpload
    from backend.app.openevolve_upload_quota import require_upload_capacity

    begin = parse(Begin, payload)
    raw = canonical(begin.model_dump(mode="json"))
    with neutral_transaction(
        factory, fence, resource_scope="factor_numeric"
    ) as session:
        intent, previous = _upload(
            session, fence, epoch_id, begin.token, settings, peer
        )
        if previous is not None:
            if _begin(previous) != begin:
                fail()
        else:
            _current(session, intent)
            # Shared physical family checker must include027 pending holds in
            # every writer before activation; assembler owns that dispatch.
            require_upload_capacity(session, fence, begin, settings)
            held = reserve_budget(
                session,
                fence,
                purpose="object_storage",
                logical_key=f"oe-structured-upload:{begin.token}",
                amounts=BudgetVector(storage_bytes=begin.transfer_peak_bytes),
                settings=settings,
            )
            session.add(
                OEStructuredCheckpointUpload(
                    id=uuid4(),
                    **scope(fence),
                    epoch_id=epoch_id,
                    intent_id=intent.id,
                    reservation_id=held.id,
                    inventory_canonical=raw.decode(),
                    inventory_sha256=_digest(raw),
                    total_bytes=begin.total_bytes,
                    file_count=len(begin.files),
                    chunk_count=len(begin.chunks),
                )
            )
            session.flush()
    return {"token": str(begin.token), "reserved": True}


def _rows(session, upload_id):
    from backend.app.oe_structured_checkpoint_models import OEStructuredCheckpointChunk

    rows = list(
        session.scalars(
            select(OEStructuredCheckpointChunk)
            .filter_by(upload_id=upload_id)
            .order_by(OEStructuredCheckpointChunk.sequence)
            .limit(89)
        )
    )
    if len(rows) > 88 or any(row.sequence != index for index, row in enumerate(rows)):
        fail()
    return rows


def _match(row, chunk):
    return (
        row.sequence,
        row.file_name,
        row.offset_bytes,
        row.size_bytes,
        row.chunk_sha256,
    ) == (chunk.sequence, chunk.name, chunk.offset, len(chunk.bytes()), chunk.sha256)


def _publish(factory, fence, epoch_id, token, kind, raw, key, settings, peer):
    parse_document(kind, raw)
    sha = _digest(raw)
    with neutral_transaction(
        factory, fence, resource_scope="factor_numeric"
    ) as session:
        intent, upload = _upload(session, fence, epoch_id, token, settings, peer)
        if upload is None:
            fail()
        _current(session, intent)
        held = reserve_budget(
            session,
            fence,
            purpose="object_storage",
            logical_key=key,
            amounts=BudgetVector(storage_bytes=2 * len(raw) + 8192),
            settings=settings,
        )
        previous = session.scalar(
            select(ResearchObjectPublication).filter_by(budget_reservation_id=held.id)
        )
        if previous is not None:
            scoped(previous, fence)
            if (previous.object_kind, previous.sha256, previous.size_bytes) != (
                kind,
                sha,
                len(raw),
            ):
                fail()
            outcome = session.get(ResearchObjectOutcome, previous.id)
            if outcome is not None:
                if (
                    read_research_object(
                        session, fence, outcome.object_id, kind, sha, settings
                    )
                    != raw
                ):
                    fail()
                return outcome.object_id
        publication = reserve_object(session, fence, kind, raw, held.id, settings)
        publication_id = publication.id
    object_id = publish_reserved_object(factory, fence, publication_id, raw, settings)
    with neutral_transaction(
        factory, fence, resource_scope="factor_numeric"
    ) as session:
        _upload(session, fence, epoch_id, token, settings, peer)
        obj = session.get(ResearchObject, object_id)
        if (
            obj is None
            or (obj.owner_id, obj.object_kind, obj.sha256, obj.size_bytes)
            != (fence.owner_id, kind, sha, len(raw))
            or read_research_object(session, fence, object_id, kind, sha, settings)
            != raw
        ):
            fail()
    return object_id


def _read_chunk(session, fence, upload, row, begin, settings):
    scoped(row, fence)
    if (
        row.epoch_id != upload.epoch_id
        or row.upload_id != upload.id
        or row.object_kind != "oe_structured_checkpoint_chunk"
    ):
        fail()
    raw = read_research_object(
        session, fence, row.object_id, row.object_kind, row.object_sha256, settings
    )
    document = parse_document(row.object_kind, raw)
    if any(getattr(document, key) != getattr(fence, key) for key in scope(fence)) or (
        document.epoch_id,
        document.upload_id,
    ) != (upload.epoch_id, upload.id):
        fail()
    document.chunk.require_inventory(begin)
    if not _match(row, document.chunk):
        fail()
    return document.chunk


def upload_chunk(factory, fence, epoch_id, payload, settings, *, peer):
    from backend.app.oe_structured_checkpoint_models import OEStructuredCheckpointChunk

    chunk = parse(Chunk, payload)
    with neutral_transaction(
        factory, fence, resource_scope="factor_numeric"
    ) as session:
        intent, upload = _upload(session, fence, epoch_id, chunk.token, settings, peer)
        if upload is None:
            fail()
        begin = _begin(upload)
        chunk.require_inventory(begin)
        rows = _rows(session, upload.id)
        if chunk.sequence < len(rows):
            if (
                _read_chunk(
                    session, fence, upload, rows[chunk.sequence], begin, settings
                )
                != chunk
            ):
                fail()
            controller(session, fence, epoch_id, settings, peer)
            return {
                "token": str(chunk.token),
                "sequence": chunk.sequence,
                "sha256": chunk.sha256,
            }
        _current(session, intent)
        if chunk.sequence != len(rows):
            fail()
        upload_id = upload.id
    raw = canonical(
        ChunkDocument(
            **scope(fence), epoch_id=epoch_id, upload_id=upload_id, chunk=chunk
        ).model_dump(mode="json")
    )
    kind = "oe_structured_checkpoint_chunk"
    obj = _publish(
        factory,
        fence,
        epoch_id,
        chunk.token,
        kind,
        raw,
        f"oe-structured-chunk:{upload_id}:{chunk.sequence}",
        settings,
        peer,
    )
    with neutral_transaction(
        factory, fence, resource_scope="factor_numeric"
    ) as session:
        intent, upload = _upload(session, fence, epoch_id, chunk.token, settings, peer)
        rows = _rows(session, upload.id)
        if chunk.sequence < len(rows):
            if (
                _read_chunk(
                    session, fence, upload, rows[chunk.sequence], begin, settings
                )
                != chunk
                or rows[chunk.sequence].object_id != obj
            ):
                fail()
        else:
            _current(session, intent)
            if chunk.sequence != len(rows):
                fail()
            session.add(
                OEStructuredCheckpointChunk(
                    id=uuid4(),
                    **scope(fence),
                    epoch_id=epoch_id,
                    upload_id=upload.id,
                    sequence=chunk.sequence,
                    file_name=chunk.name,
                    offset_bytes=chunk.offset,
                    size_bytes=len(chunk.bytes()),
                    chunk_sha256=chunk.sha256,
                    object_id=obj,
                    object_kind=kind,
                    object_sha256=_digest(raw),
                )
            )
            session.flush()
    return {
        "token": str(chunk.token),
        "sequence": chunk.sequence,
        "sha256": chunk.sha256,
    }


def upload_finish(factory, fence, epoch_id, payload, settings, *, peer):
    from backend.app.oe_structured_checkpoint_models import (
        OEStructuredCheckpointCompletion,
    )

    if (
        type(payload) is not dict
        or set(payload) != {"token", "chunks"}
        or type(payload["chunks"]) is not int
    ):
        fail()
    token = UUID(payload["token"])
    if str(token) != payload["token"]:
        fail()
    with neutral_transaction(
        factory, fence, resource_scope="factor_numeric"
    ) as session:
        intent, upload = _upload(session, fence, epoch_id, token, settings, peer)
        if upload is None or payload["chunks"] != upload.chunk_count:
            fail()
        previous = session.scalar(
            select(OEStructuredCheckpointCompletion).filter_by(upload_id=upload.id)
        )
        if previous is not None:
            scoped(previous, fence)
            retained_files(session, fence, intent, previous, settings)
            controller(session, fence, epoch_id, settings, peer)
            return {"token": str(token), "receipt_id": str(previous.id)}
        _current(session, intent)
        begin = _begin(upload)
        rows = _rows(session, upload.id)
        if len(rows) != len(begin.chunks):
            fail()
        assembled = {file.name: bytearray() for file in begin.files}
        for index, row in enumerate(rows):
            chunk = _read_chunk(session, fence, upload, row, begin, settings)
            if chunk.sequence != index:
                fail()
            assembled[chunk.name].extend(chunk.bytes())
        upload_id = upload.id
    refs = []
    for index, file in enumerate(begin.files):
        raw = bytes(assembled.pop(file.name))
        if len(raw) != file.size_bytes or _digest(raw) != file.sha256:
            fail()
        envelope = FileEnvelope(
            name=file.name,
            sha256=file.sha256,
            size_bytes=len(raw),
            data_base64=base64.b64encode(raw).decode("ascii"),
        )
        encoded = canonical(envelope.model_dump(mode="json"))
        oid = _publish(
            factory,
            fence,
            epoch_id,
            token,
            "oe_structured_checkpoint_file",
            encoded,
            f"oe-structured-file:{upload_id}:{index}",
            settings,
            peer,
        )
        refs.append(
            FileRef(
                name=file.name,
                size_bytes=file.size_bytes,
                sha256=file.sha256,
                object_id=oid,
                object_sha256=_digest(encoded),
            )
        )
    inventory = Inventory(
        **scope(fence),
        epoch_id=epoch_id,
        upload_id=upload_id,
        token=token,
        files=tuple(refs),
    )
    raw = canonical(inventory.model_dump(mode="json"))
    oid = _publish(
        factory,
        fence,
        epoch_id,
        token,
        "oe_structured_checkpoint_inventory",
        raw,
        f"oe-structured-inventory:{upload_id}",
        settings,
        peer,
    )
    with neutral_transaction(
        factory, fence, resource_scope="factor_numeric"
    ) as session:
        intent, upload = _upload(session, fence, epoch_id, token, settings, peer)
        previous = session.scalar(
            select(OEStructuredCheckpointCompletion).filter_by(upload_id=upload.id)
        )
        if previous is None:
            _current(session, intent)
            previous = OEStructuredCheckpointCompletion(
                id=uuid4(),
                **scope(fence),
                epoch_id=epoch_id,
                upload_id=upload_id,
                inventory_object_id=oid,
                inventory_object_kind="oe_structured_checkpoint_inventory",
                inventory_object_sha256=_digest(raw),
            )
            session.add(previous)
            session.flush()
        else:
            scoped(previous, fence)
            if (
                previous.epoch_id,
                previous.inventory_object_id,
                previous.inventory_object_kind,
                previous.inventory_object_sha256,
            ) != (epoch_id, oid, "oe_structured_checkpoint_inventory", _digest(raw)):
                fail()
        receipt_id = previous.id
    # Upload completion is only retained bytes, not checkpoint semantic authority.
    return {"token": str(token), "receipt_id": str(receipt_id)}
