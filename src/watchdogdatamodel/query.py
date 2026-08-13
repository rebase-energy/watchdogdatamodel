"""Read-only queries: the safe way for agents (and the CLI) to read a wdm database.

No SQL required, no writes possible. Three walls: (1) callers should use a
SELECT-only role, (2) the session forces default_transaction_read_only=on so
the server rejects any mutation regardless of role, (3) this surface exposes
read functions only. Every function returns JSON-able dicts.

    from watchdogdatamodel import query
    conn = query.connect()            # WDM_READONLY_PG_DSN or WATCHDOG_READONLY_PG_DSN
    query.list_issues(conn, state="open", check_id="source_divergence", limit=10)
    query.get_issue(conn, "<id>")      # includes the full diary + actions
"""
from __future__ import annotations

import os
from typing import Any

import psycopg
from psycopg.rows import dict_row

_ENV_VARS = ("WDM_READONLY_PG_DSN", "WATCHDOG_READONLY_PG_DSN")


def _jsonable(row: dict) -> dict[str, Any]:
    out = {}
    for k, v in row.items():
        out[k] = v.isoformat() if hasattr(v, "isoformat") else (
            str(v) if v.__class__.__name__ == "UUID" else v)
    return out


def connect(dsn: str | None = None) -> psycopg.Connection:
    """Open a read-only session over the seven wdm tables.

    ``dsn=None`` reads ``WDM_READONLY_PG_DSN`` then ``WATCHDOG_READONLY_PG_DSN``.
    """
    if dsn is None:
        for var in _ENV_VARS:
            if os.environ.get(var):
                dsn = os.environ[var]
                break
        else:
            raise RuntimeError(f"set one of {_ENV_VARS}")
    try:
        return psycopg.connect(
            dsn, autocommit=True, row_factory=dict_row,
            options="-c default_transaction_read_only=on",
        )
    except psycopg.OperationalError as e:
        if "unsupported startup parameter" not in str(e):
            raise
        # Pooled DSNs (e.g. Neon's PgBouncer) reject startup options;
        # fall back to a session SET. The SELECT-only role remains the
        # primary wall; this one is belt-and-braces where supported.
        conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
        conn.execute("SET default_transaction_read_only = on")
        return conn


def _all(conn, q: str, params: tuple = ()) -> list[dict]:
    return [_jsonable(r) for r in conn.execute(q, params).fetchall()]


# ── series ──────────────────────────────────────────────────────────
def list_series(conn, labels: dict | None = None, active: bool = True,
                limit: int = 200) -> list[dict]:
    from psycopg.types.json import Jsonb

    q, p = "SELECT * FROM series WHERE active = %s", [active]
    if labels:
        q += " AND labels @> %s"
        p.append(Jsonb(labels))
    return _all(conn, q + " ORDER BY key LIMIT %s", (*p, limit))


def get_series(conn, key: str) -> dict | None:
    r = _all(conn, "SELECT * FROM series WHERE key = %s", (key,))
    return r[0] if r else None


# ── checks / runs ───────────────────────────────────────────────────
def list_checks(conn) -> list[dict]:
    return _all(conn, "SELECT * FROM check_definition ORDER BY id")


def get_check(conn, check_id: str) -> dict | None:
    r = _all(conn, "SELECT * FROM check_definition WHERE id = %s", (check_id,))
    return r[0] if r else None


def list_runs(conn, limit: int = 20) -> list[dict]:
    return _all(
        conn, "SELECT * FROM check_run ORDER BY started_at DESC LIMIT %s", (limit,))


def get_run(conn, run_id: str) -> dict | None:
    r = _all(conn, "SELECT * FROM check_run WHERE id = %s", (run_id,))
    return r[0] if r else None


# ── issues ──────────────────────────────────────────────────────────
def list_issues(conn, state: str = "open", check_id: str | None = None,
                labels: dict | None = None, kind: str | None = None,
                limit: int = 100) -> list[dict]:
    from psycopg.types.json import Jsonb

    q = ("SELECT i.*, s.key AS series_key, s.labels AS series_labels "
         "FROM issue i LEFT JOIN series s ON s.id = i.series_id "
         "WHERE i.state = %s")
    p: list = [state]
    if check_id:
        q += " AND i.check_id = %s"
        p.append(check_id)
    if labels:
        q += " AND s.labels @> %s"
        p.append(Jsonb(labels))
    if kind:
        q += " AND i.kind = %s"
        p.append(kind)
    return _all(conn, q + " ORDER BY i.last_seen_at DESC LIMIT %s", (*p, limit))


def get_issue(conn, issue_id: str) -> dict | None:
    r = _all(
        conn,
        "SELECT i.*, s.key AS series_key, s.labels AS series_labels FROM issue i "
        "LEFT JOIN series s ON s.id = i.series_id WHERE i.id = %s", (issue_id,))
    if not r:
        return None
    issue = r[0]
    issue["events"] = list_events(conn, issue_id)
    issue["actions"] = list_actions(conn, issue_id=issue_id)
    return issue


