"""query.py: reads work, writes are impossible (server-enforced); kind filters."""
import psycopg
import pytest

from tests.dbsupport import DSN, requires_db
from watchdogdatamodel import compute_fingerprint, query
from watchdogdatamodel.store.checks import upsert_check
from watchdogdatamodel.store.issues import open_or_touch
from watchdogdatamodel.store.runs import finish_run, start_run
from watchdogdatamodel.store.series import upsert_series

pytestmark = requires_db


@pytest.fixture()
def ro_conn(conn):
    # No read-only-connection fixture exists in dbsupport.py yet, so this one
    # lives here. Depending on `conn` (rather than only the DSN) guarantees
    # the schema is bootstrapped on this database before we open the
    # SELECT-only session, regardless of which other test files have run.
    c = query.connect(DSN)
    try:
        yield c
    finally:
        c.close()


def _seed(conn):
    # Ported from tests/test_readonly_kind.py's `_seed`, with one change:
    # that helper left both rows' check_id NULL, which made them
    # indistinguishable for a check_id-based assertion. check_id has a FK to
    # check_definition, so making it non-null needs a matching catalog row —
    # added here for the "issue" row only, via upsert_check.
    upsert_check(conn, id="timing_gaps", name="timing_gaps")
    s = upsert_series(conn, key="SE:load:1:entsoe", name="x", labels={"zone": "SE"})
    open_or_touch(conn, fingerprint=compute_fingerprint(s.key, "timing_gaps"),
                  origin="check", title="local gap", actor="t",
                  series_id=s.id, check_id="timing_gaps", kind="issue")
    open_or_touch(conn, fingerprint=compute_fingerprint(s.key, "gross_range"),
                  origin="check", title="spike", actor="t",
                  series_id=s.id, kind="context")
    return s


@requires_db
def test_connection_refuses_writes(ro_conn):
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        ro_conn.execute("DELETE FROM issue")


@requires_db
def test_list_issues_filters_by_kind(conn, ro_conn):
    # seed one issue row and one context row with the store, then:
    _seed(conn)
    issues = query.list_issues(ro_conn, state="open", kind="issue")
    context = query.list_issues(ro_conn, state="open", kind="context")
    assert {i["check_id"] for i in issues}.isdisjoint({c["check_id"] for c in context})
    assert all(i["series_key"] for i in issues)  # the series join survives


@requires_db
def test_series_context_returns_only_context_rows(conn, ro_conn):
    # seed: one kind='issue' row and one kind='context' row on the same series
    s = _seed(conn)
    rows = query.series_context(ro_conn, s.key)
    assert rows, "expected the seeded context finding"
    assert all(r["kind"] == "context" for r in rows)


@requires_db
def test_run_covering_ignores_runs_that_do_not_cover(conn, ro_conn):
    # seed: the COVERING run finishes FIRST, scoped to this series with a
    # specific (non-"all") check list; a LATER run finishes AFTER it, scoped
    # to another zone entirely. Only genuine per-series scope evaluation —
    # not a naive "newest completed run" MAX(finished_at) query — can tell
    # these apart, since the non-covering run is the more recent of the two.
    s = upsert_series(conn, key="GB:consumption:neso", name="x", labels={"zone": "GB"})

    covering = start_run(
        conn, scope={"series": {"keys": [s.key]}, "checks": ["timing_gaps"]},
        trigger="targeted")
    covering_run_id = finish_run(conn, covering.id, stats={}).id

    not_covering = start_run(
        conn, scope={"series": {"labels": {"zone": "FI"}}, "checks": "all"},
        trigger="scheduled")
    finish_run(conn, not_covering.id, stats={})

    got = query.run_covering(ro_conn, s.key)
    assert got is not None
    assert got["id"] == str(covering_run_id)
    # check_id=None (the default, as used above) ignores the check dimension:
    # this run declares a specific checks list, ["timing_gaps"], not "all",
    # and is still the one returned — proving the widened default.
    assert got["scope"]["checks"] == ["timing_gaps"]
