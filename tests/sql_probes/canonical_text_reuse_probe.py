"""Pure invocation-local text reuse experiment. Never runtime or migration SQL."""

import hashlib
import json
import re
from pathlib import Path

from backend.app.row_validation_oe_032g_base_sql import literal
from script import heartbeat_graph_inventory as inventory

WORKFLOW = "mineagent_rd_workflow_config"
PROVIDER = "mineagent_rd_provider_config_026_frozen"
CHANGED = (WORKFLOW, PROVIDER)
DEPENDENCIES = (
    *CHANGED,
    "mineagent_rd_canonical_json",
    "mineagent_execution_metadata_sha",
)
PIN_SHA256 = "64accb94d898545b20440230909aa4a1a5ccfceff98d1eaea87cfecc4dafea9b"


def pins():
    raw = Path(__file__).with_name("canonical_text_reuse_pins.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != PIN_SHA256:
        raise ValueError("Exact pure canonical reuse manifest required")
    return json.loads(raw)


def original(name):
    if name not in CHANGED:
        raise ValueError("Closed pure candidate function set required")
    file = "029_research_workflow.py" if name == WORKFLOW else "027_rd_controller_v3.py"
    found = re.findall(
        r"CREATE(?: OR REPLACE)? FUNCTION @SCHEMA@\." + name + r"\(.*?\$\$;",
        literal(file),
        re.DOTALL,
    )
    if len(found) != 1:
        raise ValueError("Exact original pure function required")
    return found[0].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


def replace_once(value, old, new):
    if value.count(old) != 1:
        raise ValueError("Pure candidate source boundary changed")
    return value.replace(old, new, 1)


def candidate(name):
    source = original(name)
    if name == WORKFLOW:
        source = replace_once(
            source,
            "base jsonb; x jsonb; w jsonb; b jsonb; s jsonb;",
            "base jsonb; x jsonb; w jsonb; b jsonb; s jsonb; workflow_text text;",
        )
        source = replace_once(
            source,
            " OR octet_length(mineagent_rd_canonical_json(w,true))>32768\n",
            " THEN RETURN false; END IF;\n workflow_text:=mineagent_rd_canonical_json(w,true);\n IF octet_length(workflow_text)>32768\n",
        )
        source = replace_once(
            source,
            "v->>'workflow_inputs_sha256' IS DISTINCT FROM mineagent_execution_metadata_sha(w)",
            "v->>'workflow_inputs_sha256' IS DISTINCT FROM pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(workflow_text,'UTF8')),'hex')",
        )
    else:
        source = replace_once(
            source, "DECLARE x jsonb;", "DECLARE x jsonb; steer_text text;"
        )
        source = replace_once(
            source,
            " OR octet_length(mineagent_rd_canonical_json(v->'steer_snapshot',true))>32768\n",
            " THEN RETURN false; END IF;\n steer_text:=mineagent_rd_canonical_json(v->'steer_snapshot',true);\n IF octet_length(steer_text)>32768\n",
        )
        source = replace_once(
            source,
            "encode(sha256(convert_to(mineagent_rd_canonical_json(v->'steer_snapshot',true),'UTF8')),'hex')",
            "encode(sha256(convert_to(steer_text,'UTF8')),'hex')",
        )
    return source


def _body(name, enabled):
    source = candidate(name) if enabled else original(name)
    return source.split("$$")[1].replace("@SCHEMA@", '"@SCHEMA@"')


def verify_functions(rows, *, candidate_enabled=False):
    expected = pins()["functions"]
    found = {}
    for raw in rows:
        if raw["name"] not in DEPENDENCIES:
            continue
        row = dict(raw)
        if row["name"] in found:
            raise ValueError("Unique pure helper signature required")
        found[row["name"]] = row
    if set(found) != set(DEPENDENCIES):
        raise ValueError("Complete frozen pure helper closure required")
    for name, row in found.items():
        if candidate_enabled and name in CHANGED:
            body, definition = row.pop("raw_body"), row.pop("raw_definition")
            expected_body = _body(name, True)
            if body != expected_body or definition.count(expected_body) != 1:
                raise ValueError("Exact pure candidate body required")
            restored = definition.replace(expected_body, _body(name, False), 1)
            if (
                hashlib.sha256(restored.encode()).hexdigest()
                != expected[name]["definition_sha256"]
            ):
                raise ValueError("Pure function definition attributes changed")
            row["body_sha256"] = expected[name]["body_sha256"]
            row["definition_sha256"] = expected[name]["definition_sha256"]
        else:
            row.pop("raw_body", None)
            row.pop("raw_definition", None)
        if row != expected[name]:
            raise ValueError("Frozen pure function metadata differs")


def check(connection, *, candidate_enabled=False):
    schema = connection.exec_driver_sql("SELECT current_schema()").scalar_one()
    if not re.fullmatch(r"tradegrad_test_[a-f0-9]{32}", schema):
        raise ValueError("Generated disposable schema required")
    if connection.exec_driver_sql("SHOW server_version_num").scalar_one() != "180006":
        raise ValueError("Exact PostgreSQL18.6 required")
    if connection.exec_driver_sql(
        "SELECT version_num FROM alembic_version"
    ).scalars().all() != ["032g_schema_keyset_cost"]:
        raise ValueError("Exact current032g required")
    triggers = connection.exec_driver_sql(inventory.TRIGGERS_QUERY).mappings().all()
    functions = connection.exec_driver_sql(inventory.FUNCTIONS_QUERY).mappings().all()
    summarized = inventory.summarize(schema, triggers, functions)["functions"]
    raw = {row["name"]: row for row in functions if row["name"] in CHANGED}
    for row in summarized:
        if row["name"] in raw:
            row["raw_body"] = raw[row["name"]]["body"].replace(schema, "@SCHEMA@")
            row["raw_definition"] = raw[row["name"]]["definition"].replace(
                schema, "@SCHEMA@"
            )
    verify_functions(summarized, candidate_enabled=candidate_enabled)
    return schema


def install(connection, *, candidate_enabled):
    schema = check(connection, candidate_enabled=not candidate_enabled)
    for name in CHANGED:
        sql = candidate(name) if candidate_enabled else original(name)
        connection.exec_driver_sql(
            sql.replace("@SCHEMA@", '"' + schema + '"'),
            execution_options={"no_parameters": True},
        )
    check(connection, candidate_enabled=candidate_enabled)


def export_cost(measurements):
    import os

    expected = {
        phase + "_" + function + "_" + size
        for phase in ("original", "candidate", "restored")
        for function in ("workflow", "provider")
        for size in ("default", "large_valid")
    }
    if set(measurements) != expected or any(
        type(value) is not int or not 0 <= value <= 3_600_000
        for value in measurements.values()
    ):
        raise ValueError("Closed bounded pure cost cells required")
    report = {
        "samples_per_cell": 8,
        "warmups_per_cell": 1,
        "scope": "SQL_calls_including_savepoint_roundtrips_no_speedup_claim",
        "elapsed_ms": measurements,
    }
    encoded = json.dumps(report, sort_keys=True, allow_nan=False) + "\n"
    if len(encoded.encode()) > 4096:
        raise ValueError("Pure cost report bound exceeded")
    directory = os.environ.get("MINEAGENT_SQL_CANDIDATE_MEASUREMENTS_DIR")
    if directory:
        path = Path(directory)
        if path.resolve() != (Path.cwd() / "var/sql-candidate-proof").resolve():
            raise ValueError("Exact isolated proof artifact directory required")
        path.mkdir(parents=True, exist_ok=True)
        (path / "pure-canonical-cost.json").write_text(encoded)
    return encoded
