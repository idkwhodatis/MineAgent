"""Bounded related SQL rows for the admitted source-only integration fixture.

NOT a verified research closure. Preserve actual active run/attempt/process state;
production research_backup.read_closure must reject this precursor independently.
The mapping is input only to RD's narrower relational/source-byte validator.
"""

from sqlalchemy import text

from backend.app import research_backup


def read_admitted_source_rows(
    connection, fence, *, maximum_rows=10000, maximum_bytes=32000000
):
    if maximum_rows < 1 or maximum_bytes < 1:
        raise ValueError("Invalid narrow fixture bounds")
    count = size = 0
    parameters = {
        "owner": fence.owner_id,
        "run": fence.run_id,
        "limit": maximum_rows + 1,
    }

    def rows(query):
        nonlocal count, size
        stream = connection.execute(
            text(query).execution_options(stream_results=True, yield_per=1), parameters
        ).scalars()
        values = []
        try:
            for value in stream:
                count += 1
                size += len(research_backup.canonical(value))
                if count > maximum_rows or size > maximum_bytes:
                    raise ValueError("Narrow related SQL rows exceed bounds")
                values.append(value)
        finally:
            stream.close()
        return values

    tables = {}
    fields = research_backup.closure_fields(research_backup.SCHEMA_V4)
    publications = "SELECT id FROM research_object_publications WHERE owner_id=:owner AND run_id=:run"
    for name, columns in fields.items():
        where = "owner_id=:owner AND run_id=:run"
        if name == "research_object_outcomes":
            where = f"owner_id=:owner AND publication_id IN ({publications})"
        elif name == "research_objects":
            where = f"""owner_id=:owner AND id IN (
SELECT object_id FROM research_object_outcomes WHERE publication_id IN ({publications})
UNION SELECT request_object_id FROM research_model_operations WHERE owner_id=:owner AND run_id=:run
UNION SELECT response_object_id FROM research_model_outcomes WHERE owner_id=:owner AND run_id=:run)"""
        key = research_backup.PRIMARY_KEYS.get(name, "id")
        tables[name] = rows(
            f"SELECT to_jsonb(t) FROM (SELECT {','.join(columns.split())} FROM {name} WHERE {where} ORDER BY {key} LIMIT :limit) t"
        )
    external = {
        "runs": rows("""SELECT to_jsonb(t) FROM (
SELECT id,owner_id,generation,purpose,engine,status FROM research_runs
WHERE owner_id=:owner AND id=:run ORDER BY id LIMIT :limit) t"""),
        "attempts": rows("""SELECT to_jsonb(t) FROM (
SELECT a.id,r.owner_id,a.run_id,a.generation,a.status,a.sandbox_stopped,a.finished_at
FROM execution_attempts a JOIN research_runs r ON r.id=a.run_id
WHERE r.owner_id=:owner AND r.id=:run ORDER BY a.id LIMIT :limit) t"""),
    }
    # Version names the exact projection grammar required by the RD-only checker;
    # it does not certify shared containment, settlement or cold portability.
    return {
        "schema_version": research_backup.SCHEMA_V4,
        "tables": tables,
        "external_bindings": external,
    }
