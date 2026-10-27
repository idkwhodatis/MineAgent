"""Explicit seeded input grammar/prompt bounds; no controller authority claim."""

from uuid import uuid4

import pytest
from test_rdagent_seed_parent import resolve, retained_parent  # noqa:F401

from packages.factors.contracts import canonical_bytes, source_sha256
from packages.rdagent_adapter.contracts import FactorResearchConfig
from packages.rdagent_adapter.operational_bootstrap import RDOperationalBootstrap
from packages.rdagent_adapter.seed_parent import ResolvedSeedParent
from packages.rdagent_adapter.seed_prompt_budget import first_prompt_messages
from packages.rdagent_adapter.seeded_bootstrap import (
    RDSeededOperationalBootstrap,
    parse_bootstrap,
)
from runtimes.rdagent.probe_fixture import research_dict


@pytest.fixture
def bootstrap(retained_parent):  # noqa:F811
    config = FactorResearchConfig.model_validate(
        {**research_dict(), "max_iterations": 1}
    )
    return RDSeededOperationalBootstrap(
        owner_id=retained_parent.owner,
        run_id=uuid4(),
        attempt_id=uuid4(),
        generation=1,
        spec_id=uuid4(),
        attempt_binding_id=uuid4(),
        config=config,
        config_sha256=config.sha256,
        input_sha256=config.input.sha256,
        seeded_submission_id=uuid4(),
        seed=resolve(retained_parent),
    )


def test_seeded_single_file_dispatch_preserves_complete_parent_and_original_v1(
    bootstrap,
):
    raw = canonical_bytes(bootstrap)
    assert parse_bootstrap(raw) == bootstrap
    assert parse_bootstrap(raw).seed.source == bootstrap.seed.source
    with pytest.raises(ValueError):
        RDOperationalBootstrap.model_validate_json(raw)
    fresh = RDOperationalBootstrap.model_validate(
        {
            **bootstrap.model_dump(
                exclude={
                    "seed",
                    "seeded_submission_id",
                    "schema_version",
                    "continuation",
                }
            ),
            "schema_version": "mineagent.rdagent-operational-bootstrap.v1",
            "continuation": "fresh_only",
        }
    )
    assert type(parse_bootstrap(canonical_bytes(fresh))) is RDOperationalBootstrap


@pytest.mark.parametrize(
    "field,value",
    [
        ("owner_id", str(uuid4())),
        ("continuation", "fresh_only"),
        ("schema_version", "unknown"),
        ("input_layout", "source_mount"),
        ("generation", True),
    ],
)
def test_seeded_bootstrap_rejects_ambiguous_or_foreign_identity(
    bootstrap, field, value
):
    with pytest.raises(ValueError):
        parse_bootstrap(
            canonical_bytes({**bootstrap.model_dump(mode="json"), field: value})
        )


def test_duplicate_bootstrap_key_rejected(bootstrap):
    raw = canonical_bytes(bootstrap)
    changed = b'{"generation":1,' + raw[1:]
    with pytest.raises(ValueError):
        parse_bootstrap(changed)


def test_source_tamper_rejected_without_execution(bootstrap):
    raw = bootstrap.model_dump(mode="json")
    raw["seed"]["source"] += "# changed\n"
    with pytest.raises(ValueError):
        parse_bootstrap(canonical_bytes(raw))


def test_whole_first_message_limit_not_just_seed_section(bootstrap):
    source = bootstrap.seed.source + "\n#" + "x" * 1300
    sha = source_sha256(source)
    definition = bootstrap.seed.definition.model_copy(update={"code_sha256": sha})
    reference = bootstrap.seed.reference.model_copy(
        update={
            "code_sha256": sha,
            "code_size_bytes": len(source.encode()),
            "definition_sha256": definition.sha256,
        }
    )
    seed = ResolvedSeedParent(reference=reference, definition=definition, source=source)
    assert len(seed.prompt_section()) <= 6144
    with pytest.raises(ValueError, match="message limit"):
        first_prompt_messages(bootstrap.config, seed)
