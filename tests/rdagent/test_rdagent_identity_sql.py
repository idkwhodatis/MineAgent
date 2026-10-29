"""Offline grammar coverage, NOT a PostgreSQL execution/differential claim.

CONFIG_CASES/request_cases are reusable by a real-PG migration test: execute the
rendered helpers in an isolated migrated schema and compare every expected bool.
"""

import copy
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo, available_timezones

import pytest
from test_rdagent_contracts import research_dict

from backend.app import rdagent_identity_sql as module
from packages.rdagent_adapter.contracts import FactorResearchConfig
from packages.research_runtime.engine_broker_contracts import parse_request

CONFIG_CASES = [
    (("upstream_workers",), True),
    (("upstream_workers",), 1.0),
    (("max_source_bytes",), 131072.0),
    (("max_iterations",), True),
    (("max_iterations",), 0),
    (("max_iterations",), 9),
    (("max_output_rows_per_cutoff",), 2),
    (("max_output_bytes_per_cutoff",), 1023),
    (("budget", "calls"), 0),
    (("budget", "tokens"), 9007199254740992),
    (("budget", "process_slots"), False),
    (("budget", "mutation_slots"), 2),
    (("input", "lookback_sessions"), 64),
    (("input", "lag_sessions"), -1),
    (("input", "market_timezone"), "Definitely/Not_A_Zone"),
    (("input", "calendar_sha256"), "A" * 64),
    (("input", "dataset", "dataset_version_id"), "invalid"),
    (("input", "dataset", "dataset_version_id"), "00000000000000000000000000000002"),
    (("input", "window", "start"), "2024-02-30"),
    (("input", "window", "start"), "9999-01-01"),
    (("input", "calculation_universe", "listing_ids"), ["B", "A"]),
    (("input", "calculation_universe", "listing_ids"), ["A", "A"]),
    (
        ("input", "calculation_universe", "membership_available_at"),
        "2024-01-01T00:00:00",
    ),
    (
        ("input", "calculation_universe", "membership_available_at"),
        "2024-01-01T00:00:00+00:00",
    ),
    (("task", "description"), ""),
    (("task", "description"), "a" * 8001),
    (("task", "name"), "bad\tname"),
    (("runtime", "candidate_profile"), "mineagent.factor-prefix-candidate.v1"),
    (("runtime", "analysis_image"), "sha256:" + "A" * 64),
]


def canonical_config():
    return FactorResearchConfig.model_validate(research_dict()).model_dump(mode="json")


def normalized_config_accepts(value):
    """Reference for canonical *persisted* shape, not permissive API inputs."""
    try:
        checked = FactorResearchConfig.model_validate_json(json.dumps(value))
        return checked.model_dump(mode="json") == value
    except (ValueError, KeyError):
        return False


def request_cases():
    chat = {
        "kind": "chat",
        "model": "fixture.rdagent.v1",
        "messages": [{"role": "system", "content": "你好"}],
        "max_output_tokens": 4096,
        "temperature_millionths": 0,
        "response_format": {"type": "json_object"},
    }
    embedding = {
        "kind": "embedding",
        "model": "fixture.rdagent.v1",
        "inputs": ["x"],
        "dimensions": 2,
    }
    yield "mineagent.rdagent-chat.v1", chat, True
    yield "mineagent.rdagent-embedding.v1", embedding, True
    for field, value in [
        ("max_output_tokens", True),
        ("max_output_tokens", 1.0),
        ("temperature_millionths", False),
        ("max_output_tokens", 4097),
        ("response_format", {}),
        ("messages", []),
        ("messages", [{"role": "tool", "content": "x"}]),
    ]:
        yield "mineagent.rdagent-chat.v1", {**chat, field: value}, False
    for field, value in [
        ("dimensions", 2.0),
        ("dimensions", True),
        ("inputs", [""]),
        ("inputs", ["x" * 8193]),
        ("inputs", [1]),
    ]:
        yield "mineagent.rdagent-embedding.v1", {**embedding, field: value}, False


