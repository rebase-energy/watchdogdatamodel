from tests.dbsupport import requires_db
from tests.test_wire import hourly

pytestmark = requires_db


def _mk(conn, key="se-se1-production-entsoe", zone="SE-SE1"):
    from watchdogdatamodel.store.series import upsert_series

    return upsert_series(
        conn, key=key, name="SE-SE1 production (entsoe)", unit="MW", frequency="PT1H",
        data_type="OBSERVATION", labels={"zone": zone, "source": "entsoe"},
    )


def test_upsert_insert_then_update(conn):
    from watchdogdatamodel.store.series import get_series, upsert_series

    s1 = _mk(conn)
    s2 = upsert_series(conn, key=s1.key, name="renamed", unit="MW")
    assert s2.id == s1.id and get_series(conn, s1.key).name == "renamed"


def test_list_by_label_containment(conn):
    from watchdogdatamodel.store.series import list_series

    _mk(conn)
    _mk(conn, key="fi-production-fingrid", zone="FI")
    hits = list_series(conn, labels={"zone": "SE-SE1"})
    assert [s.key for s in hits] == ["se-se1-production-entsoe"]


def test_timedatamodel_round_trip(conn):
    from watchdogdatamodel.store.series import series_to_timeseries

    ts = series_to_timeseries(_mk(conn))
    assert not ts.has_df
    assert (ts.name, ts.unit, str(ts.frequency)) == ("SE-SE1 production (entsoe)", "MW", "PT1H")


def test_snapshot_latest_only_and_round_trip(conn):
    from watchdogdatamodel.store.series import (
        get_snapshot, snapshot_timeseries, upsert_snapshot,
    )

    s = _mk(conn)
    upsert_snapshot(conn, series_id=s.id, ts=hourly(n=4))
    upsert_snapshot(conn, series_id=s.id, ts=hourly(n=6))
    n = conn.execute("SELECT count(*) AS c FROM series_snapshot").fetchone()["c"]
    assert n == 1
    snap = get_snapshot(conn, s.id)
    assert snapshot_timeseries(snap).num_rows == 6
