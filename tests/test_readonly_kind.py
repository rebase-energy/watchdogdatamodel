"""ReadOnly SDK is kind-aware: agents see kind, situation() counts work only."""
import pytest

from tests.dbsupport import DSN, requires_db
from watchdogdatamodel import compute_fingerprint
from watchdogdatamodel.store.issues import open_or_touch
from watchdogdatamodel.store.series import upsert_series

pytestmark = requires_db


@pytest.fixture()
def ro():
    from watchdogdatamodel.readonly import ReadOnly

    return ReadOnly(DSN)


def _seed(conn):
    s = upsert_series(conn, key="SE:load:1:entsoe", name="x", labels={"zone": "SE"})
    open_or_touch(conn, fingerprint=compute_fingerprint(s.key, "timing_gaps"),
                  origin="check", title="local gap", actor="t",
                  series_id=s.id, kind="issue")
    open_or_touch(conn, fingerprint=compute_fingerprint(s.key, "gross_range"),
                  origin="check", title="spike", actor="t",
                  series_id=s.id, kind="context")
    return s


def test_list_issues_kind_filter(conn, ro):
    _seed(conn)
    assert {i["title"] for i in ro.list_issues(kind="issue")} == {"local gap"}
    assert {i["title"] for i in ro.list_issues(kind="context")} == {"spike"}
    assert len(ro.list_issues()) == 2  # default: everything, kind visible
    assert all("kind" in i for i in ro.list_issues())


def test_situation_headline_excludes_context(conn, ro):
    # No series_id here: situation()'s group lines fall back to `i["title"]`
    # only when there's no series_key to prefer, so titles are what
    # distinguish rows in this assertion (fingerprint keeps them deduped).
    open_or_touch(conn, fingerprint=compute_fingerprint("no-series", "timing_gaps"),
                  origin="check", title="local gap", actor="t", kind="issue")
    open_or_touch(conn, fingerprint=compute_fingerprint("no-series", "gross_range"),
                  origin="check", title="spike", actor="t", kind="context")

    text = ro.situation()
    assert "local gap" in text
    assert "spike" not in text.split("context")[0]  # only in the tally line
    assert "context finding" in text or "context" in text


def test_md_issue_marks_context_finding(conn, ro):
    _seed(conn)
    context_issue = next(i for i in ro.list_issues(kind="context") if i["title"] == "spike")
    actionable_issue = next(i for i in ro.list_issues(kind="issue") if i["title"] == "local gap")

    ctx_brief = ro.investigation_brief(str(context_issue["id"]))
    work_brief = ro.investigation_brief(str(actionable_issue["id"]))

    assert "context finding" in ctx_brief
    assert "NOT actionable" in ctx_brief
    assert "context finding" not in work_brief


def test_summary_counts_actionable_only_and_tallies_context(conn, ro):
    _seed(conn)
    text = ro.summary()
    assert "context findings open: 1" in text
    # the by-check × severity breakdown counts the one actionable issue only,
    # not both (both share check_id=None/severity=medium in this fixture)
    assert "None: 1 (medium)" in text
