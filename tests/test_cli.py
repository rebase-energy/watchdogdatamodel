"""Smoke tests for the read-only CLI: one per command group, light by directive."""
import subprocess
import sys

import pytest

from tests.dbsupport import DSN, requires_db

CMDS = [
    ["guide"],
    ["checks", "list"],
    ["issues", "list", "--limit", "5"],
    ["runs", "list", "--limit", "3"],
    ["stats", "--by", "kind"],
]


def _run(args, dsn=None):
    return subprocess.run(
        [sys.executable, "-m", "watchdogdatamodel.cli", *args]
        + (["--dsn", dsn] if dsn else []),
        capture_output=True, text=True, encoding="utf-8",
    )


@requires_db
@pytest.mark.parametrize("args", CMDS, ids=lambda a: "-".join(a))
def test_command_runs(args, conn):
    # `conn` is unused but required: it is what bootstraps the schema. Without
    # it, a virgin database has no tables, every DB-backed command correctly
    # answers "no wdm access" exit 2, and these tests fail on the wrong thing.
    # Exit status only: an empty table is a legitimate answer for the list
    # commands, and asserting non-empty stdout would make this test depend on
    # whatever rows other test modules happened to leave behind (they truncate).
    # `guide` is the one command whose output is data-independent, so it is the
    # one that must print something.
    res = _run(args, dsn=DSN)
    assert res.returncode == 0, res.stderr
    if args == ["guide"]:
        assert res.stdout.strip()


def test_missing_dsn_is_loud_not_silent():
    res = _run(["issues", "list"], dsn="postgresql://nope@127.0.0.1:1/none_test")
    assert res.returncode == 2
    assert "no wdm access" in (res.stdout + res.stderr).lower()


def test_malformed_dsn_is_no_wdm_access_not_a_traceback():
    # A DSN that isn't `key=value` pairs at all used to fail inside
    # psycopg.connect with ProgrammingError, a *sibling* of OperationalError
    # under psycopg.Error rather than a subclass — the narrower except let it
    # escape as a bare traceback instead of the documented NO_ACCESS path.
    res = _run(["issues", "list"], dsn="this is not a dsn")
    assert res.returncode == 2, res.stderr
    assert "no wdm access" in (res.stdout + res.stderr).lower()
    assert "Traceback" not in res.stderr


@requires_db
def test_timeline_shows_newest_events_when_truncated(conn):
    # Regression lock for finding 1: `ORDER BY at, id LIMIT n` fetched the
    # OLDEST n events and hid everything newer once an issue passed n events
    # — the exact opposite of what AGENT.md rule 2 tells an agent to trust.
    from watchdogdatamodel import compute_fingerprint
    from watchdogdatamodel.store.issues import add_event, open_or_touch
    from watchdogdatamodel.store.series import upsert_series

    s = upsert_series(conn, key="XX:test:1:t", name="x")
    issue, _ = open_or_touch(
        conn, fingerprint=compute_fingerprint(s.key, "t"), origin="check",
        title="t", actor="t", series_id=s.id)
    for n in range(25):
        add_event(conn, issue.id, type="observation", actor="t", data={"n": n})
    # 26 total events (1 "opened" + 25 "observation"); default --limit is 20.

    res = _run(["issue", "timeline", str(issue.id)], dsn=DSN)
    assert res.returncode == 0, res.stderr
    assert "'n': 24}" in res.stdout, "newest observation must be visible"
    assert "'n': 0}" not in res.stdout, "oldest, not newest, must be dropped"
    assert "older" in res.stdout.lower()


@requires_db
def test_action_list_rejects_an_issue_id_that_does_not_exist(conn):  # conn: bootstraps the schema
    # `action list --issue X` answers "what has already been tried". An empty
    # list for a nonexistent id reads as "nothing tried" and invites re-queueing
    # a heal or re-filing an investigation that already ran — the repo's
    # documented "same bug filed 5×" failure. A non-uuid must land the same way
    # (it used to raise InvalidTextRepresentation past the connection guard).
    for bad in ("00000000-0000-0000-0000-000000000000", "not-a-uuid"):
        res = _run(["action", "list", "--issue", bad], dsn=DSN)
        assert res.returncode == 2, f"{bad!r}: {res.stdout}{res.stderr}"
        assert "no such issue" in (res.stdout + res.stderr).lower()


@requires_db
def test_run_covering_distinguishes_no_such_series_from_not_covered(conn):
    # Regression lock for finding 2: a series key that doesn't exist at all
    # used to fall through to the same "(not covered: ...)" text as a real,
    # never-scanned series — indistinguishable exit-0 outcomes for "you
    # typo'd the key" and "your evidence about this series may be stale".
    from watchdogdatamodel.store.series import upsert_series

    upsert_series(conn, key="YY:test:1:t", name="y")

    missing = _run(["run", "covering", "NOPE-DOES-NOT-EXIST"], dsn=DSN)
    assert missing.returncode == 2
    assert "no such series" in (missing.stdout + missing.stderr).lower()

    real = _run(["run", "covering", "YY:test:1:t"], dsn=DSN)
    assert real.returncode == 0
    assert "not covered" in real.stdout.lower()