def test_render_is_deterministic_separate_and_qualified():
    sql = module.render_sql("rd_test")
    assert sql == module.render_sql("rd_test")
    assert "@" not in sql
    assert sql.count("CREATE OR REPLACE FUNCTION rd_test.") == 5
    assert "mineagent_neutral_" not in sql
    assert "CREATE TRIGGER" not in sql
    for name in (
        "config(v jsonb)",
        "context(v jsonb)",
        "request(v jsonb,protocol text)",
    ):
        assert "mineagent_rd_source_" + name in sql
    assert "SET search_path=pg_catalog" in sql
    assert "EXCEPTION WHEN data_exception THEN RETURN false" in sql

    # Only the exact UTF8/ASCII branch changes; preserve all old depth, NULL,
    # numeric and over-bound counting semantics (this helper does not clamp).
    import ast
    import re

    rendered = module.render_sql("public")
    start = rendered.index("CREATE OR REPLACE FUNCTION public.mineagent_rd_json_bytes(")
    helper = rendered[start : rendered.index("$$;", start) + 3]
    branch = (
        "   IF current_setting('server_encoding')='UTF8' AND octet_length(t)<=1048576 AND octet_length(t)=char_length(t) AND position(chr(127) in t)=0 THEN\n"
        "     RETURN octet_length(pg_catalog.to_json(t)::text);\n"
        "   END IF;\n"
    )
    assert helper.count(branch) == 1
    assert (
        hashlib.sha256(helper.replace(branch, "", 1).encode()).hexdigest()
        == "08a5f8cc862bf0a07066ce49d4002be7ce20405bef8bd5443198a18fb2c06d79"
    )
    definitions = []
    for path in sorted(
        (Path(__file__).resolve().parents[2] / "backend/alembic/versions").glob("*.py")
    ):
        for node in ast.parse(path.read_text()).body:
            if not isinstance(node, ast.Assign):
                continue
            try:
                literal = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                continue
            if not isinstance(literal, str):
                continue
            for match in re.finditer(
                r"CREATE(?: OR REPLACE)? FUNCTION (?:@SCHEMA@\.)?mineagent_rd_json_bytes\(",
                literal,
            ):
                actual = literal[
                    match.start() : literal.index("$$;", match.start()) + 3
                ]
                assert actual == helper.replace("public.", "@SCHEMA@.")
                definitions.append((path.name, node.targets[0].id))
    assert definitions == [("018_rd_source_persistence.py", "UPGRADE_SQL")]


@pytest.mark.parametrize("value", ["x;DROP TABLE y", "public.x", "X", "", "a" * 64])
def test_schema_identifier_closed(value):
    with pytest.raises(ValueError):
        module.render_sql(value)


def test_frozen_timezones_exact_complete_local_inventory():
    assert module.TIMEZONE_NAMES == tuple(sorted(available_timezones()))
    assert module.TIMEZONE_SOURCE["version"] == "2026b"
    # Source provenance belongs to the immutable generator input, not the host
    # distribution's repackaged tzdata.zi. Host acceptance remains the exact
    # complete inventory check above, plus ZoneInfo resolution below.
    source = Path(__file__).parents[1] / "fixtures/rdagent/tzdata-2026b.zi"
    data = source.read_bytes()

    def verify_names(raw):
        names = set()
        for line in raw.decode("ascii").splitlines():
            fields = line.split()
            if fields and fields[0] in {"Z", "Zone"}:
                names.add(fields[1])
            elif fields and fields[0] in {"L", "Link"}:
                names.add(fields[2])
        # localtime is the original system alias, not an IANA Zone/Link record.
        assert tuple(sorted(names | {"localtime"})) == module.TIMEZONE_NAMES

    def verify_source(raw):
        assert hashlib.sha256(raw).hexdigest() == module.TIMEZONE_SOURCE["sha256"]
        assert raw.splitlines()[0] == b"# version 2026b"
        verify_names(raw)

    verify_source(data)
    with pytest.raises(AssertionError):
        verify_source(data + b"# altered packaging must not replace provenance\n")
    with pytest.raises(AssertionError):
        verify_names(data.replace(b"Z Africa/Abidjan", b"Z Invalid/Timezone", 1))
    for name in module.TIMEZONE_NAMES:
        assert ZoneInfo(name).key == name


