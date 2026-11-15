"""Select the exact historical revision before disposable SQL proof admission."""

from pathlib import Path

from alembic import command
from alembic.config import Config

PREDECESSOR = "032f_shared_validation_cost"
SUCCESSOR = "032g_schema_keyset_cost"


def select_predecessor(connection):
    """Pin only a fresh isolated test schema, never already-admitted history."""
    revisions = connection.exec_driver_sql(
        "SELECT version_num FROM alembic_version"
    ).all()
    assert revisions in ([(PREDECESSOR,)], [(SUCCESSOR,)])
    if revisions == [(SUCCESSOR,)]:
        root = Path(__file__).resolve().parents[2]
        config = Config(str(root / "backend/alembic.ini"))
        config.set_main_option("script_location", str(root / "backend/alembic"))
        config.attributes["connection"] = connection
        command.downgrade(config, PREDECESSOR)
    assert connection.exec_driver_sql(
        "SELECT version_num FROM alembic_version"
    ).all() == [(PREDECESSOR,)]
