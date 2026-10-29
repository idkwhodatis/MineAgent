"""Exact installed030 predecessors; never change already published SQL bytes."""

import hashlib

from backend.app.openevolve_revision_ddl import historical
from backend.app.rdagent_provider_revision import function

PINS = {
    "028": "dbb61d771730685de3cf8cb2fd7eeafe48e2c7c77fa54d57c19d70df2e68f7c9",
    "029": "2848ba2747b703965ad8de8048591ac1931854666cda2f1f6dd512976aaf9ed3",
    "030": "1f587094ed8d30048865a13f77effdf162ec77b8c5488d70a64c7b1e94c38a7e",
}


def predecessor(name):
    for revision in ("030", "029", "028"):
        sql = historical(revision, "UPGRADE_SQL")
        if hashlib.sha256(sql.encode()).hexdigest() != PINS[revision]:
            raise ValueError("Exact031 predecessor bytes required")
        if f"FUNCTION @SCHEMA@.{name}(" in sql:
            return function(sql, name)
    raise ValueError("Unregistered031 predecessor")


def renamed(name, alias):
    sql = predecessor(name)
    old = f"FUNCTION @SCHEMA@.{name}("
    assert sql.count(old) == 1
    return sql.replace(old, f"FUNCTION @SCHEMA@.{alias}(", 1)
