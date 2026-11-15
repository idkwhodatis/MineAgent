"""Narrow wire-only compatibility for the immutable first migration's comment.

The frozen001 SQL contains one em dash in a transient DO-block comment. LATIN1
cannot encode it. Match the complete original UTF8 hash before changing only
that comment character; no DDL, stored function, literal, or data is rewritten.
Other SQL is untouched. The listener belongs only to this migration connection.
"""

import codecs
import hashlib
from contextlib import contextmanager

from sqlalchemy import event, text
from sqlalchemy.sql.elements import TextClause

FROZEN_SHA256 = "49219a382f84788a31f3c6b928d42131ce11387ac8614e1b3c95c140324aead7"
HEADER = "-- Frozen M1 PostgreSQL schema. Generated from the initial domain metadata."
COMMENT = "        -- Before ignoring, verify the extension actually exists — if it does not exist"


def latin1_m1_statement(statement):
    if not statement.startswith(HEADER):
        return statement
    if (
        hashlib.sha256(statement.encode("utf-8")).hexdigest() != FROZEN_SHA256
        or statement.count(COMMENT) != 1
    ):
        raise ValueError("Exact frozen001 source required for encoding compatibility")
    result = statement.replace(COMMENT, COMMENT.replace("—", "-"), 1)
    # Refuse rather than silently replacing any additional unsupported content.
    result.encode("latin-1", errors="strict")
    return result


def _before_execute(connection, clause, multiparams, params, execution_options):
    if isinstance(clause, TextClause):
        normalized = latin1_m1_statement(clause.text)
        if normalized != clause.text:
            if multiparams or params or clause.compile().params:
                raise ValueError("Frozen001 compatibility cannot change bound SQL")
            clause = text(normalized).execution_options(
                **clause.get_execution_options()
            )
    return clause, multiparams, params


@contextmanager
def legacy_sql_encoding_compatibility(connection):
    if connection.dialect.name != "postgresql":
        yield
        return
    encoding = connection.connection.driver_connection.info.encoding
    if codecs.lookup(encoding).name != "iso8859-1":
        yield
        return
    event.listen(connection, "before_execute", _before_execute, retval=True)
    try:
        yield
    finally:
        event.remove(connection, "before_execute", _before_execute)
