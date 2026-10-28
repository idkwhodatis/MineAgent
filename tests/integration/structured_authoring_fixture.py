"""Production assembled bytes for the exact 027a SQL/canonical boundary."""

import copy
import json

from packages.factors.contracts import canonical_bytes
from packages.structured_factor_strategy.assembly import assemble_input
from packages.structured_factor_strategy.wire import read_input, write_input
from tests.structured.test_services import material


def authoring_cases():
    value = assemble_input(**material())
    actual = json.loads(write_input(value))
    authored = copy.deepcopy(actual)
    manifest = authored["engine"]["manifest"]
    manifest["authoring"] = {
        "source_kind": "user_authored",
        "parameter_schema": manifest["state_schema"],
        "initial_state": authored["engine"]["initial_state"],
        "evidence": "user_declared_not_code_verified",
    }
    cases = [
        ("missing_canonical", actual, True),
        ("portable_authoring_forbidden", authored, False),
    ]
    for name, path, replacement in (
        ("null", ("engine", "manifest", "authoring"), None),
        ("false", ("engine", "manifest", "authoring"), False),
        ("number", ("engine", "manifest", "authoring"), 1),
        ("text", ("engine", "manifest", "authoring"), "user_authored"),
        ("array", ("engine", "manifest", "authoring"), []),
        ("empty_object", ("engine", "manifest", "authoring"), {}),
        ("unknown_manifest", ("engine", "manifest", "unexpected"), 1),
        ("invalid_uuid", ("owner_id",), "not-a-uuid"),
        ("invalid_weight", ("program", "maximum_weight"), 2.0),
        (
            "invalid_authoring_source",
            ("engine", "manifest", "authoring", "source_kind"),
            "trusted",
        ),
        (
            "invalid_authoring_evidence",
            ("engine", "manifest", "authoring", "evidence"),
            "verified",
        ),
        ("extra_authoring", ("engine", "manifest", "authoring", "extra"), 1),
    ):
        obj = copy.deepcopy(authored)
        parent = obj
        for key in path[:-1]:
            parent = parent[key]
        parent[path[-1]] = replacement
        cases.append((name, obj, False))
    return cases


def authoring_statements():
    statements = []
    for name, document, expected in authoring_cases():
        raw = canonical_bytes(document)
        accepted = True
        try:
            read_input(raw)
        except ValueError:
            accepted = False
        if accepted != expected:
            raise AssertionError(f"Python canonical boundary differs: {name}")
        literal = raw.decode("ascii").replace("'", "''")
        statements.append(
            "DO $$ BEGIN IF @SCHEMA@.mineagent_structured_contract('input','"
            + literal
            + "'::jsonb) IS DISTINCT FROM "
            + str(expected).lower()
            + " THEN RAISE EXCEPTION 'Structured authoring case differs: "
            + name
            + "'; END IF; END $$;"
        )
    statements.append("SELECT 'STRUCTURED_AUTHORING_MATRIX_COMPLETE';")
    return statements
