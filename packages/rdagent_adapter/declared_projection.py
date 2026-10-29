"""Typed in-memory projection of verified declaration bytes, not a new data claim."""

import hashlib
from datetime import datetime
from uuid import UUID

from packages.rdagent_adapter.declared_upload import LIMITS, validate_declared_basket
from packages.strategy_sdk import parse_json


def project_basket(manifest_bytes, artifacts, *, dataset_id, owner_id, manifest_sha256):
    if type(dataset_id) is not UUID or type(owner_id) is not UUID:
        raise ValueError("Exact owned declared basket required")
    built = validate_declared_basket(
        manifest_bytes, artifacts, dataset_id=dataset_id, owner_id=owner_id
    )
    if built.sha256 != manifest_sha256:
        raise ValueError("Declared basket identity changed")
    manifest = parse_json(manifest_bytes, limits=LIMITS)
    originals = [
        parse_json(artifacts[ref["artifact_prefix"] + "manifest.json"], limits=LIMITS)
        for ref in manifest["sources"]
    ]
    request = dict(originals[0]["request"])
    request["listings"] = manifest["listings"]
    request["price_mode"] = "user_declared_raw"
    # Preserve the latest acquisition timestamp, never claim historical knowledge.
    request["as_of"] = max(
        (x["request"]["as_of"] for x in originals),
        key=lambda value: datetime.fromisoformat(value),
    )
    return {
        **manifest,
        "request": request,
        "price_semantics": originals[0]["price_semantics"],
        "universe": {"state": "user_fixed", "point_in_time": False},
        "bars_sha256": hashlib.sha256(artifacts["preview.json"]).hexdigest(),
    }
