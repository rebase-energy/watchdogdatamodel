"""issue.kind: the one DDL of the watchdog rethink (spec §2.5)."""
import psycopg
import pytest

from tests.dbsupport import requires_db
from watchdogdatamodel import compute_fingerprint
from watchdogdatamodel.store.issues import open_or_touch
from watchdogdatamodel.store.series import upsert_series

pytestmark = requires_db


def _series(conn, key="SE:price:1:entsoe"):
    return upsert_series(conn, key=key, name=key, labels={"zone": "SE"})


def test_kind_defaults_to_issue(conn):
    s = _series(conn)
    issue, created = open_or_touch(
        conn, fingerprint=compute_fingerprint(s.key, "timing_gaps"),
        origin="check", title="t", actor="test", series_id=s.id)
    assert created and issue.kind == "issue"


def test_kind_context_accepted_and_persisted(conn):
    s = _series(conn)
    issue, _ = open_or_touch(
        conn, fingerprint=compute_fingerprint(s.key, "gross_range"),
        origin="check", title="t", actor="test", series_id=s.id, kind="context")
    row = conn.execute("SELECT kind FROM issue WHERE id = %s", (issue.id,)).fetchone()
    assert row["kind"] == "context"


def test_kind_rejects_unknown_value(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO issue (fingerprint, origin, title, kind) "
            "VALUES ('x', 'check', 't', 'garbage')")


def test_touching_open_issue_does_not_change_kind(conn):
    s = _series(conn)
    fp = compute_fingerprint(s.key, "timing_gaps")
    issue, created = open_or_touch(
        conn, fingerprint=fp, origin="check", title="t", actor="test",
        series_id=s.id, kind="context")
    assert created and issue.kind == "context"
    touched, created2 = open_or_touch(
        conn, fingerprint=fp, origin="check", title="t", actor="test",
        series_id=s.id, kind="issue")
    assert not created2 and touched.id == issue.id and touched.kind == "context"
