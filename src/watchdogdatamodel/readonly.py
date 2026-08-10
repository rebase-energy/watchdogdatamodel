"""Read-only SDK: the safe way for agents to read a wdm database.

No SQL required, no writes possible. Three walls: (1) callers should use a
SELECT-only role, (2) the session forces default_transaction_read_only=on so
the server rejects any mutation regardless of role, (3) this surface exposes
read functions only. Every function returns JSON-able dicts.

    from watchdogdatamodel.readonly import ReadOnly
    ro = ReadOnly.from_env()          # WDM_READONLY_PG_DSN or WATCHDOG_READONLY_PG_DSN
    ro.list_issues(state="open", check_id="source_divergence", limit=10)
    ro.get_issue("<id>")              # includes the full diary + actions
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


class ReadOnly:
    """A read-only session over the seven wdm tables."""

    def __init__(self, dsn: str) -> None:
        self._conn = psycopg.connect(
            dsn, autocommit=True, row_factory=dict_row,
            options="-c default_transaction_read_only=on",
        )

    @classmethod
    def from_env(cls) -> "ReadOnly":
        for var in _ENV_VARS:
            if os.environ.get(var):
                return cls(os.environ[var])
        raise RuntimeError(f"set one of {_ENV_VARS}")

    def _all(self, q: str, params: tuple = ()) -> list[dict]:
        return [_jsonable(r) for r in self._conn.execute(q, params).fetchall()]

    # ── series ──────────────────────────────────────────────────────────
    def list_series(self, labels: dict | None = None, active: bool = True,
                    limit: int = 200) -> list[dict]:
        from psycopg.types.json import Jsonb

        q, p = "SELECT * FROM series WHERE active = %s", [active]
        if labels:
            q += " AND labels @> %s"
            p.append(Jsonb(labels))
        return self._all(q + " ORDER BY key LIMIT %s", (*p, limit))

    def get_series(self, key: str) -> dict | None:
        r = self._all("SELECT * FROM series WHERE key = %s", (key,))
        return r[0] if r else None

    # ── checks / runs ───────────────────────────────────────────────────
    def list_checks(self) -> list[dict]:
        return self._all("SELECT * FROM check_definition ORDER BY id")

    def list_runs(self, limit: int = 20) -> list[dict]:
        return self._all(
            "SELECT * FROM check_run ORDER BY started_at DESC LIMIT %s", (limit,))

    # ── issues ──────────────────────────────────────────────────────────
    def list_issues(self, state: str = "open", check_id: str | None = None,
                    labels: dict | None = None, limit: int = 100) -> list[dict]:
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
        return self._all(q + " ORDER BY i.last_seen_at DESC LIMIT %s", (*p, limit))

    def get_issue(self, issue_id: str) -> dict | None:
        r = self._all(
            "SELECT i.*, s.key AS series_key FROM issue i "
            "LEFT JOIN series s ON s.id = i.series_id WHERE i.id = %s", (issue_id,))
        if not r:
            return None
        issue = r[0]
        issue["events"] = self.list_events(issue_id)
        issue["actions"] = self.list_actions(issue_id=issue_id)
        return issue

    def list_events(self, issue_id: str, limit: int = 200) -> list[dict]:
        return self._all(
            "SELECT * FROM issue_event WHERE issue_id = %s ORDER BY at, id LIMIT %s",
            (issue_id, limit))

    # ── actions / snapshots ─────────────────────────────────────────────
    def list_actions(self, issue_id: str | None = None, type: str | None = None,
                     status: str | None = None, limit: int = 50) -> list[dict]:
        q, p = "SELECT * FROM action WHERE true", []
        for col, val in (("issue_id", issue_id), ("type", type), ("status", status)):
            if val is not None:
                q += f" AND {col} = %s"
                p.append(val)
        return self._all(q + " ORDER BY created_at DESC LIMIT %s", (*p, limit))

    def get_snapshot(self, series_key: str) -> dict | None:
        r = self._all(
            "SELECT sn.* FROM series_snapshot sn JOIN series s ON s.id = sn.series_id "
            "WHERE s.key = %s", (series_key,))
        return r[0] if r else None
