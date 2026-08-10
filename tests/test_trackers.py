"""Tracker protocol contract tests — each rule was a prod bug first."""
import pytest

from tests.dbsupport import requires_db
from watchdogdatamodel import trackers
from watchdogdatamodel.store.actions import enqueue
from watchdogdatamodel.store.issues import open_or_touch
from watchdogdatamodel.store.series import upsert_series

pytestmark = requires_db


@pytest.fixture()
def action(conn):
    s = upsert_series(conn, key="k:x:s", name="n", labels={})
    issue, _ = open_or_touch(conn, fingerprint="k:x:s|c", origin="check",
                             title="t", actor="run:t", series_id=s.id)
    a, _ = enqueue(conn, issue.id, "agent_investigation", requested_by="u",
                   params={})
    return conn, str(a.id), str(issue.id)


def test_dedup_and_diary(action):
    conn, aid, iid = action
    assert trackers.record_external_change(conn, aid, kind="issue", state="open")
    for _ in range(3):  # filing burst
        assert not trackers.record_external_change(conn, aid, kind="issue", state="open")
    n = conn.execute("SELECT count(*) AS n FROM issue_event WHERE "
                     "type='external_changed'").fetchone()["n"]
    assert n == 1


def test_close_frees_the_slot_and_terminal_is_diary_only(action):
    conn, aid, iid = action
    assert trackers.finish_on_external_close(conn, aid, reason="completed")
    row = conn.execute("SELECT status, outcome FROM action WHERE id=%s",
                       (aid,)).fetchone()
    assert row["status"] == "succeeded"
    assert row["outcome"]["result"] == "closed_without_deliverable"
    # terminal now: further news never mutates the action, still hits the diary
    assert trackers.record_external_change(conn, aid, kind="pr", state="merged")
    assert conn.execute("SELECT outcome->>'pr_state' AS s FROM action "
                        "WHERE id=%s", (aid,)).fetchone()["s"] is None


def test_reconcile_recovers_lost_news_idempotently(action):
    conn, aid, iid = action
    trackers.deliverable_arrived(conn, aid, links={"pr_number": 7, "pr_state": "open"})
    fetches = []
    def fetch_state(a):
        fetches.append(a["id"])
        return {"kind": "pr", "state": "merged", "data": {"merge_sha": "abc"}}
    out = trackers.reconcile_external(conn, action_type="agent_investigation",
                                      fetch_state=fetch_state)
    assert out == {"checked": 1, "recovered": 1}
    out2 = trackers.reconcile_external(conn, action_type="agent_investigation",
                                       fetch_state=fetch_state)
    assert out2 == {"checked": 1, "recovered": 0}  # diary already knows
