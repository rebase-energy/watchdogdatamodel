"""Actions: the typed remediation work queue (spec §3.6)."""
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

from .db import tx
from ..models import TERMINAL_ACTION_STATUSES, Action
from .issues import add_event


def _transition(status: str, by: str) -> dict:
    return {"status": status, "at": datetime.now(timezone.utc).isoformat(), "by": by}


def get_action(conn, action_id) -> Action | None:
    row = conn.execute("SELECT * FROM action WHERE id = %s", (action_id,)).fetchone()
    return Action(**row) if row else None


def enqueue(conn, issue_id, type, *, requested_by, params=None,
            allow_context=False) -> tuple[Action, bool]:
    """Queue an action. A live (queued/running) duplicate makes this a no-op
    that returns the existing action (spec §3.6 idempotency rule).

    kind='context' issues are observations, not work (spec §2.6): enqueue
    refuses them unless the caller explicitly overrides with allow_context."""
    row = conn.execute("SELECT kind FROM issue WHERE id = %s", (issue_id,)).fetchone()
    if row is not None and row["kind"] == "context" and not allow_context:
        raise ValueError(
            "context findings are not actionable (pass allow_context=True to override)")
    with tx(conn), conn.transaction():
        row = conn.execute(
            """
            INSERT INTO action (issue_id, type, params, requested_by, transitions)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (issue_id, type) WHERE status IN ('queued', 'running')
            DO NOTHING
            RETURNING *
            """,
            (issue_id, type, Jsonb(params or {}), requested_by,
             Jsonb([_transition("queued", requested_by)])),
        ).fetchone()
        if row is not None:
            add_event(conn, issue_id, type="action_requested", actor=requested_by,
                      action_id=row["id"], data={"action_type": type})
            return Action(**row), True
    existing = conn.execute(
        """
        SELECT * FROM action
        WHERE issue_id = %s AND type = %s AND status IN ('queued', 'running')
        """,
        (issue_id, type),
    ).fetchone()
    return Action(**existing), False


def claim_next(conn, type, *, worker) -> Action | None:
    """Worker loop: claim the oldest queued action of this type, if any.

    Joins issue and only claims actions whose issue is kind='issue' — context
    findings are observations, not work (spec §2.6), so this is the single
    chokepoint that keeps every claim path kind-safe."""
    row = conn.execute(
        """
        UPDATE action SET status = 'running', started_at = now(),
               transitions = transitions || %s::jsonb
        WHERE id = (
            SELECT a.id FROM action a JOIN issue i ON i.id = a.issue_id
            WHERE a.status = 'queued' AND a.type = %s AND i.kind = 'issue'
            ORDER BY a.created_at LIMIT 1 FOR UPDATE OF a SKIP LOCKED
        )
        RETURNING *
        """,
        (Jsonb([_transition("running", worker)]), type),
    ).fetchone()
    return Action(**row) if row else None


def finish(conn, action_id, *, status, by, outcome=None) -> Action:
    if status not in TERMINAL_ACTION_STATUSES:
        raise ValueError(f"finish status must be one of {sorted(TERMINAL_ACTION_STATUSES)}")
    with tx(conn), conn.transaction():
        row = conn.execute(
            """
            UPDATE action SET status = %s, finished_at = now(),
                   outcome = outcome || %s::jsonb,
                   transitions = transitions || %s::jsonb
            WHERE id = %s AND status IN ('queued', 'running')
            RETURNING *
            """,
            (status, Jsonb(outcome or {}), Jsonb([_transition(status, by)]), action_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"action {action_id} is terminal or missing")
        add_event(conn, row["issue_id"], type="action_finished", actor=by,
                  action_id=action_id, data={"action_type": row["type"], "status": status})
    return Action(**row)


def cancel(conn, action_id, *, by) -> Action:
    return finish(conn, action_id, status="canceled", by=by)
