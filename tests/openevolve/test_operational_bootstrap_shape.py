"""Actual typed bootstrap corpus; exported SQL is separately executed by PG gates."""

import copy
import json

from backend.app.openevolve_identity_sql import _expanded, identity_sql
from backend.app.openevolve_operational_ddl import render_sql
from runtimes.openevolve_operational.bootstrap import OperationalBootstrap


def test_operational_grammar_preserves_float_bounds_and_closed_types(tmp_path):
    rule = _expanded(OperationalBootstrap)
    market = rule["properties"]["native_inputs"]["properties"]["market_profile"]
    cash = market["properties"]["initial_cash"]
    assert cash["type"] == "number"
    assert cash["exclusiveMinimum"] == 0
    assert "minimum" not in cash
    assert market["properties"]["commission_rate"]["maximum"] == 0.1
    assert market["properties"]["t_plus_one"]["type"] == "boolean"
    assert rule["properties"]["issued_at"]["format"] == "date-time"
    assert "$ref" not in json.dumps(rule)
    allowed = {
        "type",
        "additionalProperties",
        "properties",
        "required",
        "items",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "pattern",
        "format",
        "const",
        "enum",
        "anyOf",
        "oneOf",
        "description",
    }

    def visit(node):
        if not isinstance(node, dict):
            return
        assert set(node) <= allowed
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
            for child in node["properties"].values():
                visit(child)
        for key in ("anyOf", "oneOf"):
            for child in node.get(key, []):
                visit(child)
        if "items" in node:
            visit(node["items"])

    for _, model, value in typed_corpus(tmp_path):
        visit(_expanded(model))
        assert (
            model.model_validate_json(json.dumps(value)).model_dump(mode="json")
            == value
        )
    sql = render_sql()
    assert "RETURN public.mineagent_oe_shape(v,rule,0)" in sql
    assert "mineagent_rd_schema" not in sql
    shape = identity_sql("public")
    assert "wanted IN ('integer','number')" in shape
    assert "n<=(s->>'exclusiveMinimum')::numeric" in shape
    assert "n>=(s->>'exclusiveMaximum')::numeric" in shape
    assert "wanted='integer' AND v::text !~" in shape


def test_actual_bootstrap_retains_float_and_boolean_values(tmp_path):
    from pytest import MonkeyPatch

    from tests.openevolve.test_operational_native_process import case

    with MonkeyPatch.context() as patches:
        value = case.__wrapped__(patches, tmp_path).launch.bootstrap
    parsed = OperationalBootstrap.model_validate_json(json.dumps(value))
    assert parsed.model_dump(mode="json") == value
    market = value["native_inputs"]["market_profile"]
    assert type(market["initial_cash"]) is float
    assert market["commission_rate"] == 0.0005
    assert market["stamp_tax_rate"] == 0.001
    assert market["slippage"]["value"] == 0.0005
    assert market["t_plus_one"] is True
    altered = copy.deepcopy(value)
    altered["native_inputs"]["market_profile"]["initial_cash"] = 0
    import pytest

    with pytest.raises(ValueError):
        OperationalBootstrap.model_validate_json(json.dumps(altered))


def typed_corpus(tmp_path):
    """Full actual model serializations, not a fabricated successful DB graph."""
    from uuid import uuid4

    from pytest import MonkeyPatch

    from packages.openevolve_adapter.upload_contracts import FIXED, Begin, File
    from tests.openevolve.operational_fixtures import records as controller_records
    from tests.openevolve.test_native_evaluator_journal import records as native_records
    from tests.openevolve.test_operational_native_process import case

    with MonkeyPatch.context() as patches:
        bootstrap = case.__wrapped__(patches, tmp_path).launch.bootstrap
    cl, ci, cc, cs = controller_records(tmp_path)
    nl, ni, ns, ne = native_records()
    begin = Begin(
        token=uuid4(),
        files=tuple(
            File(name=n, size_bytes=1, sha256="a" * 64)
            for n in sorted(FIXED | {"programs/" + str(uuid4()) + ".json"})
        ),
    )
    values = [
        ("bootstrap", OperationalBootstrap, bootstrap),
        ("begin", Begin, begin.model_dump(mode="json")),
    ]
    for profile, kind, obj in (
        (cl.intent.admission.profile, "launch_intent", cl.intent),
        (cl.intent.admission.profile, "launch_identity", ci),
        (cl.intent.admission.profile, "controller_child", cc),
        (cl.intent.admission.profile, "stop", cs),
        (nl.intent.admission.profile, "launch_intent", nl.intent),
        (nl.intent.admission.profile, "launch_identity", ni),
        (nl.intent.admission.profile, "stop", ns),
        (nl.intent.admission.profile, "extraction", ne),
    ):
        values.append(((profile, kind), type(obj), obj.model_dump(mode="json")))
    return values


def postgres_probe_sql(tmp_path):
    """Reproducible complete DTO probes for the separate real PostgreSQL gate."""
    rows = []

    def literal(value):
        return (
            "'"
            + json.dumps(value, separators=(",", ":")).replace("'", "''")
            + "'::jsonb"
        )

    def call(kind, value):
        if isinstance(kind, tuple):
            return f"public.mineagent_oe_process_payload('{kind[0]}','{kind[1]}',{literal(value)})"
        return f"public.mineagent_oe_operational_shape('{kind}',{literal(value)})"

    def emit(name, expected, expression):
        rows.append(
            f"SELECT '{name}' AS probe, {str(expected).lower()} AS expected, {expression} AS actual;"
        )

    def leaves(value, path=()):
        if isinstance(value, dict):
            for key, child in value.items():
                yield from leaves(child, path + (key,))
        elif isinstance(value, list):
            for key, child in enumerate(value):
                yield from leaves(child, path + (key,))
        else:
            yield path, value

    for kind, model, value in typed_corpus(tmp_path):
        name = model.__name__
        emit(name, True, call(kind, value))
        bad = copy.deepcopy(value)
        bad["untrusted_extra"] = 1
        emit(name + "_extra", False, call(kind, bad))
        for key in value:
            bad = copy.deepcopy(value)
            del bad[key]
            emit(name + "_missing_" + key, False, call(kind, bad))
        for path, original in leaves(value):
            if type(original) in (int, float, bool):
                bad = copy.deepcopy(value)
                target = bad
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = str(original)
                emit(name + "_type_" + "_".join(map(str, path)), False, call(kind, bad))
    for name, rule, values in (
        (
            "cash",
            {"type": "number", "exclusiveMinimum": 0, "maximum": 1e12},
            [
                (0, False),
                (-0.1, False),
                (0.5, True),
                (1e12, True),
                (1e12 + 0.1, False),
                (True, False),
                ("0.5", False),
            ],
        ),
        (
            "rate",
            {"type": "number", "minimum": 0, "maximum": 0.1},
            [
                (0, True),
                (0.1, True),
                (0.100001, False),
                (-0.0001, False),
                (False, False),
            ],
        ),
        (
            "time",
            {"type": "string", "format": "date-time"},
            [
                ("2026-10-27T05:00:00Z", True),
                ("2026-10-27T05:00:00.123456Z", True),
                ("2026-02-30T05:00:00Z", False),
                ("2026-10-27T25:00:00Z", False),
                ("2026-10-27T05:00:00+00:00", False),
                ("2026-10-27T05:00:00.000000Z", False),
            ],
        ),
    ):
        for value, expected in values:
            emit(
                name + "_" + str(value),
                expected,
                f"public.mineagent_oe_shape({literal(value)},{literal(rule)})",
            )
    return "\n".join(rows) + "\n"
