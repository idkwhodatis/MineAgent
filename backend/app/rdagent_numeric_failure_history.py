"""Owned historical rejection evidence; never a release or execution capability.

The physical helper preserves original execution identity after relocation. Its
caller must load exact owner/terminal-attempt SQL evidence and independently
validate source, controller, provider and workflow/release relationships. There is
intentionally no public disclose endpoint or caller-supplied 'verified' flag.
"""

import hashlib
import os
import stat
import time
from pathlib import Path

from backend.app.core.atomic_storage import _open_directory
from backend.app.factor_analysis_history import retained_reader
from backend.app.factor_native_io import require_native_storage_guard
from backend.app.numeric_rejection_backup import validate_rejection_closure_unit
from packages.research_runtime.process_contracts import canonical
from packages.strategy_sdk import JsonLimits, parse_json

MAX_OPERATION_FILES = 128
MAX_OPERATION_BYTES = 64 * 1024 * 1024 + 65536


def _operation_inventory(root, owner_id, admission_id, deadline):
    """Enumerate only this exact operation via retained nofollow directory FDs."""
    from uuid import UUID

    owner_id, admission_id = str(UUID(str(owner_id))), str(UUID(str(admission_id)))
    require_native_storage_guard(root)
    prefix = f"research-processes/{owner_id}/{admission_id}"
    directory = _open_directory(Path(root) / prefix)
    found = {}
    total = 0

    def walk(fd, key, depth):
        nonlocal total
        before = os.fstat(fd)
        with os.scandir(fd) as entries:
            for entry in entries:
                if time.monotonic() >= deadline:
                    raise ValueError("Historical operation read deadline")
                info = entry.stat(follow_symlinks=False)
                child = key + "/" + entry.name
                if stat.S_ISDIR(info.st_mode):
                    if depth != 0 or entry.name not in {"control", "inputs", "output"}:
                        raise ValueError("Unknown historical operation directory")
                    nested = os.open(
                        entry.name,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=fd,
                    )
                    try:
                        current = os.fstat(nested)
                        if (current.st_dev, current.st_ino) != (
                            info.st_dev,
                            info.st_ino,
                        ):
                            raise ValueError("Historical operation directory changed")
                        walk(nested, child, depth + 1)
                        named = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
                        if (named.st_dev, named.st_ino) != (info.st_dev, info.st_ino):
                            raise ValueError("Historical operation directory replaced")
                    finally:
                        os.close(nested)
                else:
                    if (
                        depth != 1
                        or not stat.S_ISREG(info.st_mode)
                        or info.st_nlink != 1
                    ):
                        raise ValueError("Unknown historical operation leaf")
                    total += info.st_size
                    if len(found) >= MAX_OPERATION_FILES or total > MAX_OPERATION_BYTES:
                        raise ValueError("Historical operation inventory exceeds bound")
                    found[child] = (
                        info.st_size,
                        info.st_dev,
                        info.st_ino,
                        info.st_mode,
                        info.st_nlink,
                        info.st_mtime_ns,
                        info.st_ctime_ns,
                    )
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError("Historical operation inventory changed")

    try:
        walk(directory, prefix, 0)
    finally:
        os.close(directory)
    require_native_storage_guard(root)
    return found


