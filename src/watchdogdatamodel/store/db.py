"""Connection + schema bootstrap."""
from importlib.resources import files

import psycopg
from psycopg.rows import dict_row


def connect(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, autocommit=True, row_factory=dict_row)


def bootstrap(conn: psycopg.Connection) -> None:
    """Create all tables/indexes/triggers. Idempotent by construction."""
    ddl = (files("watchdogdatamodel") / "schema.sql").read_text()
    conn.execute(ddl)
