"""Normal027 native JSON checkpoint intents and actual retained publications.

Flush-only control mutations; caller commits before any ACK. A checkpoint does
not imply whole-controller stop, run success, continuation or release authority.
"""

import hashlib
import math
from uuid import UUID, uuid4

from sqlalchemy import select

from backend.app.oe_structured_controller_context import (
    SCOPE,
    fail,
    require_controller_context,
    scoped,
)
from backend.app.oe_structured_operational_models import OEStructuredControlState
from backend.app.oe_structured_population import _state
from backend.app.oe_structured_terminal_services import (
    _event,
    register_terminal_application,
)
from backend.app.research_object_services import read_research_object
from packages.openevolve_structured.checkpoint import (
    _json,
    _normalize,
    canonical,
    state_sha256,
)
from packages.openevolve_structured.upload_contracts import FileEnvelope, Inventory
from runtimes.openevolve_structured.checkpoint_transfer import (
    FIXED,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_TOTAL_BYTES,
    _name,
)
from runtimes.openevolve_structured_host.peer import ControllerBinding


def scope(fence):
    return {key: getattr(fence, key) for key in SCOPE}


def latest_control(session, epoch_id):
    return session.scalar(
        select(OEStructuredControlState)
        .filter_by(epoch_id=epoch_id)
        .order_by(OEStructuredControlState.sequence.desc())
        .limit(1)
    )


def controller(session, fence, epoch_id, settings, peer):
    context = require_controller_context(session, fence, epoch_id, settings, peer=peer)
    if type(peer.binding) is not ControllerBinding:
        fail()
    return context


def current_state(session, fence, epoch_id, context, settings, peer):
    """Re-run exact actual terminal/result checks, never a caller population score."""
    from backend.app.oe_structured_terminal_models import (
        OEStructuredTerminalApplication,
    )

    cursor = scoped(latest_control(session, epoch_id), fence)
    if cursor.epoch_id != epoch_id or cursor.phase not in {"seed", "applied"}:
        fail()
    terminal = scoped(
        session.scalar(
            select(OEStructuredTerminalApplication).filter_by(
                control_state_id=cursor.id
            )
        ),
        fence,
    )
    if terminal.epoch_id != epoch_id or terminal.ordinal != cursor.frontier:
        fail()
    raw = terminal.event_canonical.encode("utf-8")
    if hashlib.sha256(raw).hexdigest() != terminal.event_sha256:
        fail()
    # Exact replay also re-reads real engine outcomes and conservative charges.
    actual = register_terminal_application(
        session, fence, epoch_id, raw, settings, peer=peer
    )
    if actual.id != terminal.id:
        fail()
    return cursor, _state(cursor, context.config.search.domain)


def prepare_checkpoint(session, fence, epoch_id, raw, settings, *, peer):
    from backend.app.oe_structured_checkpoint_models import OEStructuredCheckpointIntent

    payload = _event(raw, {"frontier", "state_sha256", "save_ordinal"})
    context = controller(session, fence, epoch_id, settings, peer)
    ordinal = payload["save_ordinal"]
    if (
        type(ordinal) is not int
        or not 1 <= ordinal <= 4
        or type(payload["frontier"]) is not int
    ):
        fail()
    rows = list(
        session.scalars(
            select(OEStructuredCheckpointIntent)
            .filter_by(epoch_id=epoch_id)
            .order_by(OEStructuredCheckpointIntent.save_ordinal)
            .limit(5)
        )
    )
    if len(rows) > 4:
        fail()
    for index, row in enumerate(rows, 1):
        scoped(row, fence)
        if row.save_ordinal != index or row.epoch_id != epoch_id:
            fail()
    if ordinal <= len(rows):
        previous = rows[ordinal - 1]
        if (previous.frontier, previous.state_sha256) != (
            payload["frontier"],
            payload["state_sha256"],
        ):
            fail()
        return previous  # original intent, never a new save under same ordinal
    if ordinal != len(rows) + 1:
        fail()
    if rows:
        from backend.app.oe_structured_checkpoint_models import (
            OEStructuredCheckpointPublication,
        )

        published = session.scalar(
            select(OEStructuredCheckpointPublication).filter_by(intent_id=rows[-1].id)
        )
        scoped(published, fence)
        if (
            published.epoch_id,
            published.intent_id,
            published.control_state_id,
            published.state_sha256,
        ) != (epoch_id, rows[-1].id, rows[-1].control_state_id, rows[-1].state_sha256):
            fail()
    cursor, _ = current_state(session, fence, epoch_id, context, settings, peer)
    if (cursor.frontier, cursor.state_sha256) != (
        payload["frontier"],
        payload["state_sha256"],
    ):
        fail()
    row = OEStructuredCheckpointIntent(
        id=uuid4(),
        **scope(fence),
        epoch_id=epoch_id,
        control_state_id=cursor.id,
        save_ordinal=ordinal,
        frontier=cursor.frontier,
        state_sha256=cursor.state_sha256,
    )
    session.add(row)
    session.flush()
    return row


