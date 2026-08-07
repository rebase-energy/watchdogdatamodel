import psycopg
import pytest

from tests.dbsupport import requires_db

pytestmark = requires_db


def _issue(conn):
    from watchdogdatamodel.store.issues import open_or_touch

    issue, _ = open_or_touch(
        conn, fingerprint="k|gap", origin="check", title="gap", actor="run:test"
    )
    return issue


def test_enqueue_is_idempotent_per_issue_and_type(conn):
    from watchdogdatamodel.store.actions import enqueue
    from watchdogdatamodel.store.issues import list_events

    issue = _issue(conn)
    a1, created1 = enqueue(conn, issue.id, "backfill", requested_by="rule:auto")
    a2, created2 = enqueue(conn, issue.id, "backfill", requested_by="rule:auto")
    assert created1 and not created2 and a1.id == a2.id
    reqs = [e for e in list_events(conn, issue.id) if e.type == "action_requested"]
    assert len(reqs) == 1 and reqs[0].action_id == a1.id


def test_claim_transition_finish_freeze(conn):
    from watchdogdatamodel.store.actions import claim_next, enqueue, finish
    from watchdogdatamodel.store.issues import list_events

    issue = _issue(conn)
    a, _ = enqueue(conn, issue.id, "backfill", requested_by="rule:auto")
    assert claim_next(conn, "notify", worker="w1") is None
    running = claim_next(conn, "backfill", worker="w1")
    assert running.id == a.id and running.status == "running" and running.started_at
    done = finish(conn, a.id, status="succeeded", by="w1", outcome={"rows": 42})
    assert done.status == "succeeded" and done.finished_at and done.outcome == {"rows": 42}
    assert [t["status"] for t in done.transitions] == ["queued", "running", "succeeded"]
    with pytest.raises(ValueError):
        finish(conn, a.id, status="failed", by="w1")  # already terminal (API)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE action SET status = 'queued' WHERE id = %s", (a.id,))  # DB freeze
    finishes = [e for e in list_events(conn, issue.id) if e.type == "action_finished"]
    assert len(finishes) == 1 and finishes[0].data["status"] == "succeeded"


def test_after_terminal_a_new_action_can_be_enqueued(conn):
    from watchdogdatamodel.store.actions import enqueue, finish

    issue = _issue(conn)
    a, _ = enqueue(conn, issue.id, "backfill", requested_by="rule:auto")
    finish(conn, a.id, status="failed", by="w1")
    b, created = enqueue(conn, issue.id, "backfill", requested_by="user:davide")
    assert created and b.id != a.id
