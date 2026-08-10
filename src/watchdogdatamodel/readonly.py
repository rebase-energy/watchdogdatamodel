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
        try:
            self._conn = psycopg.connect(
                dsn, autocommit=True, row_factory=dict_row,
                options="-c default_transaction_read_only=on",
            )
        except psycopg.OperationalError as e:
            if "unsupported startup parameter" not in str(e):
                raise
            # Pooled DSNs (e.g. Neon's PgBouncer) reject startup options;
            # fall back to a session SET. The SELECT-only role remains the
            # primary wall; this one is belt-and-braces where supported.
            self._conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
            self._conn.execute("SET default_transaction_read_only = on")

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
            "SELECT i.*, s.key AS series_key, s.labels AS series_labels FROM issue i "
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

    # ── agent layer (spec docs/specs/2026-08-10-agent-readonly-layer-design.md)
    # for_agent=True on the composites returns prompt-ready markdown built by
    # the pure renderers below. Strictly read-only; no schema knowledge beyond
    # the seven tables.

    def investigation_brief(self, issue_id: str, budget: str = "compact") -> str:
        i = self.get_issue(issue_id)
        if i is None:
            return f"No issue with id {issue_id}."
        caps = _CAPS[budget]
        out = [f"## Issue\n{_md_issue(i)}"]
        out.append("## Timeline\n" + _md_timeline(i["events"], caps["events"]))
        acts = [a for a in i["actions"]]
        out.append("## Already tried\n" + (_md_actions(acts, caps["actions"])
                                            or "Nothing yet."))
        out.append("## Past incidents\n" + (self._md_lineage(i) or
                                            "None — first occurrence."))
        out.append("## Related\n" + (self._md_related(i, caps["related"]) or "None."))
        snap = self.get_snapshot(i["series_key"]) if i.get("series_key") else None
        out.append("## Data\n" + (_md_snapshot(snap) if snap else
                                  "No snapshot (cell may be empty or unreadable)."))
        return "\n\n".join(out)

    def work_order(self, issue_id: str, budget: str = "full") -> str:
        """The complete, file-ready work order for one investigation: the
        issue's brief plus the board situation, in one call. Executors write
        this into the agent's sandbox as its entire context instead of
        hand-assembling the bundle (see docs/executor-pattern.md)."""
        return (self.investigation_brief(issue_id, budget=budget)
                + "\n\n---\n\n" + self.situation())

    def history(self, issue_id: str) -> str:
        i = self.get_issue(issue_id)
        if i is None:
            return f"No issue with id {issue_id}."
        return (f"## Issue\n{_md_issue(i)}\n\n## Timeline\n"
                + _md_timeline(i["events"], _CAPS["full"]["events"])
                + "\n\n## Past incidents\n" + (self._md_lineage(i) or "None."))

    def situation(self, labels: dict | None = None,
                  check_id: str | None = None) -> str:
        issues = self.list_issues(state="open", check_id=check_id, labels=labels,
                                  limit=500)
        by_check: dict[str, list] = {}
        for i in issues:
            by_check.setdefault(i.get("check_id") or i["origin"], []).append(i)
        out = [f"## Open issues ({len(issues)})"]
        for check, group in sorted(by_check.items(), key=lambda kv: -len(kv[1])):
            lines = [f"### {check} ({len(group)})"]
            for i in group[:5]:
                lines.append(f"- {i.get('series_key') or i['title']} — "
                             f"{i['severity']}, last seen {_ts(i['last_seen_at'])}")
            if len(group) > 5:
                lines.append(f"- … {len(group) - 5} more omitted")
            out.append("\n".join(lines))
        runs = self.list_runs(limit=3)
        out.append("## Recent runs\n" + "\n".join(
            f"- {r['status']} · {r['trigger']} · started {_ts(r['started_at'])}"
            for r in runs))
        return "\n\n".join(out)

    def summary(self) -> str:
        rows = self._all(
            "SELECT check_id, severity, count(*) n FROM issue WHERE state='open' "
            "GROUP BY 1, 2 ORDER BY n DESC LIMIT 15")
        stages = self._all(
            "SELECT stage, count(*) n FROM issue WHERE state='open' GROUP BY 1")
        run = self.list_runs(limit=1)
        out = ["## Watchdog summary", "Open issues by check × severity:"]
        out += [f"- {r['check_id']}: {r['n']} ({r['severity']})" for r in rows]
        out.append("By stage: " + ", ".join(f"{s['stage']}={s['n']}" for s in stages))
        if run:
            out.append(f"Last run: {run[0]['status']} ({run[0]['trigger']}) "
                       f"started {_ts(run[0]['started_at'])}")
        return "\n".join(out)

    # renderer helpers needing DB access
    def _md_lineage(self, issue: dict, depth: int = 10) -> str:
        lines, cur = [], issue
        for _ in range(depth):
            pid = cur.get("predecessor_id")
            if not pid:
                break
            prev = self.get_issue(str(pid))
            if prev is None:
                break
            verified = ""
            for e in prev["events"]:
                if e["type"] == "comment" and (e["data"] or {}).get("verify"):
                    verified = f" — fix {e['data']['verify']}"
            lines.append(f"- {_ts(prev['first_seen_at'])} → resolved "
                         f"({prev.get('resolution_reason')}) "
                         f"{_ts(prev.get('resolved_at'))}{verified}")
            cur = prev
        return "\n".join(lines)

    def _md_related(self, issue: dict, cap: int) -> str:
        if not issue.get("series_id"):
            return ""
        rows = self._all(
            "SELECT i.check_id, i.severity, i.last_seen_at, s.key AS series_key "
            "FROM issue i JOIN series s ON s.id = i.series_id "
            "WHERE i.state = 'open' AND i.id <> %s "
            "  AND (i.series_id = %s OR i.check_id = %s) "
            "ORDER BY i.last_seen_at DESC LIMIT %s",
            (issue["id"], issue["series_id"], issue.get("check_id"), cap + 1))
        lines = [f"- {r['series_key']} · {r['check_id']} ({r['severity']})"
                 for r in rows[:cap]]
        if len(rows) > cap:
            lines.append(f"- … more omitted")
        return "\n".join(lines)