def read_rejection_evidence(root, admission, indexes):
    """Internal real-file component, not a loader or source/release authorization.

    ``indexes`` must be exact SQL evidence loaded by the owning history reader,
    never HTTP input. It returns the cold validator's actual immutable journal
    tuple and rejects unknown/stale leaves rather than cleaning or adopting them.
    """
    require_native_storage_guard(root)
    deadline = time.monotonic() + 15
    inventory = _operation_inventory(
        root, admission["owner_id"], admission["id"], deadline
    )
    physical = {key: identity[0] for key, identity in inventory.items()}
    read = retained_reader(root)
    seen = {}

    def checked_bytes(key, maximum):
        if key not in physical or not 0 < physical[key] <= maximum:
            raise ValueError("Historical operation file bound differs")
        if key not in seen:
            if time.monotonic() >= deadline:
                raise ValueError("Historical operation read deadline")
            seen[key] = read(key, maximum)
        if len(seen[key]) != physical[key] or time.monotonic() >= deadline:
            raise ValueError("Historical operation byte inventory differs")
        return seen[key]

    def read_json(key):
        raw = checked_bytes(key, 16384)
        value = parse_json(
            raw,
            limits=JsonLimits(
                max_bytes=16384, max_depth=16, max_nodes=2000, max_items=512
            ),
        )
        if canonical(value) != raw:
            raise ValueError("Historical journal is not canonical")
        return value

    def check(key, sha, size):
        raw = checked_bytes(key, max(size, 1))
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != sha:
            raise ValueError("Historical journal or retained input differs")

    allowed = set()
    result = validate_rejection_closure_unit(
        admission, indexes, physical, check, read_json, allowed, set()
    )
    if set(physical) != allowed:
        raise ValueError("Historical operation contains unaccounted bytes")
    if (
        _operation_inventory(root, admission["owner_id"], admission["id"], deadline)
        != inventory
    ):
        raise ValueError("Historical operation inventory changed after validation")
    require_native_storage_guard(root)
    return result


class _ScopedRows:
    """Streaming SQL evidence loader with one shared attempt/byte/deadline budget."""

    def __init__(self, session, owner_id, run_id, attempt_id, generation):
        from uuid import UUID

        if any(type(value) is not UUID for value in (owner_id, run_id, attempt_id)):
            raise ValueError("Exact historical scope required")
        if type(generation) is not int or generation < 1:
            raise ValueError("Exact historical generation required")
        self.session = session
        self.scope = {
            "owner_id": owner_id,
            "run_id": run_id,
            "attempt_id": attempt_id,
            "generation": generation,
        }
        self.rows = 0
        self.bytes = 0
        self.deadline = time.monotonic() + 15

    def read(self, model, **filters):
        from datetime import date, datetime
        from uuid import UUID

        from sqlalchemy import select

        table = model.__table__
        # Table authority is the owning Python call site, never an HTTP name.
        if not set(self.scope) <= set(table.columns.keys()):
            raise ValueError("Attempt-bound table required")
        if set(filters) & set(self.scope):
            raise ValueError("Historical scope cannot be overridden")
        statement = (
            select(table)
            .filter_by(**self.scope, **filters)
            .limit(257)
            .execution_options(stream_results=True, yield_per=1)
        )
        result = self.session.execute(statement)
        rows = []
        try:
            for source in result.mappings():
                if time.monotonic() >= self.deadline or len(rows) >= 256:
                    raise ValueError("Historical scoped query exceeds bound")
                row = {
                    key: str(value)
                    if isinstance(value, UUID)
                    else value.isoformat()
                    if isinstance(value, (date, datetime))
                    else value
                    for key, value in source.items()
                }
                if any(
                    row[key] != (str(value) if isinstance(value, UUID) else value)
                    for key, value in self.scope.items()
                ):
                    raise ValueError("Historical row scope differs")
                self.rows += 1
                self.bytes += len(canonical(row)) + 1
                if self.rows > 4096 or self.bytes > 8 * 1024 * 1024:
                    raise ValueError("Historical aggregate inventory exceeds bound")
                rows.append(row)
        finally:
            result.close()
        return rows


