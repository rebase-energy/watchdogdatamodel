"""reclassify(): the only legal kind flip (spec §5.2)."""
import pytest
from tests.dbsupport import requires_db
from watchdogdatamodel import compute_fingerprint
from watchdogdatamodel.store.actions import enqueue
from watchdogdatamodel.store.issues import open_or_touch, reclassify
from watchdogdatamodel.store.series import upsert_series

pytestmark = requires_db


def test_reclassify_flips_kind_and_diaries(conn):
    # check_definition is unseeded in these tests, so check_id must stay None
    # (an established A1 convention) — checks are distinguished by fingerprint.
    s = upsert_series(conn, key="HU:load:1:mavir", name="x", labels={})
    issue, _ = open_or_touch(conn, fingerprint=compute_fingerprint(s.key, "timing_gaps"),
                             origin="check", title="t", actor="test",
                             series_id=s.id, check_id=None, kind="context")
    out = reclassify(conn, issue.id, kind="issue", actor="run:r1",
                     reason="verdicts_changed")
    assert out.kind == "issue" and out.state == "open"
    ev = conn.execute(
        "SELECT data FROM issue_event WHERE issue_id=%s AND type='kind_changed'",
        (issue.id,)).fetchone()
    assert ev["data"] == {"from": "context", "to": "issue", "reason": "verdicts_changed"}


def test_reclassify_leaves_actions_untouched(conn):
    s = upsert_series(conn, key="HU:price:1:entsoe", name="x", labels={})
    issue, _ = open_or_touch(conn, fingerprint=compute_fingerprint(s.key, "x1"),
                             origin="check", title="t", actor="test",
                             series_id=s.id, check_id=None, kind="issue")
    action, _ = enqueue(conn, issue.id, "agent_investigation", requested_by="test")
    reclassify(conn, issue.id, kind="context", actor="test")
    row = conn.execute("SELECT status FROM action WHERE id=%s", (action.id,)).fetchone()
    assert row["status"] == "queued"  # engagement survives the flip


def test_reclassify_rejects_noop_and_bad_kind(conn):
    s = upsert_series(conn, key="HU:x:1:y", name="x", labels={})
    issue, _ = open_or_touch(conn, fingerprint=compute_fingerprint(s.key, "x2"),
                             origin="check", title="t", actor="test",
                             series_id=s.id, check_id=None, kind="issue")
    with pytest.raises(ValueError):
        reclassify(conn, issue.id, kind="issue", actor="test")   # no-op
    with pytest.raises(ValueError):
        reclassify(conn, issue.id, kind="banana", actor="test")  # bad kind
