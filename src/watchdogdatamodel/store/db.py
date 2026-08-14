"""Connection + schema bootstrap."""
import threading
from contextlib import nullcontext
from importlib.resources import files

import psycopg
from psycopg.rows import dict_row


def connect(dsn: str) -> psycopg.Connection:
    """Open a connection with dict rows + a store-level transaction lock.

    psycopg3 serializes individual statements on a shared connection, but
    interleaved ``conn.transaction()`` blocks from different threads corrupt
    the savepoint nesting. Store functions that transact take this lock, so
    one connection can be shared safely across worker threads.
    """
    conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
    conn.wdm_tx_lock = threading.RLock()  # type: ignore[attr-defined]
    return conn


def tx(conn):
    """The connection's transaction guard (nullcontext for foreign conns)."""
    return getattr(conn, "wdm_tx_lock", None) or nullcontext()


def bootstrap(conn: psycopg.Connection) -> None:
    """Create all tables/indexes/triggers. Idempotent by construction."""
    ddl = (files("watchdogdatamodel") / "schema.sql").read_text(encoding="utf-8")
    conn.execute(ddl)
