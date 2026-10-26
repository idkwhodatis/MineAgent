"""Real PG fragment tests only; not application integration/activation acceptance."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.app.process_admission_ddl_draft import SQL

pytestmark = pytest.mark.postgres

# Minimal relational harness isolates this fragment. Full real revision upgrade,
# FKs, ownership, trigger interaction and cold restore remain separate obligations.
CREATE = """CREATE TABLE research_process_admissions (
 id integer PRIMARY KEY, generation integer NOT NULL CHECK (generation > 0),
 launch_ordinal integer NOT NULL CHECK (launch_ordinal > 0),
 role varchar(40) NOT NULL, profile varchar(60) NOT NULL,
 input_sha256 varchar(64) NOT NULL, image_id varchar(71) NOT NULL,
 ticket_sha256 varchar(64) NOT NULL,
 CONSTRAINT ck_research_process_profile CHECK (role='neutral_fixture' AND profile='neutral_fixture.v1'),
 CONSTRAINT ck_research_process_hashes CHECK (input_sha256 ~ '^[a-f0-9]{64}$' AND ticket_sha256 ~ '^[a-f0-9]{64}$' AND image_id ~ '^sha256:[a-f0-9]{64}$')
)"""


def values():
    return {
        "id": 2,
        "generation": 1,
        "launch_ordinal": 1,
        "role": "neutral_fixture",
        "profile": "neutral_fixture.v1",
        "input_sha256": "a" * 64,
        "image_id": "sha256:" + "b" * 64,
        "ticket_sha256": "c" * 64,
        "schema_version": "mineagent.process-admission.v1",
        "ipc_policy": "host_ticket",
    }


def insert(conn, value):
    conn.execute(
        text(
            "INSERT INTO research_process_admissions ("
            + ",".join(value)
            + ") VALUES ("
            + ",".join(":" + key for key in value)
            + ")"
        ),
        value,
    )


@pytest.fixture
def fragment_connection(postgres_engine):
    with postgres_engine.begin() as conn:
        conn.execute(text(CREATE))
        old = values()
        old.pop("schema_version")
        old.pop("ipc_policy")
        old["id"] = 1
        insert(conn, old)
        conn.execute(text(SQL))
        yield conn


def test_backfill_preserves_v1_and_requires_explicit_discriminator(fragment_connection):
    conn = fragment_connection
    old = conn.execute(
        text(
            "SELECT schema_version,ipc_policy,ticket_sha256 FROM research_process_admissions WHERE id=1"
        )
    ).one()
    assert tuple(old) == ("mineagent.process-admission.v1", "host_ticket", "c" * 64)
    insert(conn, values())
    value = values()
    value["id"] = 3
    value.pop("schema_version")
    with pytest.raises(DBAPIError), conn.begin_nested():
        insert(conn, value)


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": None},
        {"ipc_policy": None},
        {"ticket_sha256": None},
        {"profile": "unknown"},
        {"role": "rd_orchestrator"},
        {"input_sha256": None},
        {"image_id": "sha256:bad"},
        {
            "schema_version": "mineagent.process-admission.v2",
            "role": "factor_cutoff_candidate",
            "profile": "disabled.draft.v2",
            "ipc_policy": "none",
            "ticket_sha256": None,
        },
        {
            "schema_version": "mineagent.process-admission.v2",
            "role": "rd_orchestrator",
            "profile": "disabled.draft.v2",
        },
    ],
)
def test_direct_sql_invalid_or_unregistered_rows_fail(fragment_connection, change):
    value = values()
    value.update(change)
    with pytest.raises(DBAPIError), fragment_connection.begin_nested():
        insert(fragment_connection, value)


@pytest.mark.parametrize(
    "change",
    [
        {"ipc_policy": None},
        {"ipc_policy": "host_ticket"},
        {"ticket_sha256": "c" * 64},
        {"role": None},
        {"profile": None},
        {"generation": None},
        {"schema_version": None},
    ],
)
def test_v2_grammar_itself_is_null_safe_without_activation_gate(
    fragment_connection, change
):
    # Isolate the predicate in this throwaway test table ONLY. Production fragment
    # always includes the independent unregistered gate, never an opt-out flag.
    conn = fragment_connection
    conn.execute(
        text(
            "ALTER TABLE research_process_admissions DROP CONSTRAINT ck_process_v2_unregistered"
        )
    )
    value = values()
    value.update(
        schema_version="mineagent.process-admission.v2",
        role="factor_cutoff_candidate",
        profile="disabled.draft.v2",
        ipc_policy="none",
        ticket_sha256=None,
    )
    insert(conn, value)
    value["id"] = 3
    value.update(change)
    with pytest.raises(DBAPIError), conn.begin_nested():
        insert(conn, value)
