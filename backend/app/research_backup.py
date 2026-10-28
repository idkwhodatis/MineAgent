"""Revision-pinned neutral evidence closure; no executable restoration or recovery.

Every database row and physical file remains conservative evidence. N1 authorizes
no garbage collection in these namespaces, including valid atomic temporary
files left by a lost publisher. Unknown paths or relationships fail closed.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import text

from backend.app.schema_capabilities import capabilities_for

SCHEMA = "mineagent.research-restore-closure.v1"
SCHEMA_V2 = "mineagent.research-restore-closure.v2"
SCHEMA_V3 = "mineagent.research-restore-closure.v3"
SCHEMA_V4 = "mineagent.research-restore-closure.v4"
SCHEMA_V5 = "mineagent.research-restore-closure.v5"
SCHEMA_V6 = "mineagent.research-restore-closure.v6"
SCHEMA_V7 = "mineagent.research-restore-closure.v7"
SCHEMA_V8 = "mineagent.research-restore-closure.v8"
SCHEMA_V9 = "mineagent.research-restore-closure.v9"
SCHEMA_V10 = "mineagent.research-restore-closure.v10"
NAMESPACES = frozenset({"research-objects", "research-processes"})
SCOPE = ("owner_id", "run_id", "attempt_id", "generation")
# Literal field inventories are intentionally not derived from ORM reflection:
# adding a future table/column requires an explicit closure revision decision.
FIELDS = {
    "research_budget_controls": "id owner_id run_id created_at limits used reserved",
    "research_budget_reservations": "id owner_id run_id attempt_id generation created_at purpose logical_key amounts state",
    "research_budget_settlements": "id owner_id run_id attempt_id generation created_at reservation_id charge outcome",
    "research_operation_contexts": "id owner_id run_id attempt_id generation created_at context context_sha256",
    "research_context_aborts": "id owner_id run_id attempt_id generation created_at context_id reason",
    "research_model_operations": "id owner_id run_id attempt_id generation created_at context_id purpose ordinal request request_object_id request_object_kind request_sha256 state",
    "research_model_transports": "id owner_id run_id attempt_id generation created_at operation_id ordinal reservation_id state",
    "research_model_outcomes": "id owner_id run_id attempt_id generation created_at transport_id outcome usage response_object_id response_object_kind response_sha256",
    "research_objects": "id owner_id object_kind sha256 size_bytes media_type storage_key created_at",
    "research_object_publications": "id owner_id run_id attempt_id generation budget_reservation_id object_kind sha256 size_bytes storage_key peak_bytes created_at",
    "research_object_outcomes": "publication_id owner_id object_id object_kind sha256 journal_sha256 created_at",
    "research_process_admissions": "id owner_id run_id attempt_id generation role logical_operation_id launch_ordinal profile input_sha256 reservation_id image_id ticket_sha256 created_at",
    "research_process_receipts": "id owner_id run_id attempt_id generation admission_id kind payload payload_sha256 created_at",
    "research_process_states": "admission_id owner_id run_id attempt_id generation status updated_at",
}
FIELDS_V2 = {
    **FIELDS,
    "research_process_admissions": FIELDS["research_process_admissions"]
    + " schema_version ipc_policy",
}

FIELDS_V4 = {
    **FIELDS_V2,
    "research_model_operations": FIELDS_V2["research_model_operations"] + " protocol",
}


def closure_fields(schema):
    if schema == SCHEMA:
        return FIELDS
    if schema in {
        SCHEMA_V4,
        SCHEMA_V5,
        SCHEMA_V6,
        SCHEMA_V7,
        SCHEMA_V8,
        SCHEMA_V9,
        SCHEMA_V10,
    }:
        return FIELDS_V4
    if schema in {SCHEMA_V2, SCHEMA_V3}:
        return FIELDS_V2
    raise ValueError("Unsupported neutral research closure")


PRIMARY_KEYS = {
    "research_object_outcomes": "publication_id",
    "research_process_states": "admission_id",
}


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def validate_absence(inventory):
    if any(item["path"].split("/", 1)[0] in NAMESPACES for item in inventory):
        raise ValueError("Neutral files require a neutral closure capability")


def validate_path(key, *, schema=None):
    """Closed file AND directory prefixes, including empty directory inventory."""
    from packages.research_runtime.object_identity import (
        OBJECT_KINDS,
        parse_storage_key,
    )

    if schema in {
        SCHEMA_V4,
        SCHEMA_V5,
        SCHEMA_V6,
        SCHEMA_V7,
        SCHEMA_V8,
        SCHEMA_V9,
        SCHEMA_V10,
    }:
        from packages.rdagent_adapter.object_identity import KIND, parse_storage_key

        OBJECT_KINDS = {**OBJECT_KINDS, KIND: ("json", "application/json")}
    elif schema not in {None, SCHEMA, SCHEMA_V2, SCHEMA_V3}:
        raise ValueError("Unsupported research path grammar revision")
    if schema in {SCHEMA_V6, SCHEMA_V7, SCHEMA_V8, SCHEMA_V9, SCHEMA_V10}:
        from packages.rdagent_adapter.execution_object_identity import (
            KINDS,
            parse_storage_key,
        )

        OBJECT_KINDS = {
            **OBJECT_KINDS,
            **{kind: ("json", "application/json") for kind in KINDS},
        }
    if schema in {SCHEMA_V8, SCHEMA_V9, SCHEMA_V10}:
        from packages.openevolve_adapter.object_identity import KINDS, parse_storage_key

        OBJECT_KINDS = {
            **OBJECT_KINDS,
            **{kind: ("json", "application/json") for kind in KINDS},
        }
    if schema == SCHEMA_V10:
        from packages.rdagent_provider.object_identity import KINDS, parse_storage_key

        OBJECT_KINDS = {
            **OBJECT_KINDS,
            **{kind: ("json", "application/json") for kind in KINDS},
        }
    parts = key.split("/")
    if parts[0] not in NAMESPACES:
        return
    if len(parts) == 1:
        return
    _uuid(parts[1])
    if len(parts) == 2:
        return
    if parts[0] == "research-objects":
        if parts[2] not in {"by-kind", "journals"}:
            raise ValueError("Unknown neutral object directory")
        if len(parts) == 3:
            return
        if parts[2] == "by-kind" and len(parts) == 4 and parts[3] in OBJECT_KINDS:
            return
        parse_storage_key(key)
        return
    _uuid(parts[2])
    if len(parts) == 3:
        return
    if (
        schema in {SCHEMA_V8, SCHEMA_V9, SCHEMA_V10}
        and len(parts) == 5
        and parts[3:] == ["output", "native-export.bin"]
    ):
        return
    if schema in {SCHEMA_V8, SCHEMA_V9, SCHEMA_V10} and parts[3:] == ["manifest.json"]:
        return
    if schema in {SCHEMA_V9, SCHEMA_V10} and parts[3:] in (
        ["output", "result.json"],
        ["inputs"],
        ["inputs", "strategy.json"],
        ["inputs", "market.json"],
    ):
        return
    if parts[3] == "output":
        if len(parts) == 4 or (
            len(parts) == 5
            and parts[4]
            in (
                (
                    {"result.h5", "values.parquet", "rows.json", "analysis.json"}
                    if schema in {SCHEMA_V7, SCHEMA_V8, SCHEMA_V9, SCHEMA_V10}
                    else {"result.h5", "values.parquet", "rows.json"}
                )
                if schema in {SCHEMA_V6, SCHEMA_V7, SCHEMA_V8, SCHEMA_V9, SCHEMA_V10}
                else {"result.h5", "values.parquet"}
            )
        ):
            return
        raise ValueError("Unknown numeric output")
    if parts[3] != "control":
        raise ValueError("Unknown or active neutral process directory")
    if len(parts) == 4:
        return
    allowed_journals = {
        "launch.json",
        "identity.json",
        "stop.json",
        "extraction.json",
    }
    if schema in {SCHEMA_V5, SCHEMA_V6, SCHEMA_V7, SCHEMA_V8, SCHEMA_V9, SCHEMA_V10}:
        allowed_journals.add("child.json")
    if len(parts) != 5 or parts[4] not in allowed_journals:
        raise ValueError("Unknown neutral process journal")


def read_closure(connection, maximum_rows, *, revision, maximum_bytes=32_000_000):
    schema = capabilities_for(revision).research_closure_schema
    if schema not in {
        SCHEMA,
        SCHEMA_V2,
        SCHEMA_V3,
        SCHEMA_V4,
        SCHEMA_V5,
        SCHEMA_V6,
        SCHEMA_V7,
        SCHEMA_V8,
        SCHEMA_V9,
        SCHEMA_V10,
    }:
        raise ValueError("Unsupported neutral research closure capability")
    count = size = 0

    def rows(query):
        nonlocal count, size
        stream = connection.execute(
            text(query).execution_options(stream_results=True, yield_per=1),
            {"limit": maximum_rows + 1},
        ).scalars()
        result = []
        try:
            for row in stream:
                count += 1
                size += len(canonical(row))
                if count > maximum_rows or size > maximum_bytes:
                    raise ValueError("Neutral research closure exceeds its bounds")
                result.append(row)
        finally:
            stream.close()
        return result

    tables = {
        name: rows(
            f"SELECT to_jsonb(t) FROM {name} t ORDER BY {PRIMARY_KEYS.get(name, 'id')} LIMIT :limit"
        )
        for name in FIELDS
    }
    external = {
        "runs": rows(
            "SELECT to_jsonb(t) FROM (SELECT id,owner_id,generation,purpose,engine,status FROM research_runs ORDER BY id) t LIMIT :limit"
        ),
        "attempts": rows(
            "SELECT to_jsonb(t) FROM (SELECT a.id,r.owner_id,a.run_id,a.generation,a.status,a.sandbox_stopped,a.finished_at FROM execution_attempts a JOIN research_runs r ON r.id=a.run_id ORDER BY a.id) t LIMIT :limit"
        ),
    }
    closure = {
        "schema_version": schema,
        "tables": tables,
        "external_bindings": external,
    }
    _indexes(closure, maximum_rows)
    return closure


def _uuid(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError("Invalid neutral closure identity")
    return value


def _indexes(closure, maximum_rows):
    if (
        type(closure) is not dict
        or set(closure) != {"schema_version", "tables", "external_bindings"}
        or closure["schema_version"]
        not in {
            SCHEMA,
            SCHEMA_V2,
            SCHEMA_V3,
            SCHEMA_V4,
            SCHEMA_V5,
            SCHEMA_V6,
            SCHEMA_V7,
            SCHEMA_V8,
            SCHEMA_V9,
            SCHEMA_V10,
        }
    ):
        raise ValueError("Unsupported neutral research closure")
    tables, external = closure["tables"], closure["external_bindings"]
    if (
        type(tables) is not dict
        or set(tables) != set(FIELDS)
        or type(external) is not dict
        or set(external) != {"runs", "attempts"}
    ):
        raise ValueError("Incomplete neutral research closure")
    expected = {
        **closure_fields(closure["schema_version"]),
        "runs": "id owner_id generation purpose engine status",
        "attempts": "id owner_id run_id generation status sandbox_stopped finished_at",
    }
    indexes = {}
    count = 0
    for name, records in {**tables, **external}.items():
        if type(records) is not list:
            raise ValueError("Invalid neutral closure table")
        count += len(records)
        if count > maximum_rows:
            raise ValueError("Oversized neutral closure")
        index = {}
        for row in records:
            if type(row) is not dict or set(row) != set(expected[name].split()):
                raise ValueError("Unknown neutral closure row fields")
            key = PRIMARY_KEYS.get(name, "id")
            _uuid(row[key])
            _uuid(row["owner_id"])
            if row[key] in index:
                raise ValueError("Duplicate neutral closure identity")
            if "generation" in row and (
                type(row["generation"]) is not int or row["generation"] < 1
            ):
                raise ValueError("Invalid neutral closure generation")
            index[row[key]] = row
        indexes[name] = index
    if closure["schema_version"] not in {
        SCHEMA_V3,
        SCHEMA_V4,
        SCHEMA_V5,
        SCHEMA_V6,
        SCHEMA_V7,
        SCHEMA_V8,
        SCHEMA_V9,
        SCHEMA_V10,
    } and any(
        row.get("schema_version", "mineagent.process-admission.v1")
        != "mineagent.process-admission.v1"
        for row in tables["research_process_admissions"]
    ):
        raise ValueError("Numeric process requires closure v3")
    terminal = {"paused", "cancelled", "failed", "interrupted", "succeeded"}
    if any(row["status"] not in terminal for row in external["runs"]):
        raise ValueError("Active runs cannot enter neutral cold closure")
    if any(
        row["status"] not in terminal
        or row["sandbox_stopped"] is not True
        or row["finished_at"] is None
        for row in external["attempts"]
    ):
        raise ValueError("Unstopped attempts cannot enter neutral cold closure")
    for name, records in tables.items():
        for row in records:
            if "run_id" in row:
                run = indexes["runs"].get(row["run_id"])
                if run is None or run["owner_id"] != row["owner_id"]:
                    raise ValueError("Missing or substituted neutral run")
                attempt = indexes["attempts"].get(row.get("attempt_id"))
                if "attempt_id" in row and (
                    attempt is None
                    or any(
                        attempt[field] != row[field]
                        for field in ("owner_id", "run_id", "generation")
                    )
                ):
                    raise ValueError("Missing or substituted neutral run/attempt")
                identity = (run["purpose"], run["engine"])
                allowed = {("legacy_strategy", "legacy")}
                if closure["schema_version"] in {
                    SCHEMA_V4,
                    SCHEMA_V5,
                    SCHEMA_V6,
                    SCHEMA_V7,
                    SCHEMA_V8,
                    SCHEMA_V9,
                    SCHEMA_V10,
                }:
                    allowed.add(("factor_research", "rdagent"))
                if closure["schema_version"] in {SCHEMA_V8, SCHEMA_V9, SCHEMA_V10}:
                    allowed.add(("strategy_search", "openevolve"))
                if closure["schema_version"] in {SCHEMA_V9, SCHEMA_V10}:
                    allowed.add(("factor_strategy", "structured"))
                    if run["engine"] == "structured" and name in {
                        "research_model_operations",
                        "research_operation_contexts",
                    }:
                        raise ValueError(
                            "Structured closed engine cannot have model or policy IPC"
                        )
                if identity not in allowed:
                    raise ValueError("Versioned research identity is not admitted")
                if name == "research_model_operations" and closure[
                    "schema_version"
                ] in {SCHEMA_V8, SCHEMA_V9, SCHEMA_V10}:
                    protocols = {
                        "legacy": {"mineagent.neutral-model.v1"},
                        "rdagent": {
                            "mineagent.rdagent-chat.v1",
                            "mineagent.rdagent-embedding.v1",
                        },
                        "openevolve": {"mineagent.openevolve-chat.v1"},
                    }
                    if closure["schema_version"] == SCHEMA_V10:
                        from packages.rdagent_provider.codec import PROTOCOLS

                        protocols["rdagent"].update(PROTOCOLS)
                    if row["protocol"] not in protocols[run["engine"]]:
                        raise ValueError("Run/model protocol mismatch")
                if (
                    name == "research_model_operations"
                    and closure["schema_version"]
                    in {
                        SCHEMA_V4,
                        SCHEMA_V5,
                        SCHEMA_V6,
                        SCHEMA_V7,
                        SCHEMA_V8,
                        SCHEMA_V9,
                        SCHEMA_V10,
                    }
                    and (
                        (row["protocol"] == "mineagent.neutral-model.v1")
                        != (run["engine"] == "legacy")
                    )
                ):
                    raise ValueError("Run/model protocol mismatch")
                if name == "research_operation_contexts" and closure[
                    "schema_version"
                ] in {
                    SCHEMA_V4,
                    SCHEMA_V5,
                    SCHEMA_V6,
                    SCHEMA_V7,
                    SCHEMA_V8,
                    SCHEMA_V9,
                    SCHEMA_V10,
                }:
                    if run["engine"] == "openevolve" and closure["schema_version"] in {
                        SCHEMA_V8,
                        SCHEMA_V9,
                        SCHEMA_V10,
                    }:
                        if (
                            row["context"].get("schema_version")
                            != "mineagent.openevolve-context.v1"
                        ):
                            raise ValueError("Run/context protocol mismatch")
                        continue
                    expected_context = (
                        "mineagent.neutral-fixture-context.v1"
                        if run["engine"] == "legacy"
                        else "mineagent.rdagent-context.v1"
                    )
                    if row["context"].get("schema_version") != expected_context:
                        raise ValueError("Run/context protocol mismatch")
                if (
                    name == "research_budget_reservations"
                    and row["purpose"] == "engine_mutation"
                    and run["engine"]
                    not in (
                        {"rdagent", "openevolve"}
                        if closure["schema_version"]
                        in {SCHEMA_V8, SCHEMA_V9, SCHEMA_V10}
                        else {"rdagent"}
                    )
                ):
                    raise ValueError("Mutation reservation requires RD identity")
    return indexes


def manifest_keys(closure):
    """Bounded expected JSON readers for retention's pinned inventory scan."""
    _indexes(closure, 100_000)
    tables = closure["tables"]
    keys = {row["storage_key"] for row in tables["research_objects"]}
    for row in tables["research_object_publications"]:
        keys.add(f"research-objects/{row['owner_id']}/journals/{row['id']}.json")
        keys.add(row["storage_key"])
    for row in tables["research_process_admissions"]:
        if closure["schema_version"] in {SCHEMA_V8, SCHEMA_V9, SCHEMA_V10} and row.get(
            "profile"
        ) in {
            "mineagent.openevolve-orchestrator.v1",
            "mineagent.openevolve-native-evaluator.v1",
        }:
            root = f"research-processes/{row['owner_id']}/{row['id']}/control"
            keys.update(
                f"{root}/{name}.json" for name in ("launch", "identity", "stop")
            )
            for receipt in tables["research_process_receipts"]:
                if receipt["admission_id"] == row["id"]:
                    if receipt["kind"] == "controller_child":
                        keys.add(f"{root}/child.json")
                    elif receipt["kind"] == "extraction":
                        keys.add(f"{root}/extraction.json")
            if row["profile"] == "mineagent.openevolve-native-evaluator.v1":
                keys.add(
                    f"research-processes/{row['owner_id']}/{row['id']}/manifest.json"
                )
            # Native export is binary: read_bytes validates it, never read_json.
            continue
        if (
            closure["schema_version"]
            in {SCHEMA_V5, SCHEMA_V6, SCHEMA_V7, SCHEMA_V8, SCHEMA_V9, SCHEMA_V10}
            and row.get("profile") == "mineagent.rdagent-orchestrator.v2"
        ):
            from backend.app.rdagent_controller_backup import (
                manifest_keys as controller_keys,
            )

            keys.update(controller_keys(row))
            continue
        keys.update(
            f"research-processes/{row['owner_id']}/{row['id']}/control/{name}.json"
            for name in (
                ("launch", "identity", "stop", "extraction")
                if row.get("schema_version") == "mineagent.process-admission.v2"
                else ("launch", "identity", "stop")
            )
        )
    if closure["schema_version"] in {
        SCHEMA_V6,
        SCHEMA_V7,
        SCHEMA_V8,
        SCHEMA_V9,
        SCHEMA_V10,
    }:
        for receipt in tables["research_process_receipts"]:
            if (
                receipt["kind"] == "extraction"
                and receipt["payload"].get("schema_version")
                == "mineagent.numeric-process-extraction.v3"
            ):
                keys.add(
                    f"research-processes/{receipt['owner_id']}/{receipt['admission_id']}/output/rows.json"
                )
    if closure["schema_version"] in {SCHEMA_V7, SCHEMA_V8, SCHEMA_V9, SCHEMA_V10}:
        for row in tables["research_process_admissions"]:
            if row.get("profile") == "mineagent.qlib-direct-analysis.v1":
                keys.add(
                    f"research-processes/{row['owner_id']}/{row['id']}/output/analysis.json"
                )
    return keys