_CAPS = {"compact": {"events": 12, "actions": 3, "related": 5},
         "full": {"events": 50, "actions": 10, "related": 15}}


def _ts(v) -> str:
    return str(v)[:16] if v else "?"


def _md_issue(i: dict) -> str:
    lb = i.get("series_labels") or {}
    lbl = ", ".join(f"{k}={v}" for k, v in sorted(lb.items(), key=str)) or "-"
    win = f"{_ts(i.get('valid_start'))} → {_ts(i.get('valid_end'))}" \
        if i.get("valid_start") else "unknown"
    return (f"**{i.get('check_id') or i['origin']}** on `{i.get('series_key') or '-'}`"
            f" ({lbl})\n- state: {i['state']} / {i['stage']} · severity {i['severity']}"
            f"\n- first seen {_ts(i['first_seen_at'])}, last {_ts(i['last_seen_at'])}"
            f"\n- affected data window: {win}"
            + (f"\n- verdict: {i['details'].get('verdict')}"
               if (i.get("details") or {}).get("verdict") else ""))


def _md_timeline(events: list[dict], cap: int) -> str:
    lines, run_start, run_n = [], None, 0
    def flush():
        nonlocal run_start, run_n
        if run_n:
            lines.append(f"- detected again ×{run_n} ({_ts(run_start)} → last)")
            run_start, run_n = None, 0
    for e in events:
        if e["type"] == "detected_again":
            run_start = run_start or e["at"]
            run_n += 1
            continue
        flush()
        note = (e["data"] or {})
        extra = note.get("reason") or note.get("verify") or note.get("pr") \
            or note.get("to") or note.get("note") or ""
        lines.append(f"- {_ts(e['at'])} {e['type']}"
                     + (f" ({extra})" if extra else "") + f" · {e['actor']}")
    flush()
    if len(lines) > cap:
        omitted = len(lines) - cap
        lines = lines[:2] + [f"- … {omitted} more omitted"] + lines[-(cap - 3):]
    return "\n".join(lines)


def _md_actions(actions: list[dict], cap: int) -> str:
    lines = []
    for a in actions[:cap]:
        o = a.get("outcome") or {}
        tail = (o.get("log") or [])[-2:]
        line = (f"- **{a['type']}** → {a['status']}"
                + (f" ({o.get('result')})" if o.get("result") else "")
                + (f", PR {o.get('pr_url')}" if o.get("pr_url") else ""))
        for t in tail:
            line += f"\n  - {t.split(' ', 1)[-1][:120]}"
        lines.append(line)
    if len(actions) > cap:
        lines.append(f"- … {len(actions) - cap} earlier omitted")
    return "\n".join(lines)


def _md_snapshot(s: dict) -> str:
    st = s.get("stats") or {}
    return (f"- window {_ts(s.get('window_start'))} → {_ts(s.get('window_end'))}, "
            f"fetched {_ts(s.get('fetched_at'))}\n- points {st.get('points')}, "
            f"nulls {st.get('nulls')}")
