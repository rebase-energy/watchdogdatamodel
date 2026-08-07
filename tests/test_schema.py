from tests.dbsupport import TABLES, requires_db

pytestmark = requires_db


def test_bootstrap_creates_all_tables(conn):
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
    ).fetchall()
    names = {r["table_name"] for r in rows}
    assert set(TABLES) <= names


def test_bootstrap_is_idempotent(conn):
    from watchdogdatamodel.store.db import bootstrap

    bootstrap(conn)  # second run on an already-bootstrapped DB must not raise