def validate_closure(
    closure,
    check_object,
    read_json,
    maximum_rows,
    *,
    inventory,
    read_bytes=None,
    rd_source_closure=None,
    openevolve_closure=None,
    structured_links_pending=False,
    seed_context=None,
):
    if (closure["schema_version"] in {SCHEMA_V9, SCHEMA_V10}) != (
        structured_links_pending is True
    ):
        raise ValueError("Exact structured final links must be independently validated")
    if (
        closure["schema_version"] in {SCHEMA_V8, SCHEMA_V9, SCHEMA_V10}
        and type(openevolve_closure) is not dict
    ):
        raise ValueError("OE v8 requires its full separate typed domain closure")
    if (
        closure["schema_version"] not in {SCHEMA_V8, SCHEMA_V9, SCHEMA_V10}
        and openevolve_closure is not None
    ):
        raise ValueError("Historical research closure cannot acquire OE authority")
    from collections.abc import Mapping

    if closure["schema_version"] == SCHEMA_V10:
        if not isinstance(seed_context, Mapping):
            raise ValueError(
                "Seed context must be reconstructed before research validation"
            )
    elif seed_context is not None:
        raise ValueError("Historical research closure cannot acquire seed authority")
    indexes = _indexes(closure, maximum_rows)
    physical = {entry["path"]: entry for entry in inventory}
    if len(physical) != len(inventory):
        raise ValueError("Duplicate physical inventory entry")
    _validate_objects(
        indexes,
        physical,
        check_object,
        read_json,
        rd_source=closure["schema_version"]
        in {
            SCHEMA_V4,
            SCHEMA_V5,
            SCHEMA_V6,
            SCHEMA_V7,
            SCHEMA_V8,
            SCHEMA_V9,
            SCHEMA_V10,
        },
        rd_execution=closure["schema_version"]
        in {SCHEMA_V6, SCHEMA_V7, SCHEMA_V8, SCHEMA_V9, SCHEMA_V10},
        rd_analysis=closure["schema_version"]
        in {SCHEMA_V7, SCHEMA_V8, SCHEMA_V9, SCHEMA_V10},
        openevolve=closure["schema_version"] in {SCHEMA_V8, SCHEMA_V9, SCHEMA_V10},
        rd_provider=closure["schema_version"] == SCHEMA_V10,
    )
    process_records = _validate_processes(
        indexes,
        physical,
        check_object,
        read_json,
        research_schema=closure["schema_version"],
        read_bytes=read_bytes,
        rd_source_closure=rd_source_closure,
        seed_context=seed_context,
    )
    from backend.app.research_broker_services import validate_accounting_closure

    provider_bindings = None
    if closure["schema_version"] == SCHEMA_V10:
        from packages.rdagent_adapter.persisted_identity import require_source_identity

        if (
            type(rd_source_closure) is not dict
            or rd_source_closure.get("schema_version")
            != "mineagent.rd-source-restore-closure.v5"
        ):
            raise ValueError(
                "Provider accounting requires exact RD-v5 source inventory"
            )
        provider_bindings = {}
        for row in rd_source_closure["external_bindings"]["runs"]:
            values = dict(row)
            values["owner_id"] = UUID(values["owner_id"])
            values["dataset_version_id"] = UUID(values["dataset_version_id"])
            config = require_source_identity(SimpleNamespace(**values))
            if config.model_profile == "configured.rdagent.v1":
                provider_bindings[row["id"]] = config.model_binding

    validate_accounting_closure(
        closure["tables"],
        indexes,
        rd_source=closure["schema_version"]
        in {
            SCHEMA_V4,
            SCHEMA_V5,
            SCHEMA_V6,
            SCHEMA_V7,
            SCHEMA_V8,
            SCHEMA_V9,
            SCHEMA_V10,
        },
        openevolve=closure["schema_version"] in {SCHEMA_V8, SCHEMA_V9, SCHEMA_V10},
        **(
            {"rd_provider": True, "model_bindings": provider_bindings}
            if provider_bindings is not None
            else {}
        ),
    )
    from packages.research_runtime.broker_contracts import (
        RESPONSE,
        canonical_bytes,
        parse_request,
        validate_response,
    )

    for operation in indexes["research_model_operations"].values():
        obj = indexes["research_objects"][operation["request_object_id"]]
        if read_json(obj["storage_key"]) != operation["request"]:
            raise ValueError("Model request bytes differ from immutable operation")
    for outcome in indexes["research_model_outcomes"].values():
        if outcome["outcome"] != "completed":
            continue
        transport = indexes["research_model_transports"][outcome["transport_id"]]
        operation = indexes["research_model_operations"][transport["operation_id"]]
        obj = indexes["research_objects"][outcome["response_object_id"]]
        payload = read_json(obj["storage_key"])
        protocol = operation.get("protocol", "mineagent.neutral-model.v1")
        if closure["schema_version"] == SCHEMA_V10 and protocol in {
            "mineagent.rdagent-provider-chat.v1",
            "mineagent.rdagent-provider-embedding.v1",
        }:
            from packages.rdagent_provider.codec import (
                parse_request as provider_request,
            )
            from packages.rdagent_provider.codec import (
                validate_response as provider_response,
            )
            from packages.rdagent_provider.contracts import (
                RDProviderChatResponse,
                RDProviderEmbeddingResponse,
            )

            response_type = (
                RDProviderChatResponse
                if protocol == "mineagent.rdagent-provider-chat.v1"
                else RDProviderEmbeddingResponse
            )
            response = response_type.model_validate_json(canonical_bytes(payload))
            provider_response(
                protocol, provider_request(protocol, operation["request"]), response
            )
        elif (
            closure["schema_version"] in {SCHEMA_V8, SCHEMA_V9, SCHEMA_V10}
            and protocol == "mineagent.openevolve-chat.v1"
        ):
            from packages.openevolve_adapter.persisted_broker import (
                parse_request as oe_request,
            )
            from packages.openevolve_adapter.persisted_broker import (
                validate_response as oe_response,
            )
            from packages.research_runtime.engine_broker_contracts import OEChatResponse

            response = OEChatResponse.model_validate_json(canonical_bytes(payload))
            oe_response(protocol, oe_request(protocol, operation["request"]), response)
        elif (
            closure["schema_version"]
            in {
                SCHEMA_V4,
                SCHEMA_V5,
                SCHEMA_V6,
                SCHEMA_V7,
                SCHEMA_V8,
                SCHEMA_V9,
                SCHEMA_V10,
            }
            and protocol != "mineagent.neutral-model.v1"
        ):
            from packages.rdagent_adapter.persisted_broker import (
                parse_request as versioned_request,
            )
            from packages.rdagent_adapter.persisted_broker import (
                validate_response as versioned_response,
            )
            from packages.research_runtime.engine_broker_contracts import (
                RDChatResponse,
                RDEmbeddingResponse,
            )

            response_type = (
                RDChatResponse
                if protocol == "mineagent.rdagent-chat.v1"
                else RDEmbeddingResponse
            )
            response = response_type.model_validate_json(canonical_bytes(payload))
            versioned_response(
                protocol, versioned_request(protocol, operation["request"]), response
            )
        else:
            response = RESPONSE.validate_json(canonical_bytes(payload))
            validate_response(parse_request(operation["request"]), response)

    if closure["schema_version"] in {SCHEMA_V8, SCHEMA_V9, SCHEMA_V10}:
        from backend.app.openevolve_backup import validate_closure as validate_search

        validate_search(
            openevolve_closure, closure, process_records, read_bytes, maximum_rows
        )