def list_events(conn, issue_id: str, limit: int = 200) -> list[dict]:
    return _all(
        conn, "SELECT * FROM issue_event WHERE issue_id = %s ORDER BY at, id LIMIT %s",
        (issue_id, limit))


# ── actions / snapshots ─────────────────────────────────────────────
def list_actions(conn, issue_id: str | None = None, type: str | None = None,
                 status: str | None = None, limit: int = 50) -> list[dict]:
    q, p = "SELECT * FROM action WHERE true", []
    for col, val in (("issue_id", issue_id), ("type", type), ("status", status)):
        if val is not None:
            q += f" AND {col} = %s"
            p.append(val)
    return _all(conn, q + " ORDER BY created_at DESC LIMIT %s", (*p, limit))


def get_snapshot(conn, series_key: str) -> dict | None:
    r = _all(
        conn,
        "SELECT sn.* FROM series_snapshot sn JOIN series s ON s.id = sn.series_id "
        "WHERE s.key = %s", (series_key,))
    return r[0] if r else None


# ── context / coverage / analytics ─────────────────────────────────
def series_context(conn, series_key: str) -> list[dict]:
    """Open context-lane findings for one series: true, upstream-caused, NOT
    actionable by us. The board and kanban deliberately never paint these."""
    return _all(conn,
        "SELECT i.*, s.key AS series_key FROM issue i JOIN series s ON s.id = i.series_id "
        "WHERE s.key = %s AND i.state = 'open' AND i.kind = 'context' "
        "ORDER BY i.last_seen_at DESC", (series_key,))


def series_issues(conn, series_key: str) -> list[dict]:
    """Every open issue on one series, both kinds — kind is in the payload."""
    return _all(conn,
        "SELECT i.*, s.key AS series_key FROM issue i JOIN series s ON s.id = i.series_id "
        "WHERE s.key = %s AND i.state = 'open' ORDER BY i.kind, i.last_seen_at DESC",
        (series_key,))


def series_checks(conn, series_key: str) -> dict | None:
    """Latest per-check outcome for one series, from its snapshot's stats (the
    main lane). Returns the snapshot's `stats` verbatim plus the window, so a
    caller sees ran / not-applicable / could-not-run as the run recorded it."""
    snap = get_snapshot(conn, series_key)
    if snap is None:
        return None
    return {"window_start": snap.get("window_start"), "window_end": snap.get("window_end"),
            "fetched_at": snap.get("fetched_at"), "stats": snap.get("stats") or {}}


def run_covering(conn, series_key: str, check_id: str | None = None) -> dict | None:
    """The most recent COMPLETED run whose declared scope covers this series —
    not a global MAX(finished_at). A one-cell targeted run declares a narrow
    scope, so `latest run` is a per-series question (spec 5.1, scope truth).

    ``scope_covers`` (scope.py) takes keyword args — ``series_id``, ``labels``,
    ``check_id``, ``series_key`` — not a series dict, so those are unpacked
    from the looked-up series row rather than passed as one object.
    """
    series = get_series(conn, series_key)
    if series is None:
        return None
    from .scope import scope_covers

    rows = _all(conn,
        "SELECT r.* FROM check_run r WHERE r.status = 'completed' "
        "ORDER BY r.finished_at DESC NULLS LAST LIMIT 200")
    for r in rows:
        if scope_covers(r.get("scope") or {}, series_id=series["id"],
                        labels=series.get("labels") or {}, check_id=check_id,
                        series_key=series_key):
            return r
    return None


def issues_similar(conn, issue_id: str, limit: int = 15) -> list[dict]:
    """Open issues sharing this issue's series OR its check — the
    is-it-systemic question. kind is returned so a caller can tell an
    actionable sibling from an upstream context finding."""
    me = _all(conn, "SELECT series_id, check_id FROM issue WHERE id = %s", (issue_id,))
    if not me:
        return []
    return _all(conn,
        "SELECT i.check_id, i.kind, i.severity, i.last_seen_at, s.key AS series_key "
        "FROM issue i JOIN series s ON s.id = i.series_id "
        "WHERE i.state = 'open' AND i.id <> %s AND (i.series_id = %s OR i.check_id = %s) "
        "ORDER BY i.last_seen_at DESC LIMIT %s",
        (issue_id, me[0]["series_id"], me[0]["check_id"], limit))


_STATS_BY = {"check": "i.check_id", "kind": "i.kind", "severity": "i.severity",
             "zone": "s.labels->>'zone'", "source": "s.labels->>'source'"}


def stats(conn, by: str = "check") -> list[dict]:
    """Open-issue counts grouped by one dimension. Rejects anything outside
    _STATS_BY — the group expression is interpolated, so it must never come
    from caller input directly."""
    col = _STATS_BY.get(by)
    if col is None:
        raise ValueError(f"by must be one of {sorted(_STATS_BY)}")
    return _all(conn,
        f"SELECT {col} AS group_value, i.kind, count(*) AS n "
        "FROM issue i LEFT JOIN series s ON s.id = i.series_id "
        "WHERE i.state = 'open' GROUP BY 1, 2 ORDER BY n DESC")
