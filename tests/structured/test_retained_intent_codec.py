"""Execute exact retained-codec expressions, not an entire DB/history proof."""

import ast
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from packages.factors.contracts import canonical_bytes
from runtimes.structured_factor_engine.journal import StructuredIntent
from tests.structured_factor_strategy.test_process_journal import records

ROOT = Path(__file__).resolve().parents[2]


def calls():
    result = []
    for name in ("structured_factor_backup", "structured_factor_history"):
        source = ast.parse((ROOT / "backend/app" / (name + ".py")).read_text())
        for node in ast.walk(source):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "StructuredIntent"
            ):
                result.append((name, ast.Expression(node)))
    assert [name for name, _ in result] == [
        "structured_factor_backup",
        "structured_factor_backup",
        "structured_factor_history",
    ]
    return result


@pytest.mark.parametrize("name,expression", calls())
@pytest.mark.parametrize(
    "change", [None, "uuid", "extra", "bool", "float", "unknown_profile", "deadline"]
)
def test_actual_retained_json_calls_keep_strict_contract(name, expression, change):
    launch, _, _, _ = records()
    raw = launch.intent.model_dump(mode="json")
    # The prior Python-mode call rejects legitimate persisted UUID/timestamp
    # strings; this was not invalid source data or a missing capability.
    with pytest.raises(ValidationError):
        StructuredIntent.model_validate(raw)
    expected = copy.deepcopy(raw)
    if change == "uuid":
        raw["admission"]["id"] = "not-a-uuid"
    elif change == "extra":
        raw["unexpected"] = "never authority"
    elif change == "bool":
        raw["admission"]["generation"] = True
    elif change == "float":
        raw["admission"]["generation"] = 1.5
    elif change == "unknown_profile":
        raw["admission"]["profile"] = "future.profile"
    elif change == "deadline":
        raw["not_after"] = raw["issued_at"]
    context = {
        "StructuredIntent": StructuredIntent,
        "canonical_bytes": canonical_bytes,
        "by_kind": {"launch_intent": {"payload": raw}},
        "intents": [{"payload": raw}],
        "receipts": {"launch_intent": SimpleNamespace(payload=raw)},
    }
    compiled = compile(expression, name, "eval")
    if change is None:
        actual = eval(compiled, context)
        assert actual == launch.intent
        assert actual.model_dump(mode="json") == expected == raw
        assert canonical_bytes(actual) == canonical_bytes(raw)
    else:
        with pytest.raises(ValidationError):
            eval(compiled, context)
