"""Per-detection observations + kind-aware open accessors (watchdog-rethink design, 2026-08-12)."""
from tests.dbsupport import requires_db
from watchdogdatamodel import compute_fingerprint
from watchdogdatamodel.store.issues import (
    latest_observation, open_actionable, open_context, open_or_touch)
from watchdogdatamodel.store.series import upsert_series

pytestmark = requires_db


def _open(conn, s, check, kind="issue", obs=None):
    # check_definition is unseeded in these tests, so check_id must stay None
    # (an established A1 convention) — checks are distinguished by fingerprint.
    return open_or_touch(
        conn, fingerprint=compute_fingerprint(s.key, check), origin="check",
        title="t", actor="test", series_id=s.id, check_id=None,
        kind=kind, observation=obs)


def test_observation_recorded_on_open_and_touch(conn):
    s = upsert_series(conn, key="FI:load:1:fingrid", name="x", labels={})
    issue, _ = _open(conn, s, "timing_gaps",
                     obs={"severity": "medium", "verdict_summary": {"local_only": 2}})
    _open(conn, s, "timing_gaps",
          obs={"severity": "high", "verdict_summary": {"upstream_confirmed": 4}})
    latest = latest_observation(conn, issue.id)
    assert latest["severity"] == "high"
    assert latest["verdict_summary"] == {"upstream_confirmed": 4}
    n = conn.execute(
        "SELECT count(*) n FROM issue_event WHERE issue_id=%s AND type='observation'",
        (issue.id,)).fetchone()["n"]
    assert n == 2


def test_touch_still_freezes_details(conn):
    s = upsert_series(conn, key="FI:price:1:entsoe", name="x", labels={})
    issue, _ = _open(conn, s, "gross_range")
    open_or_touch(conn, fingerprint=issue.fingerprint, origin="check",
                  title="t", actor="test", series_id=s.id, check_id=None,
                  details={"new": "evidence"})
    row = conn.execute("SELECT details FROM issue WHERE id=%s", (issue.id,)).fetchone()
    assert row["details"] == {}  # frozen at first detection


def test_kind_accessors_partition_open_rows(conn):
    s = upsert_series(conn, key="SE:load:1:entsoe", name="x", labels={})
    actionable, _ = _open(conn, s, "timing_gaps", kind="issue")
    context, _ = _open(conn, s, "gross_range", kind="context")
    assert {r["fingerprint"] for r in open_actionable(conn, series_id=s.id)} == \
        {actionable.fingerprint}
    assert {r["fingerprint"] for r in open_context(conn, series_id=s.id)} == \
        {context.fingerprint}


def test_latest_observation_none_when_never_recorded(conn):
    s = upsert_series(conn, key="NO:load:1:entsoe", name="x", labels={})
    issue, _ = _open(conn, s, "timing_gaps")
    assert latest_observation(conn, issue.id) is None