def _validate_objects(
    indexes,
    physical,
    check,
    read_json,
    *,
    rd_source=False,
    rd_execution=False,
    rd_analysis=False,
    openevolve=False,
    rd_provider=False,
):
    from packages.research_runtime.object_identity import (
        object_identity,
        parse_storage_key,
        publication_journal,
        validate_publication_journal,
    )

    if rd_source:
        from packages.rdagent_adapter.object_identity import (
            object_identity,
            parse_storage_key,
            publication_journal,
            validate_publication_journal,
        )
    if rd_execution:
        from packages.rdagent_adapter.execution_object_identity import (
            object_identity,
            parse_storage_key,
            publication_journal,
            validate_publication_journal,
        )

    if openevolve:
        from packages.openevolve_adapter.object_identity import (
            object_identity,
            parse_storage_key,
            publication_journal,
            validate_publication_journal,
        )

    if rd_provider:
        from packages.rdagent_provider.object_identity import (
            object_identity,
            parse_storage_key,
            publication_journal,
            validate_publication_journal,
        )

    def typed_execution_body(row, payload, *, publication=False):
        if rd_provider and row["object_kind"] == "rd_provider_process_receipt":
            from packages.rdagent_provider.process_receipts import parse_receipt

            document = parse_receipt(canonical(payload))
            for field in SCOPE if publication else ("owner_id",):
                if str(getattr(document, field)) != str(row[field]):
                    raise ValueError("Provider process object scope differs")
            return

        if openevolve:
            from packages.openevolve_adapter.object_identity import (
                KINDS as OE_KINDS,
            )
            from packages.openevolve_adapter.object_identity import (
                parse_document as parse_oe,
            )

            if row["object_kind"] in OE_KINDS:
                document = parse_oe(row["object_kind"], canonical(payload))
                for field in SCOPE if publication else ("owner_id",):
                    if hasattr(document, field) and str(
                        getattr(document, field)
                    ) != str(row[field]):
                        raise ValueError("OE object scope differs")
                return
        if not rd_execution:
            return
        from packages.rdagent_adapter.execution_object_identity import KINDS
        from packages.rdagent_adapter.execution_outcomes import parse_document

        if rd_analysis:
            from packages.rdagent_adapter.analysis_object_codec import parse_document

        if row["object_kind"] not in KINDS:
            return
        raw = canonical(payload)
        if len(raw) != row["size_bytes"] or len(raw) > 65536:
            raise ValueError("Typed execution body size differs")
        doc = parse_document(row["object_kind"], raw).model_dump(mode="json")
        fields = SCOPE if publication else ("owner_id",)
        if any(doc[field] != row[field] for field in fields):
            raise ValueError("Typed execution body scope differs")

    objects = indexes["research_objects"]
    publications = indexes["research_object_publications"]
    outcomes = indexes["research_object_outcomes"]
    allowed = {}
    admitted_bytes = {}
    object_addresses = set()
    reservation_ids = set()
    settlements_by_reservation = defaultdict(list)
    for settlement in indexes["research_budget_settlements"].values():
        settlements_by_reservation[settlement["reservation_id"]].append(settlement)
    journaled_objects = set()
    for row in objects.values():
        identity = object_identity(
            UUID(row["owner_id"]), row["object_kind"], row["sha256"]
        )
        if (
            row["storage_key"] != identity.storage_key
            or row["media_type"] != identity.media_type
            or type(row["size_bytes"]) is not int
            or not 1 <= row["size_bytes"] <= 1048576
        ):
            raise ValueError("Invalid neutral object address")
        check(row["storage_key"], row["sha256"], row["size_bytes"])
        payload = read_json(row["storage_key"])
        if row["storage_key"] in object_addresses or type(payload) is not dict:
            raise ValueError("Duplicate or non-object neutral evidence")
        object_addresses.add(row["storage_key"])
        if hashlib.sha256(canonical(payload)).hexdigest() != row["sha256"]:
            raise ValueError("Neutral object must retain canonical JSON bytes")
        typed_execution_body(row, payload)
    for row in publications.values():
        expected = publication_journal(SimpleNamespace(**row))
        journal = f"research-objects/{row['owner_id']}/journals/{row['id']}.json"
        if (
            type(row["peak_bytes"]) is not int
            or row["peak_bytes"] != row["size_bytes"] * 2 + 8192
        ):
            raise ValueError("Invalid neutral peak reservation")
        reservation = indexes["research_budget_reservations"].get(
            row["budget_reservation_id"]
        )
        if row["budget_reservation_id"] in reservation_ids:
            raise ValueError("Physical publications cannot share a reservation")
        reservation_ids.add(row["budget_reservation_id"])
        if (
            reservation is None
            or reservation["purpose"] != "object_storage"
            or any(reservation[field] != row[field] for field in SCOPE)
            or reservation["amounts"]["storage_bytes"] < row["peak_bytes"]
        ):
            raise ValueError("Neutral publication has no exact peak reservation")
        if reservation["state"] == "settled":
            settlements = settlements_by_reservation[reservation["id"]]
            if (
                len(settlements) != 1
                or settlements[0]["outcome"] != "conservative_unknown"
                or settlements[0]["charge"] != reservation["amounts"]
            ):
                raise ValueError("Neutral physical evidence cannot release its charge")
        elif reservation["state"] not in {"reserved", "uncertain"}:
            raise ValueError("Neutral physical evidence cannot release its reservation")
        admitted_bytes[row["owner_id"]] = (
            admitted_bytes.get(row["owner_id"], 0)
            + reservation["amounts"]["storage_bytes"]
        )
        allowed[journal] = row
        allowed[row["storage_key"]] = row
        if journal in physical:
            raw = canonical(read_json(journal))
            if validate_publication_journal(raw) != json.loads(expected):
                raise ValueError("Neutral publication journal substituted")
            check(journal, hashlib.sha256(expected).hexdigest(), len(expected))
            journaled_objects.add(row["storage_key"])
        outcome = outcomes.get(row["id"])
        if outcome is not None:
            obj = objects.get(outcome["object_id"])
            if (
                obj is None
                or reservation["state"] != "settled"
                or any(
                    outcome[field] != row[field] or obj[field] != row[field]
                    for field in ("owner_id", "object_kind", "sha256")
                )
                or obj["size_bytes"] != row["size_bytes"]
                or outcome["journal_sha256"] != hashlib.sha256(expected).hexdigest()
                or journal not in physical
            ):
                raise ValueError("Neutral published outcome binding mismatch")
    if any(key not in publications for key in outcomes):
        raise ValueError("Neutral object outcome lacks publication")
    if set(objects) - {outcome["object_id"] for outcome in outcomes.values()}:
        raise ValueError("Neutral object lacks immutable publication evidence")
    observed_bytes = {}
    for key, entry in physical.items():
        if not key.startswith("research-objects/"):
            continue
        domain, owner, kind, identity, temporary = parse_storage_key(key)
        if type(entry["size_bytes"]) is not int or entry["size_bytes"] < 0:
            raise ValueError("Invalid neutral physical byte count")
        observed_bytes[str(owner)] = (
            observed_bytes.get(str(owner), 0) + entry["size_bytes"]
        )
        if temporary:
            if domain == "object":
                base = f"research-objects/{owner}/by-kind/{kind}/{identity}.json"
            else:
                base = f"research-objects/{owner}/journals/{identity}.json"
            maximum = 1048576 if domain == "object" else 4096
            if base not in allowed or entry["size_bytes"] > maximum:
                raise ValueError("Unreserved neutral temporary bytes")
        elif key not in allowed:
            raise ValueError("Unreserved neutral physical object")
        elif domain == "object":
            row = allowed[key]
            check(key, row["sha256"], row["size_bytes"])
            payload = read_json(key)
            if (
                type(payload) is not dict
                or hashlib.sha256(canonical(payload)).hexdigest() != row["sha256"]
            ):
                raise ValueError("Orphan object bytes must remain canonical")
            typed_execution_body(row, payload, publication=True)
        if domain == "object":
            base = f"research-objects/{owner}/by-kind/{kind}/{identity}.json"
            if base not in journaled_objects:
                raise ValueError("Object write lacks its prior durable journal")
    if any(
        size > admitted_bytes.get(owner, 0) for owner, size in observed_bytes.items()
    ):
        raise ValueError("Neutral physical bytes exceed durable reservations")


