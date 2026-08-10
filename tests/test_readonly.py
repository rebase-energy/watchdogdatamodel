"""Read-only SDK: reads work, writes are impossible (server-enforced)."""
import psycopg
import pytest

from tests.dbsupport import DSN, requires_db

pytestmark = requires_db


@pytest.fixture()
def ro(conn):
    from watchdogdatamodel.readonly import ReadOnly
    from watchdogdatamodel.store.issues import open_or_touch
    from watchdogdatamodel.store.series import upsert_series

    s = upsert_series(conn, key="z1:price:src", name="n", unit="EUR/MWh",
                      labels={"zone": "Z1"})
    open_or_touch(conn, fingerprint="z1:price:src|gap", origin="check",
                  title="gap", actor="run:t", series_id=s.id, check_id=None)
    return ReadOnly(DSN)


def test_reads_work(ro):
    issues = ro.list_issues(state="open", labels={"zone": "Z1"})
    assert len(issues) == 1 and issues[0]["series_key"] == "z1:price:src"
    detail = ro.get_issue(issues[0]["id"])
    assert [e["type"] for e in detail["events"]] == ["opened"]
    assert ro.get_series("z1:price:src")["labels"]["zone"] == "Z1"
    assert ro.list_checks() == []  # catalog not synced in this fixture — fine


def test_writes_are_impossible(ro):
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        ro._conn.execute("UPDATE issue SET stage = 'x'")
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        ro._conn.execute("INSERT INTO check_definition (id, name) VALUES ('x','x')")