def native_files_state(files, domain):
    """Parse preserved native bytes in memory, never write/load arbitrary paths."""
    if (
        type(files) is not dict
        or not 4 <= len(files) <= MAX_FILES
        or not FIXED <= set(files)
    ):
        fail()
    total = 0
    programs = {}
    for name, raw in files.items():
        _name(name)
        if type(raw) is not bytes or not 0 < len(raw) <= MAX_FILE_BYTES:
            fail()
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            fail()
        if name.startswith("programs/"):
            programs[name[9:-5]] = _json(raw)
    state = _normalize(_json(files["metadata.json"]), programs, domain)
    best = programs[state["metadata"]["best_program_id"]]
    if files["best_program.json"] != best["code"].encode("utf-8"):
        fail()
    info = _json(files["best_program_info.json"])
    expected = {
        "id": best["id"],
        "generation": best["generation"],
        "iteration": best["iteration_found"],
        "current_iteration": state["metadata"]["last_iteration"],
        "metrics": best["metrics"],
        "language": best["language"],
        "timestamp": best["timestamp"],
    }
    if (
        type(info) is not dict
        or set(info) != set(expected) | {"saved_at"}
        or canonical({k: info[k] for k in expected}) != canonical(expected)
        or type(info["saved_at"]) not in (int, float)
        or not math.isfinite(info["saved_at"])
    ):
        fail()
    return state


def retained_files(session, fence, intent, completion, settings):
    from backend.app.oe_structured_checkpoint_models import OEStructuredCheckpointUpload
    from backend.app.oe_structured_checkpoint_upload import _begin

    upload = scoped(
        session.get(OEStructuredCheckpointUpload, completion.upload_id), fence
    )
    scoped(completion, fence)
    if (upload.intent_id, upload.epoch_id, completion.epoch_id) != (
        intent.id,
        intent.epoch_id,
        intent.epoch_id,
    ):
        fail()
    begin = _begin(upload)
    raw = read_research_object(
        session,
        fence,
        completion.inventory_object_id,
        "oe_structured_checkpoint_inventory",
        completion.inventory_object_sha256,
        settings,
    )
    inventory = Inventory.model_validate_json(raw)
    if (
        canonical(inventory.model_dump(mode="json")) != raw
        or any(getattr(inventory, k) != getattr(fence, k) for k in SCOPE)
        or (inventory.epoch_id, inventory.token, inventory.upload_id)
        != (intent.epoch_id, intent.id, completion.upload_id)
        or completion.inventory_object_kind != "oe_structured_checkpoint_inventory"
    ):
        fail()
    if [(f.name, f.size_bytes, f.sha256) for f in inventory.files] != [
        (f.name, f.size_bytes, f.sha256) for f in begin.files
    ]:
        fail()
    files = {}
    for ref in inventory.files:
        raw = read_research_object(
            session,
            fence,
            ref.object_id,
            "oe_structured_checkpoint_file",
            ref.object_sha256,
            settings,
        )
        envelope = FileEnvelope.model_validate_json(raw)
        if canonical(envelope.model_dump(mode="json")) != raw or (
            envelope.name,
            envelope.sha256,
            envelope.size_bytes,
        ) != (ref.name, ref.sha256, ref.size_bytes):
            fail()
        files[ref.name] = envelope.bytes()
    return files


def publish_checkpoint(session, fence, epoch_id, raw, settings, *, peer):
    from backend.app.oe_structured_checkpoint_models import (
        OEStructuredCheckpointCompletion,
        OEStructuredCheckpointIntent,
        OEStructuredCheckpointPublication,
        OEStructuredCheckpointUpload,
    )

    payload = _event(raw, {"token", "frontier", "state_sha256", "path"})
    token = UUID(payload["token"])
    if (
        str(token) != payload["token"]
        or payload["path"] != f"/work/checkpoints/{token}"
        or type(payload["frontier"]) is not int
    ):
        fail()
    context = controller(session, fence, epoch_id, settings, peer)
    intent = scoped(session.get(OEStructuredCheckpointIntent, token), fence)
    if (intent.epoch_id, intent.frontier, intent.state_sha256) != (
        epoch_id,
        payload["frontier"],
        payload["state_sha256"],
    ):
        fail()
    upload = scoped(
        session.scalar(select(OEStructuredCheckpointUpload).filter_by(intent_id=token)),
        fence,
    )
    completion = scoped(
        session.scalar(
            select(OEStructuredCheckpointCompletion).filter_by(upload_id=upload.id)
        ),
        fence,
    )
    if (upload.epoch_id, completion.epoch_id) != (epoch_id, epoch_id):
        fail()
    from backend.app.oe_structured_checkpoint_upload import require_upload_hold

    require_upload_hold(session, fence, upload)
    state = native_files_state(
        retained_files(session, fence, intent, completion, settings),
        context.config.search.domain,
    )
    cursor = scoped(
        session.get(OEStructuredControlState, intent.control_state_id), fence
    )
    if (
        cursor.epoch_id != epoch_id
        or cursor.phase not in {"seed", "applied"}
        or state_sha256(state) != intent.state_sha256
        or state != _state(cursor, context.config.search.domain)
    ):
        fail()
    previous = session.scalar(
        select(OEStructuredCheckpointPublication).filter_by(intent_id=token)
    )
    if previous is not None:
        scoped(previous, fence)
        if (
            previous.epoch_id,
            previous.completion_id,
            previous.control_state_id,
            previous.state_sha256,
        ) != (epoch_id, completion.id, cursor.id, intent.state_sha256):
            fail()
        if controller(session, fence, epoch_id, settings, peer) != context:
            fail()
        return previous  # old retained bytes, not the newer population's authority
    current, actual = current_state(session, fence, epoch_id, context, settings, peer)
    if current.id != cursor.id or actual != state:
        fail()
    row = OEStructuredCheckpointPublication(
        id=uuid4(),
        **scope(fence),
        epoch_id=epoch_id,
        intent_id=token,
        completion_id=completion.id,
        control_state_id=cursor.id,
        state_sha256=intent.state_sha256,
    )
    session.add(row)
    session.flush()
    return row