def _validate_processes(
    indexes,
    physical,
    check,
    read_json,
    *,
    research_schema=None,
    read_bytes=None,
    rd_source_closure=None,
    seed_context=None,
):
    from packages.research_runtime.process_contracts import (
        ProcessAdmission,
        ProcessError,
        digest,
        validate_launch_intent,
    )
    from packages.research_runtime.process_contracts import (
        canonical as process_canonical,
    )
    from runtimes.research_supervisor.supervisor import STOP_SCHEMA, validate_identity

    admissions = indexes["research_process_admissions"]
    states = indexes["research_process_states"]
    receipts = indexes["research_process_receipts"]
    if set(states) != set(admissions):
        raise ValueError("Process projection inventory incomplete")
    openevolve_records = {}
    allowed = set()
    reservation_ids = set()
    receipts_by_admission = defaultdict(list)
    for receipt in receipts.values():
        receipts_by_admission[receipt["admission_id"]].append(receipt)
    for row in admissions.values():
        if (
            research_schema in {SCHEMA_V9, SCHEMA_V10}
            and row.get("profile") == "mineagent.factor-strategy-engine.v1"
        ):
            from backend.app.structured_process_backup import (
                validate_structured_closure_unit,
            )

            validate_structured_closure_unit(
                row, indexes, physical, check, read_json, allowed, reservation_ids
            )
            continue
        if research_schema in {SCHEMA_V8, SCHEMA_V9, SCHEMA_V10}:
            from runtimes.openevolve_evaluator.contracts import PROFILE as OE_NATIVE
            from runtimes.openevolve_operational.process_journal import (
                PROFILE as OE_CONTROLLER,
            )

            if row.get("profile") in {OE_CONTROLLER, OE_NATIVE}:
                from backend.app.openevolve_process_backup import validate_process_unit

                openevolve_records[row["id"]] = validate_process_unit(
                    row, indexes, physical, check, read_bytes, allowed, reservation_ids
                )
                continue
        if (
            research_schema
            in {SCHEMA_V5, SCHEMA_V6, SCHEMA_V7, SCHEMA_V8, SCHEMA_V9, SCHEMA_V10}
            and row.get("profile") == "mineagent.rdagent-orchestrator.v2"
        ):
            from backend.app.rdagent_controller_backup import (
                validate_controller_closure_unit,
            )

            if not callable(read_bytes) or type(rd_source_closure) is not dict:
                raise ValueError(
                    "Controller v5 requires actual bytes and RD source closure"
                )
            validate_controller_closure_unit(
                row,
                indexes,
                rd_source_closure,
                physical,
                check,
                read_bytes,
                allowed,
                reservation_ids,
                research_schema=research_schema,
                seed_context=seed_context,
            )
            continue
        if (
            research_schema in {SCHEMA_V7, SCHEMA_V8, SCHEMA_V9, SCHEMA_V10}
            and row.get("profile") == "mineagent.qlib-direct-analysis.v1"
        ):
            from backend.app.analysis_process_backup import (
                validate_analysis_closure_unit,
            )

            validate_analysis_closure_unit(
                row, indexes, physical, check, read_json, allowed, reservation_ids
            )
            continue
        if row.get("schema_version") == "mineagent.process-admission.v2":
            from backend.app.numeric_process_backup import validate_numeric_closure_unit

            validate_numeric_closure_unit(
                row,
                indexes,
                physical,
                check,
                read_json,
                allowed,
                reservation_ids,
                **(
                    {"allow_reports": True}
                    if research_schema
                    in {SCHEMA_V6, SCHEMA_V7, SCHEMA_V8, SCHEMA_V9, SCHEMA_V10}
                    else {}
                ),
            )
            continue
        reservation = indexes["research_budget_reservations"].get(row["reservation_id"])
        if (
            reservation is None
            or reservation["purpose"] != "process_unit"
            or any(reservation[field] != row[field] for field in SCOPE)
            or reservation["amounts"]["process_slots"] < 1
            or reservation["amounts"]["storage_bytes"] < 1179648
            or reservation["amounts"]["wall_milliseconds"] < 60000
            or reservation["id"] in reservation_ids
        ):
            raise ValueError("Neutral process has no exact resource reservation")
        reservation_ids.add(reservation["id"])
        state = states[row["id"]]
        if state["status"] != "stopped" or any(
            state[field] != row[field] for field in SCOPE
        ):
            raise ValueError("Uncontained neutral process")
        children = receipts_by_admission[row["id"]]
        if len(children) != 3 or {r["kind"] for r in children} != {
            "launch_intent",
            "launch_identity",
            "stop",
        }:
            raise ValueError("Process immutable stop evidence incomplete")
        for receipt in children:
            if (
                any(receipt[field] != row[field] for field in SCOPE)
                or digest(receipt["payload"]) != receipt["payload_sha256"]
            ):
                raise ValueError("Process receipt scope/hash mismatch")
        prefix = f"research-processes/{row['owner_id']}/{row['id']}/control/"
        journals = {}
        for name in ("launch", "identity", "stop"):
            key = prefix + name + ".json"
            allowed.add(key)
            if key not in physical:
                raise ValueError("Missing protected process journal")
            journals[name] = read_json(key)
            data = process_canonical(journals[name])
            if len(data) > 16384:
                raise ValueError("Oversized process journal")
            check(key, hashlib.sha256(data).hexdigest(), len(data))
        values = {key: value for key, value in row.items() if key != "created_at"}
        for field in (
            "id",
            "owner_id",
            "run_id",
            "attempt_id",
            "logical_operation_id",
            "reservation_id",
        ):
            values[field] = UUID(values[field])
        try:
            if "schema_version" in values:
                from packages.research_runtime.process_persistence_codec import (
                    require_persistable,
                )

                admission = require_persistable(values)
            else:
                admission = ProcessAdmission(**values)
            launch = journals["launch"]
            if (
                set(launch)
                != {
                    "schema_version",
                    "admission",
                    "intent",
                    "runtime_name",
                    "root_device",
                    "root_inode",
                }
                or launch["schema_version"] != "mineagent.research-process-launch.v1"
                or launch["admission"] != admission.public()
                or launch["runtime_name"] != "mineagent-research-" + row["id"]
                or any(
                    type(launch[field]) is not int or launch[field] < 0
                    for field in ("root_device", "root_inode")
                )
            ):
                raise ValueError("Process launch journal admission mismatch")
            validate_launch_intent(launch["intent"], admission)
            identity = validate_identity(journals["identity"], launch)
            if journals["stop"] != {
                "schema_version": STOP_SCHEMA,
                "launch_sha256": digest(launch),
                "container_id": identity["container_id"],
                "confirmed_absent": True,
            }:
                raise ValueError("Process stop journal incomplete")
            receipts_by_kind = {receipt["kind"]: receipt for receipt in children}
            if (
                receipts_by_kind["launch_intent"]["payload"] != launch["intent"]
                or receipts_by_kind["launch_identity"]["payload"] != identity
                or receipts_by_kind["stop"]["payload"] != journals["stop"]
            ):
                raise ValueError(
                    "Process journals differ from immutable database receipts"
                )
        except ProcessError:
            raise ValueError("Invalid immutable process containment evidence") from None
    if any(r["admission_id"] not in admissions for r in receipts.values()):
        raise ValueError("Process receipt lacks admission")
    if any(
        key.startswith("research-processes/") and key not in allowed for key in physical
    ):
        raise ValueError("Unknown process journal or active IPC socket")

    return openevolve_records