def test_config_roundtrip_full_default_keys_required():
    value = canonical_config()
    assert normalized_config_accepts(value)
    assert value["input"]["preprocessing"] == {"kind": "none"}
    assert value["input"]["history_semantics"] == "prior_sessions_plus_current"
    del value["upstream_workers"]
    assert not normalized_config_accepts(value)


@pytest.mark.parametrize(("path", "replacement"), CONFIG_CASES)
def test_negative_corpus_matches_canonical_pydantic(path, replacement):
    value = canonical_config()
    node = value
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = replacement
    assert not normalized_config_accepts(value)


@pytest.mark.parametrize(("protocol", "value", "expected"), list(request_cases()))
def test_request_corpus_matches_real_codec(protocol, value, expected):
    try:
        parsed = parse_request(protocol, value)
        actual = parsed.model_dump(mode="json") == value
    except ValueError:
        actual = False
    assert actual is expected


def test_nested_key_and_lock_order_mutations():
    for path in (
        (),
        ("input",),
        ("input", "dataset"),
        ("runtime",),
        ("budget",),
        ("task",),
    ):
        value = canonical_config()
        node = value
        for key in path:
            node = node[key]
        node["unexpected"] = None
        assert not normalized_config_accepts(value)
    value = canonical_config()
    value["runtime"]["dependency_locks"].reverse()
    assert not normalized_config_accepts(value)
    value = canonical_config()
    value["input"]["input_fields"] *= 2
    assert not normalized_config_accepts(value)


def test_fitted_preprocessing_readiness_and_canonical_utc():
    value = canonical_config()
    value["input"]["preprocessing"] = {
        "kind": "fitted",
        "transform_id": "x",
        "code_sha256": "a" * 64,
        "parameters_sha256": "b" * 64,
        "fitted_artifact_sha256": "c" * 64,
        "fit_window": {"start": "2020-01-01", "end": "2020-02-01"},
        "last_input_at": "2020-02-01T00:00:00Z",
        "available_at": "2020-02-01T00:00:01Z",
    }
    assert normalized_config_accepts(value)
    bad = copy.deepcopy(value)
    bad["input"]["preprocessing"]["available_at"] = "2020-01-01T00:00:00Z"
    assert not normalized_config_accepts(bad)
    bad = copy.deepcopy(value)
    bad["input"]["preprocessing"]["available_at"] = "2020-02-01T00:00:01.000000Z"
    assert not normalized_config_accepts(bad)


def test_reviewed_semantic_checks_present_not_schema_only():
    sql = module.render_sql()
    for text in (
        "DateWindow",
        "FittedPreprocessing",
        "CalculationUniverse",
        "RuntimePins",
        "FrozenResearchInput",
        "FactorResearchConfig",
        "RDEmbeddingRequest",
        'previous COLLATE "C">=x COLLATE "C"',
        "'open','high','low','close','volume'",
        "mutation_slots",
        "jsonb_typeof(v->'task_index')='number'",
        "right(t,8)='.000000Z'",
        "ascii_only AND p>=127",
        "ascii_only AND p>65535",
        "1048576",
        "262144",
    ):
        assert text in sql


def test_source_pin_drift_fails_render(monkeypatch):
    monkeypatch.setitem(
        module.SOURCE_PINS, "packages/rdagent_adapter/contracts.py", "0" * 64
    )
    with pytest.raises(ValueError, match="Contract source drift"):
        module.render_sql()


def test_seeded_successor_preserves_exact_historical_identity_sql():
    import hashlib

    from backend.app.rdagent_identity_sql import render_sql

    assert hashlib.sha256(render_sql().encode()).hexdigest() == (
        "360b5ad619aba3d6cdf06f4ee9064f68797c7af210213ed01476683bbd72a3fa"
    )
