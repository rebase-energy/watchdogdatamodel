import pytest

from tests.dbsupport import requires_db

pytestmark = requires_db


def test_check_upsert(conn):
    from watchdogdatamodel.store.checks import list_checks, upsert_check

    upsert_check(conn, id="freshness", name="Freshness", dimension="freshness")
    upsert_check(conn, id="freshness", name="Freshness v2", dimension="freshness")
    checks = list_checks(conn)
    assert [(c.id, c.name) for c in checks] == [("freshness", "Freshness v2")]


def test_run_lifecycle_and_coverage(conn):
    from watchdogdatamodel.store.runs import finish_run, run_covers, start_run
    from watchdogdatamodel.store.series import upsert_series

    s = upsert_series(conn, key="k1", name="n", labels={"zone": "SE-SE1"})
    run = start_run(conn, scope={"series": "all", "checks": "all"}, trigger="scheduled")
    assert run.status == "running"
    assert not run_covers(run, series=s, check_id="freshness")  # not completed yet
    run = finish_run(conn, run.id, stats={"series_checked": 1})
    assert run.status == "completed" and run.finished_at is not None
    assert run_covers(run, series=s, check_id="freshness")
    with pytest.raises(ValueError):
        finish_run(conn, run.id)  # already finished

    # keys-scoped (targeted) runs cover exactly their declared series —
    # run_covers forwards series.key, so this works without caller plumbing.
    targeted = start_run(conn, scope={"series": {"keys": ["k1"]}, "checks": "all"},
                         trigger="targeted")
    targeted = finish_run(conn, targeted.id, stats={})
    assert run_covers(targeted, series=s, check_id="freshness")
    other = upsert_series(conn, key="k2", name="n2", labels={"zone": "FI"})
    assert not run_covers(targeted, series=other, check_id="freshness")


def test_bad_scope_rejected(conn):
    from watchdogdatamodel.store.runs import start_run

    with pytest.raises(ValueError):
        start_run(conn, scope={"series": "some"}, trigger="manual")
