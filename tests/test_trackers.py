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


def test_multiple_deliverables_attach_without_finishing(action):
    conn, aid, iid = action
    assert trackers.add_deliverable(conn, aid, ref={"id": 1, "url": "x/1"})
    assert not trackers.add_deliverable(conn, aid, ref={"id": 1, "url": "x/1"})  # dedup
    assert trackers.add_deliverable(conn, aid, ref={"id": 2, "url": "x/2"})
    row = conn.execute("SELECT status, outcome->'deliverables' d FROM action "
                       "WHERE id=%s", (aid,)).fetchone()
    assert row["status"] == "running" or row["status"] == "queued"  # engagement continues
    assert [d["id"] for d in row["d"]] == [1, 2]


def test_stamp_correlation_no_keywords():
    import uuid
    u = str(uuid.uuid4())
    assert trackers.find_stamp(f"Some PR body\n{trackers.stamp(u)}\n") == u
    assert trackers.find_stamp("mentions #99 and closes #100") is None


def test_reconcile_grace_window_covers_recently_resolved(action):
    from watchdogdatamodel.store.issues import resolve

    conn, aid, iid = action
    trackers.record_external_change(conn, aid, kind="ticket", state="open")
    resolve(conn, iid, reason="recovered", actor="t")
    out = trackers.reconcile_external(
        conn, action_type="agent_investigation",
        fetch_state=lambda a: {"kind": "ticket", "state": "closed"})
    assert out["recovered"] == 1  # resolved issue still got its last look


def test_claim_next_is_the_queue_and_respects_max_inflight(action):
    conn, aid, iid = action  # fixture leaves one queued agent_investigation
    a1 = trackers.claim_next(conn, "agent_investigation", worker="w1",
                             max_inflight=1)
    assert a1 is not None and str(a1.id) == aid and a1.status == "running"
    # a second queued action exists, but the budget is spent -> None
    s = upsert_series(conn, key="k:y:s", name="n", labels={})
    issue2, _ = open_or_touch(conn, fingerprint="k:y:s|c", origin="check",
                              title="t", actor="run:t", series_id=s.id)
    a2q, _ = enqueue(conn, issue2.id, "agent_investigation", requested_by="u",
                     params={})
    assert trackers.claim_next(conn, "agent_investigation", worker="w1",
                               max_inflight=1) is None
    # an external close frees the slot (rule 3); the next claim is FIFO
    trackers.finish_on_external_close(conn, aid, reason="completed")
    a2 = trackers.claim_next(conn, "agent_investigation", worker="w2",
                             max_inflight=1)
    assert a2 is not None and str(a2.id) == str(a2q.id)
    # drained queue -> None, capped or not
    assert trackers.claim_next(conn, "agent_investigation", worker="w2") is None


def test_rule6_diagnosis_is_a_deliverable_and_never_closes(action):
    conn, aid, iid = action
    assert trackers.deliver_findings(conn, aid, ref={"id": 9, "url": "x/c/9"})
    row = conn.execute("SELECT status, outcome FROM action WHERE id=%s",
                       (aid,)).fetchone()
    assert row["status"] == "succeeded"
    assert row["outcome"]["result"] == "diagnosed"
    assert row["outcome"]["findings_url"] == "x/c/9"
    assert [d["id"] for d in row["outcome"]["deliverables"]] == [9]
    assert row["outcome"]["deliverables"][0]["kind"] == "findings"
    # the helper cannot close tickets: nothing in outcome claims a state
    assert "issue_state" not in row["outcome"]
    # terminal now: late findings are refused (diary-only path remains)
    assert not trackers.deliver_findings(conn, aid, ref={"id": 10, "url": "x/c/10"})
