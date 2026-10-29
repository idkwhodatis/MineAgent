"""New source dispatch never reinterprets historical synthetic/declared contracts."""

import runpy
from pathlib import Path
from uuid import UUID

import pytest

from packages.rdagent_adapter.configured_source import (
    ConfiguredSourcePolicy,
    parse_retained_source,
)
from packages.rdagent_adapter.declared_upload import build_declared_basket


def bundle():
    f = runpy.run_path(str(Path(__file__).with_name("test_declared_upload_source.py")))
    value = build_declared_basket(
        owner_id=f["OWNER"],
        dataset_id=f["TARGET"],
        sources=(f["source"](0), f["source"](1)),
    )
    return f, value


def test_declared_projection_requires_new_explicit_owner_and_gate():
    f, b = bundle()
    kwargs = {
        "dataset_id": f["TARGET"],
        "manifest_sha256": b.sha256,
        "manifest_bytes": b.manifest_bytes,
        "artifacts": dict(b.files),
        "policy": ConfiguredSourcePolicy(acknowledged_unverified=True),
        "input_names": ("close", "volume"),
    }
    with pytest.raises(ValueError):
        parse_retained_source(**kwargs)
    with pytest.raises(ValueError):
        parse_retained_source(**kwargs, declared_upload=True)
    with pytest.raises(ValueError):
        parse_retained_source(**kwargs, declared_upload=True, owner_id=UUID(int=999))
    result = parse_retained_source(**kwargs, declared_upload=True, owner_id=f["OWNER"])
    assert result.dataset.content_sha256 == b.sha256
    assert result.dataset.source_quality == "declared_source"
    assert len(result.universe.listing_ids) == 2
    assert result.universe.membership_history == "declared"
    assert result.dataset.price_semantics == "unadjusted"
    assert len(result.observations) == 4
