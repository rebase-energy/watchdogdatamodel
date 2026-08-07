import psycopg
import pytest

from tests.dbsupport import requires_db

pytestmark = requires_db

FP = "k1|freshness"


def _open(conn, **kw):
    from watchdogdatamodel.store.issues import open_or_touch

    args = dict(
        fingerprint=FP, origin="check", title="stale data", actor="run:test",
        details={"evidence": {"frozen": True}},
    )
    args.update(kw)
    return open_or_touch(conn, **args)


def test_open_then_touch_dedups_and_keeps_evidence(conn):
    from watchdogdatamodel.store.issues import list_events

    issue, opened = _open(conn)
    assert opened and issue.state == "open"
    touched, opened2 = _open(conn, details={"evidence": {"frozen": False}})
    assert not opened2 and touched.id == issue.id
    assert touched.details["evidence"] == {"frozen": True}  # evidence immutable
    assert touched.last_seen_at >= issue.last_seen_at
    assert [e.type for e in list_events(conn, issue.id)] == ["opened", "detected_again"]


def test_db_constraint_blocks_second_open_row(conn):
    _open(conn)
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO issue (fingerprint, origin, title) VALUES (%s, 'check', 't')", (FP,)
        )


def test_resolution_requires_reason_and_links_recurrence(conn):
    from watchdogdatamodel.store.issues import resolve

    issue, _ = _open(conn)
    with pytest.raises(ValueError):
        resolve(conn, issue.id, reason="because", actor="user:davide")
    resolved = resolve(conn, issue.id, reason="recovered", actor="rule:auto")
    assert resolved.state == "resolved" and resolved.resolved_at is not None
    again, opened = _open(conn)
    assert opened and again.id != issue.id and again.predecessor_id == issue.id


def test_events_append_only_at_db_level(conn):
    from watchdogdatamodel.store.issues import list_events

    issue, _ = _open(conn)
    ev = list_events(conn, issue.id)[0]
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE issue_event SET actor = 'x' WHERE id = %s", (ev.id,))
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM issue_event WHERE id = %s", (ev.id,))


def test_not_seen_stage_and_reopen(conn):
    from watchdogdatamodel.store.issues import (
        list_events, record_not_seen, reopen, resolve, set_stage,
    )

    issue, _ = _open(conn)
    set_stage(conn, issue.id, stage="healing", actor="user:davide")
    record_not_seen(conn, issue.id, run_id=None, actor="run:test")
    resolve(conn, issue.id, reason="recovered", actor="rule:auto")
    back = reopen(conn, issue.id, actor="user:davide", comment="not actually fixed")
    assert back.state == "open"
    types = [e.type for e in list_events(conn, issue.id)]
    assert types == ["opened", "stage_changed", "not_seen", "resolved", "reopened"]
    stage_ev = [e for e in list_events(conn, issue.id) if e.type == "stage_changed"][0]
    assert stage_ev.data == {"from": "new", "to": "healing"}