def _provider_model_bytes(
    operations,
    transports,
    outcomes,
    reservations,
    settlements,
    objects,
    binding,
    read_bytes,
):
    """Recheck actual completed SDK envelopes and charge, not just containment.

    Internal SQL-loaded mappings only. Provider process receipts and source/call
    membership must be validated separately before a historical buffer is usable.
    """
    from packages.rdagent_provider import codec
    from packages.rdagent_provider.accounting import expected_usage, request_reservation
    from packages.rdagent_provider.contracts import (
        RDProviderChatResponse,
        RDProviderEmbeddingResponse,
        require_request_binding,
    )

    def one(rows, field, value):
        matches = [row for row in rows.values() if row[field] == value]
        if len(matches) != 1:
            raise ValueError("Exact completed provider relationship required")
        return matches[0]

    def scope(left, right):
        if any(
            left[k] != right[k]
            for k in ("owner_id", "run_id", "attempt_id", "generation")
        ):
            raise ValueError("Provider historical scope differs")

    def raw(owner, identity, kind, sha):
        obj = objects[identity]
        key = f"research-objects/{owner}/by-kind/{kind}/{sha}.json"
        if (obj["owner_id"], obj["object_kind"], obj["sha256"], obj["storage_key"]) != (
            owner,
            kind,
            sha,
            key,
        ):
            raise ValueError("Provider historical object identity differs")
        if type(obj["size_bytes"]) is not int or not 0 < obj["size_bytes"] <= 262144:
            raise ValueError("Provider historical envelope bound")
        content = read_bytes(key, sha, 262144)
        if (
            type(content) is not bytes
            or len(content) != obj["size_bytes"]
            or hashlib.sha256(content).hexdigest() != sha
        ):
            raise ValueError("Provider historical envelope bytes differ")
        return content

    for op in operations.values():
        protocol = op["protocol"]
        if protocol not in codec.PROTOCOLS or op["state"] != "completed":
            raise ValueError("Only fully completed provider calls can supply feedback")
        request = codec.parse_request(protocol, op["request"])
        require_request_binding(request, binding)
        if (
            codec.request_sha256(protocol, request) != op["request_sha256"]
            or op["request_object_kind"] != "model_request"
        ):
            raise ValueError("Provider request commitment differs")
        request_raw = raw(
            op["owner_id"],
            op["request_object_id"],
            "model_request",
            op["request_sha256"],
        )
        if request_raw != codec.canonical_bytes(protocol, request):
            raise ValueError("Actual provider request differs")
        transport = one(transports, "operation_id", op["id"])
        outcome = one(outcomes, "transport_id", transport["id"])
        held = reservations[transport["reservation_id"]]
        charged = one(settlements, "reservation_id", held["id"])
        for row in (transport, outcome, held, charged):
            scope(op, row)
        if transport["state"] != "completed" or outcome["outcome"] != "completed":
            raise ValueError("Provider completion differs")
        expected = request_reservation(request, binding).model_dump()
        if (
            held["purpose"] != "model_transport"
            or held["state"] != "settled"
            or held["amounts"] != expected
        ):
            raise ValueError("Provider original reservation differs")
        if (
            charged["outcome"] != "conservative_unknown"
            or charged["charge"] != expected
            or outcome["usage"] != expected
        ):
            raise ValueError("Provider complete conservative charge differs")
        if outcome["response_object_kind"] != "model_response":
            raise ValueError("Provider response object kind differs")
        response_raw = raw(
            op["owner_id"],
            outcome["response_object_id"],
            "model_response",
            outcome["response_sha256"],
        )
        cls = (
            RDProviderChatResponse
            if request.kind == "chat"
            else RDProviderEmbeddingResponse
        )
        response = cls.model_validate_json(response_raw)
        _, charge = expected_usage(protocol, request, response, binding)
        if (
            response_raw != codec.canonical_bytes(protocol, response)
            or charge.model_dump() != expected
        ):
            raise ValueError("Provider actual response or usage differs")
    if {t["operation_id"] for t in transports.values()} != set(operations) or len(
        transports
    ) != len(operations):
        raise ValueError("Orphan or repeated historical provider transport")
    if {o["transport_id"] for o in outcomes.values()} != set(transports) or len(
        outcomes
    ) != len(transports):
        raise ValueError("Orphan or repeated historical provider outcome")
