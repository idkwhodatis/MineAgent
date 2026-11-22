"""Closed source-pinned provider-broker call sites; test diagnostics only."""

from hashlib import sha256
from pathlib import Path
from types import CodeType

# Exact source continuation with reviewed call sites. Any drift makes the
# diagnosis unavailable rather than projecting a nearby or unrelated frame.
_SOURCES = (
    "b2beef8c3e8f2e9aadbc049c904bab5b7a47db9598c2839bcc8e59357695077a",
    "054a8e1f4bd807715eaf8128c06896c88fdaa8336edda611332b9ce09a0e93f0",
    "d18307c9d5a9070e114c63f652263fa7f66dd392e1e0502853c734cc8951c009",
)


def _pinned(modules):
    for module, expected in zip(modules, _SOURCES, strict=True):
        try:
            raw = Path(module.__file__).read_bytes()
        except (OSError, TypeError):
            return False
        if sha256(raw).hexdigest() != expected:
            return False
    return True


def broker_busy_boundary(trace):
    from backend.app import (
        rdagent_operational_broker as operational,
    )
    from backend.app import (
        rdagent_provider_broker as broker,
    )
    from backend.app import (
        research_object_services as objects,
    )
    from packages.rdagent_provider.process_receipts import (
        SDKIntent,
        SDKStarted,
        SDKStopped,
    )

    submit = broker.submit_provider_request.__code__
    callback = [
        value
        for value in submit.co_consts
        if type(value) is CodeType
        and value.co_qualname == "submit_provider_request.<locals>.identity_boundary"
    ]
    if len(callback) != 1:
        return None
    roles = {
        submit: "submit",
        callback[0]: "callback",
        broker._retain_receipt.__code__: "receipt",
        operational._publish.__code__: "publish",
        objects.publish_reserved_object.__code__: "reserved",
    }
    frames = {}
    for _ in range(64):
        if trace is None:
            break
        frame = trace.tb_frame
        role = next(
            (label for code, label in roles.items() if frame.f_code is code), None
        )
        if role is not None:
            if role in frames:
                return None
            frames[role] = (trace.tb_lineno, frame.f_locals)
        trace = trace.tb_next
    if (
        trace is not None
        or "submit" not in frames
        or not _pinned((broker, operational, objects))
    ):
        return None

    site = frames["submit"][0]
    reuse = frames.get("callback")
    receipt = frames.get("receipt")
    publish = frames.get("publish")
    reserved = frames.get("reserved")

    if 205 <= site <= 207 and publish is not None and reuse is None and receipt is None:
        if 109 <= publish[0] <= 116 and reserved is None:
            return "reserve"
        # Only the exact fresh request publication enters the prepare read.
        # Already-retained/replayed reservations do not get this label.
        if (
            publish[0] == 139
            and "previous" in publish[1]
            and publish[1]["previous"] is None
            and reserved is not None
            and reserved[0] == 766
        ):
            return "request_read"
        return None
    if 208 <= site <= 223 and not (reuse or receipt or publish or reserved):
        return "admit"
    if site == 249 and reuse is None and receipt is not None:
        return "intent" if type(receipt[1].get("document")) is SDKIntent else None
    if 282 <= site <= 290 and reuse is not None:
        callback_site = reuse[0]
        if callback_site in (255, 256, 257, 258, 276, 277, 278, 279) and not (
            receipt or publish or reserved
        ):
            return "started"
        if callback_site == 274 and receipt is not None:
            return {SDKStarted: "started", SDKStopped: "stop"}.get(
                type(receipt[1].get("document"))
            )
        return None
    if 307 <= site <= 314 and publish is not None and reuse is None and receipt is None:
        return "response_publish"
    if 315 <= site <= 323 and not (reuse or receipt or publish or reserved):
        return "settle"
    return None
