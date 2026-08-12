"""Protocol enforcement: context findings are observations, not work (A4).

enqueue refuses to queue work on a context issue unless explicitly overridden;
claim_next (both the store primitive and the tracker wrapper) never serves a
context-issue action; and the max_inflight budget counts only actionable
(kind='issue') running actions, so context-issue engagements never spend it.
"""
import pytest

from tests.dbsupport import requires_db
from watchdogdatamodel import compute_fingerprint, trackers
from watchdogdatamodel.store.actions import enqueue
from watchdogdatamodel.store.issues import open_or_touch
from watchdogdatamodel.store.series import upsert_series

pytestmark = requires_db


def _issue(conn, key, check, kind):
    s = upsert_series(conn, key=key, name="x", labels={})
    issue, _ = open_or_touch(conn, fingerprint=compute_fingerprint(key, check),
                             origin="check", title="t", actor="test",
                             series_id=s.id, check_id=None, kind=kind)
    return issue


def test_enqueue_rejects_context_by_default(conn):
    ctx = _issue(conn, "DE:load:1:sm", "gross_range", "context")
    with pytest.raises(ValueError, match="not actionable"):
        enqueue(conn, ctx.id, "backfill", requested_by="test")
    a, created = enqueue(conn, ctx.id, "backfill", requested_by="test",
                         allow_context=True)
    assert created


def test_claim_next_skips_context_actions(conn):
    ctx = _issue(conn, "DE:x:1:a", "c1", "context")
    enqueue(conn, ctx.id, "job", requested_by="t", allow_context=True)
    real = _issue(conn, "DE:y:1:b", "c2", "issue")
    enqueue(conn, real.id, "job", requested_by="t")
    claimed = trackers.claim_next(conn, "job", worker="w1")
    assert claimed is not None and claimed.issue_id == real.id
    assert trackers.claim_next(conn, "job", worker="w1") is None  # ctx never served


def test_max_inflight_counts_only_actionable(conn):
    ctx = _issue(conn, "DE:z:1:c", "c3", "context")
    a, _ = enqueue(conn, ctx.id, "job2", requested_by="t", allow_context=True)
    conn.execute("UPDATE action SET status='running' WHERE id=%s", (a.id,))
    real = _issue(conn, "DE:w:1:d", "c4", "issue")
    enqueue(conn, real.id, "job2", requested_by="t")
    # one context action running; budget 1 must still admit the real one
    assert trackers.claim_next(conn, "job2", worker="w", max_inflight=1) is not None
