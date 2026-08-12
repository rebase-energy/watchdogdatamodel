"""Issues: fingerprint-deduped problems + their append-only diary (spec §3.4, §3.5)."""
import psycopg
from psycopg.types.json import Jsonb

from .db import tx
from ..models import RESOLUTION_REASONS, Issue, IssueEvent


def add_event(conn, issue_id, *, type, actor, run_id=None, action_id=None, data=None) -> IssueEvent:
    row = conn.execute(
        """
        INSERT INTO issue_event (issue_id, type, actor, run_id, action_id, data)
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING *
        """,
        (issue_id, type, actor, run_id, action_id, Jsonb(data or {})),
    ).fetchone()
    return IssueEvent(**row)


def list_events(conn, issue_id) -> list[IssueEvent]:
    rows = conn.execute(
        "SELECT * FROM issue_event WHERE issue_id = %s ORDER BY at, id", (issue_id,)
    ).fetchall()
    return [IssueEvent(**r) for r in rows]


def get_issue(conn, issue_id) -> Issue | None:
    row = conn.execute("SELECT * FROM issue WHERE id = %s", (issue_id,)).fetchone()
    return Issue(**row) if row else None


def open_or_touch(conn, *, fingerprint, origin, title, actor, severity="medium",
                  stage="new", series_id=None, related_series=None, check_id=None,
                  run_id=None, details=None, valid_start=None, valid_end=None,
                  knowledge_time=None, kind="issue") -> tuple[Issue, bool]:
    """Open a new issue, or touch the open one with this fingerprint.

    Touching bumps last_seen_at and records a detected_again event; it never
    rewrites details (evidence stays frozen at detection, spec §3.4).
    """
    with tx(conn), conn.transaction():
        row = conn.execute(
            "SELECT id FROM issue WHERE fingerprint = %s AND state = 'open' FOR UPDATE",
            (fingerprint,),
        ).fetchone()
        if row:
            updated = conn.execute(
                """
                UPDATE issue SET last_seen_at = now(), updated_at = now()
                WHERE id = %s RETURNING *
                """,
                (row["id"],),
            ).fetchone()
            add_event(conn, row["id"], type="detected_again", actor=actor, run_id=run_id)
            return Issue(**updated), False

        pred = conn.execute(
            """
            SELECT id FROM issue WHERE fingerprint = %s AND state = 'resolved'
            ORDER BY resolved_at DESC LIMIT 1
            """,
            (fingerprint,),
        ).fetchone()
        try:
            created = conn.execute(
                """
                INSERT INTO issue (fingerprint, origin, series_id, related_series, check_id,
                                   stage, severity, title, details, valid_start, valid_end,
                                   knowledge_time, detected_by_run, predecessor_id, kind)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (fingerprint, origin, series_id, Jsonb(related_series or []), check_id,
                 stage, severity, title, Jsonb(details or {}), valid_start, valid_end,
                 knowledge_time, run_id, pred["id"] if pred else None, kind),
            ).fetchone()
        except psycopg.errors.UniqueViolation:
            created = None  # lost a race with a concurrent opener; retry as touch
        else:
            add_event(conn, created["id"], type="opened", actor=actor, run_id=run_id)
            return Issue(**created), True
    return open_or_touch(
        conn, fingerprint=fingerprint, origin=origin, title=title, actor=actor,
        severity=severity, stage=stage, series_id=series_id,
        related_series=related_series, check_id=check_id, run_id=run_id,
        details=details, valid_start=valid_start, valid_end=valid_end,
        knowledge_time=knowledge_time, kind=kind,
    )


def record_not_seen(conn, issue_id, *, run_id, actor) -> None:
    """A covering run reported nothing. Resolution is the caller's policy."""
    add_event(conn, issue_id, type="not_seen", actor=actor, run_id=run_id)


def resolve(conn, issue_id, *, reason, actor, comment=None) -> Issue:
    if reason not in RESOLUTION_REASONS:
        raise ValueError(
            f"unknown resolution reason {reason!r}; allowed: {sorted(RESOLUTION_REASONS)}"
        )
    with tx(conn), conn.transaction():
        row = conn.execute(
            """
            UPDATE issue SET state = 'resolved', resolved_at = now(),
                   resolution_reason = %s, resolution_comment = %s, resolved_by = %s,
                   updated_at = now()
            WHERE id = %s AND state = 'open' RETURNING *
            """,
            (reason, comment, actor, issue_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"issue {issue_id} is not open")
        add_event(conn, issue_id, type="resolved", actor=actor,
                  data={"reason": reason, "comment": comment})
    return Issue(**row)


def reopen(conn, issue_id, *, actor, comment=None) -> Issue:
    """Human-initiated only (spec §3.4): automatic recurrence opens a new issue."""
    with tx(conn), conn.transaction():
        row = conn.execute(
            """
            UPDATE issue SET state = 'open', resolved_at = NULL, resolution_reason = NULL,
                   resolution_comment = NULL, resolved_by = NULL, updated_at = now()
            WHERE id = %s AND state = 'resolved' RETURNING *
            """,
            (issue_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"issue {issue_id} is not resolved")
        add_event(conn, issue_id, type="reopened", actor=actor, data={"comment": comment})
    return Issue(**row)


def set_stage(conn, issue_id, *, stage, actor) -> Issue:
    with tx(conn), conn.transaction():
        old = conn.execute(
            "SELECT stage FROM issue WHERE id = %s FOR UPDATE", (issue_id,)
        ).fetchone()
        if old is None:
            raise ValueError(f"issue {issue_id} not found")
        row = conn.execute(
            "UPDATE issue SET stage = %s, updated_at = now() WHERE id = %s RETURNING *",
            (stage, issue_id),
        ).fetchone()
        add_event(conn, issue_id, type="stage_changed", actor=actor,
                  data={"from": old["stage"], "to": stage})
    return Issue(**row)
