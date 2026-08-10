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


def test_investigation_brief_renders_the_whole_story(conn):
    from watchdogdatamodel.readonly import ReadOnly
    from watchdogdatamodel.store.actions import enqueue, finish
    from watchdogdatamodel.store.issues import open_or_touch, resolve
    from watchdogdatamodel.store.runs import finish_run, start_run
    from watchdogdatamodel.store.series import upsert_series

    s = upsert_series(conn, key="z2:load:src", name="Z2 load", unit="MW",
                      labels={"zone": "Z2"})
    run = start_run(conn, scope={"series": "all", "checks": "all"}, trigger="scheduled")
    kw = dict(fingerprint="z2:load:src|gap", origin="check", title="gap",
              series_id=s.id, run_id=run.id, details={"verdict": "stale"})
    first, _ = open_or_touch(conn, actor="run:1", **kw)
    resolve(conn, first.id, reason="recovered", actor="run:1")
    second, _ = open_or_touch(conn, actor="run:2", **kw)   # recurrence w/ lineage
    for n in range(3):
        open_or_touch(conn, actor=f"run:{n+3}", **kw)      # detected_again x3
    a, _ = enqueue(conn, second.id, "backfill", requested_by="rule:auto",
                   params={})
    finish(conn, str(a.id), status="failed", by="w",
           outcome={"result": "not_healed",
                    "log": ["t provider data matches stored — nothing to re-ingest"]})
    finish_run(conn, run.id)

    ro = ReadOnly(DSN)
    md = ro.investigation_brief(str(second.id))
    for header in ("## Issue", "## Timeline", "## Already tried",
                   "## Past incidents", "## Related", "## Data"):
        assert header in md, header
    assert "detected again ×3" in md
    assert "not_healed" in md and "nothing to re-ingest" in md
    assert "recovered" in md          # lineage carries the past resolution
    assert "verdict: stale" in md
    assert "zone=Z2" in md            # labels rendered, no product words in SDK
    # composites are read-only text; summary works too
    assert "## Watchdog summary" in ro.summary()
    assert "## Open issues" in ro.situation(labels={"zone": "Z2"})


def test_work_order_bundles_brief_and_situation(conn):
    from watchdogdatamodel.readonly import ReadOnly
    from watchdogdatamodel.store.issues import open_or_touch
    from watchdogdatamodel.store.series import upsert_series

    s = upsert_series(conn, key="z3:price:src", name="n", labels={"zone": "Z3"})
    issue, _ = open_or_touch(conn, fingerprint="z3:price:src|dvg", origin="check",
                             title="dvg", actor="run:t", series_id=s.id)
    md = ReadOnly(DSN).work_order(str(issue.id))
    brief_at, situation_at = md.index("## Issue"), md.index("## Open issues")
    assert brief_at < situation_at  # one file: the brief, then the board
