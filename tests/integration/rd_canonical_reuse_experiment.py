"""Test-only second candidate: reuse workflow-input canonical text per call.

No installer is invoked on import. The gated disposable installer validates the
actual helper bodies/catalog rather than inferring installation from source pins.
"""

import re
from hashlib import sha256

from backend.app.openevolve_revision_ddl import _function, historical
from tests.integration.rd_hash_reuse_experiment import PINS, previous_function

FUNCTION = "mineagent_rd_workflow_config"
HELPER_PINS = {
    (
        "018",
        "UPGRADE_SQL",
    ): "7aab728d7e5300e8d8718663276b8cba8c7262f38af56230a0d463297f596bc3",
    (
        "021",
        "NORMALIZER_RESULT_SQL",
    ): "9c0665b463bbc493e22c57659b52b99a62e00738680feac8ef8a648c8cd99de0",
}
DECLARATION = " base jsonb; x jsonb; w jsonb; b jsonb; s jsonb;"
LOCALS = DECLARATION + " w_canonical text;"
BOUND = " OR octet_length(mineagent_rd_canonical_json(w,true))>32768\n"
SPLIT = " THEN RETURN false; END IF;\n w_canonical:=mineagent_rd_canonical_json(w,true);\n IF octet_length(w_canonical)>32768\n"
HASH = "mineagent_execution_metadata_sha(w)"
REUSED_HASH = "pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(w_canonical,'UTF8')),'hex')"


def pinned_sources():
    # Revalidate all existing029..032c pins plus both exact pure helper literals.
    previous_function()
    sources = {key: historical(*key) for key in HELPER_PINS}
    for key, expected in HELPER_PINS.items():
        if sha256(sources[key].encode()).hexdigest() != expected:
            raise ValueError("Exact canonical helper SQL changed")
    for revision in PINS:
        if revision != "029" and re.search(
            r"CREATE (?:OR REPLACE )?FUNCTION (?:@SCHEMA@\.)?" + FUNCTION + r"\(",
            historical(revision, "UPGRADE_SQL"),
        ):
            raise ValueError("Unexpected workflow config replacement")
    return sources


def original_sql():
    pinned_sources()
    return _function(historical("029", "UPGRADE_SQL"), FUNCTION)


def candidate_sql():
    old = original_sql()
    for expression in (DECLARATION, BOUND, HASH):
        if old.count(expression) != 1:
            raise ValueError("Exact canonical expression layout changed")
    return (
        old.replace(DECLARATION, LOCALS)
        .replace(BOUND, SPLIT)
        .replace(HASH, REUSED_HASH)
    )


def helper_sql():
    sources = pinned_sources()
    metadata = sources[("021", "NORMALIZER_RESULT_SQL")]
    metadata = metadata[: metadata.index("$$;") + 3].strip()
    return {
        "mineagent_execution_metadata_sha": metadata,
        "mineagent_rd_canonical_json": _function(
            sources[("018", "UPGRADE_SQL")], "mineagent_rd_canonical_json"
        ),
    }


def verify_installed_helpers(connection):
    """Read-only fail-closed check for a future full032c disposable installer."""
    from sqlalchemy import text

    from tests.integration.rd_hash_reuse_experiment import CATALOG, _body

    query = text(
        str(CATALOG).replace(
            "p.prosupport::text",
            "p.prosupport::text, pg_get_function_arguments(p.oid), p.provariadic=0, p.proargmodes, p.proallargtypes",
        )
    )

    expected = {
        "mineagent_execution_metadata_sha": ("sql", "3802", ["v"]),
        "mineagent_rd_canonical_json": (
            "plpgsql",
            "3802 16 23",
            ["v", "ascii_only", "depth"],
        ),
    }
    for name, sql in helper_sql().items():
        rows = connection.execute(query, {"name": name}).all()
        if len(rows) != 1:
            raise ValueError("Exact installed canonical helper identity required")
        row = tuple(rows[0])
        language, args, names = expected[name]
        arguments = (
            "v jsonb"
            if name == "mineagent_execution_metadata_sha"
            else "v jsonb, ascii_only boolean, depth integer DEFAULT 0"
        )
        if (
            row[0] != _body(sql.replace("@SCHEMA@", '"public"'))
            or row[1:15]
            != (
                language,
                "i",
                "u",
                True,
                False,
                False,
                False,
                "f",
                100,
                0,
                ["search_path=pg_catalog"],
                "text",
                args,
                names,
            )
            or row[16] is not None
            or row[18] != "-"
            or row[19:] != (arguments, True, None, None)
        ):
            raise ValueError("Installed canonical helper body or catalog drift")


def verify_installed_config(connection, expected_sql):
    """Exact immutable boolean predicate catalog and body, no DDL."""
    from sqlalchemy import text

    from tests.integration.rd_hash_reuse_experiment import CATALOG, _body

    query = text(
        str(CATALOG).replace(
            "p.prosupport::text",
            "p.prosupport::text, pg_get_function_arguments(p.oid), p.provariadic=0, p.proargmodes, p.proallargtypes",
        )
    )
    rows = connection.execute(query, {"name": FUNCTION}).all()
    if len(rows) != 1:
        raise ValueError("Exact installed workflow config identity required")
    row = tuple(rows[0])
    if (
        row[0] != _body(expected_sql)
        or row[1:15]
        != (
            "plpgsql",
            "i",
            "u",
            False,
            False,
            False,
            False,
            "f",
            100,
            0,
            ["search_path=public, pg_catalog"],
            "boolean",
            "3802",
            ["v"],
        )
        or row[16] is not None
        or row[18:] != ("-", "v jsonb", True, None, None)
    ):
        raise ValueError("Installed workflow config body or catalog drift")
    return row


def provenance(selected):
    from tests.integration.rd_hash_reuse_experiment import _body

    if selected not in ("control", "candidate"):
        raise ValueError("Explicit canonical experiment mode required")
    old = original_sql()
    return {
        "canonical_reuse": selected == "candidate",
        "canonical_config_original_sha256": sha256(_body(old).encode()).hexdigest(),
        "canonical_config_installed_sha256": sha256(
            _body(candidate_sql() if selected == "candidate" else old).encode()
        ).hexdigest(),
        "canonical_helpers_body_sha256": {
            name: sha256(
                _body(sql.replace("@SCHEMA@", '"public"')).encode()
            ).hexdigest()
            for name, sql in helper_sql().items()
        },
        "canonical_helpers_source_sha256": {
            revision + ":" + literal: value
            for (revision, literal), value in HELPER_PINS.items()
        },
    }
