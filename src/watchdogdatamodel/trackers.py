"""Tracker protocol: safe plumbing between external trackers and the model.

Products connect issues/actions to external trackers (GitHub, GitLab, Jira,
Linear...). The tracker's API is the product's business; the PROTOCOL — what
lands where, what dedups, what frees a slot, how lost deliveries are recovered
— is enforced here. Every rule below was a production bug before it was code
(grid-map watchdog, 2026-08-10):

1. External facts land on the DIARY; terminal actions are never mutated.
2. No state change => no event (tracker filing bursts must not spam).
3. An external close ends a live action (else it leaks concurrency slots).
4. The deliverable's arrival finishes the action; later news is diary-only.
5. Webhooks give latency, POLLING gives truth: deliveries get lost (a fix's
   merge can restart the very receiver that would record it) — reconcile.
"""
from __future__ import annotations

import logging
from typing import Callable

from .store.actions import finish, get_action
from .store.issues import add_event

log = logging.getLogger(__name__)


def record_external_change(conn, action_id, *, kind: str, state: str,
                           actor: str = "tracker", data: dict | None = None) -> bool:
    """Mirror one external state change. Dedups against the last known state
    for this `kind`; updates action outcome only while the action is live;
    always leaves the fact on the diary. Returns False when nothing changed."""
    a = get_action(conn, action_id)
    if a is None:
        return False
    key = f"{kind}_state"
    # Dedup against the DIARY first (the source of truth once an action is
    # terminal and its outcome frozen), then the outcome (seeded at filing).
    last = conn.execute(
        "SELECT data FROM issue_event WHERE action_id = %s AND "
        "type = 'external_changed' AND data ? %s ORDER BY id DESC LIMIT 1",
        (action_id, kind)).fetchone()
    known = (last["data"].get(kind) if last else (a.outcome or {}).get(key))
    if known == state:
        return False  # rule 2: filing bursts / duplicate deliveries
    from psycopg.types.json import Jsonb

    conn.execute(  # rule 1: only live actions mutate; trigger enforces anyway
        "UPDATE action SET outcome = outcome || %s::jsonb "
        "WHERE id = %s AND status IN ('queued', 'running')",
        (Jsonb({key: state, **(data or {})}), action_id))
    add_event(conn, a.issue_id, type="external_changed", actor=actor,
              action_id=action_id, data={kind: state, **(data or {})})
    return True


def finish_on_external_close(conn, action_id, *, reason: str = "completed",
                             actor: str = "tracker") -> bool:
    """Rule 3: an externally-closed tracker item ends a live action —
    'completed' => succeeded (closed_without_deliverable), else canceled."""
    a = get_action(conn, action_id)
    if a is None or a.status not in ("queued", "running"):
        return False
    finish(conn, str(action_id),
           status="succeeded" if reason == "completed" else "canceled",
           by=actor, outcome={"result": "closed_without_deliverable",
                              "close_reason": reason})
    return True


def deliverable_arrived(conn, action_id, *, links: dict,
                        actor: str = "tracker") -> bool:
    """Rule 4: the deliverable (e.g. an opened PR) finishes a live action with
    its links. Later news about the deliverable belongs on the diary only."""
    a = get_action(conn, action_id)
    if a is None or a.status not in ("queued", "running"):
        return False
    finish(conn, str(action_id), status="succeeded", by=actor, outcome=links)
    return True


def reconcile_external(conn, *, action_type: str,
                       fetch_state: Callable[[dict], dict | None],
                       actor: str = "rule:reconcile") -> dict:
    """Rule 5: poll truth for in-flight work. For every action of
    `action_type` whose issue is still open, call `fetch_state(action_dict)`
    — the product's tracker glue — which returns None (no news) or
    {"kind": ..., "state": ..., "data": {...}}. Missed changes are recorded
    idempotently via record_external_change. Never raises past one item."""
    rows = conn.execute(
        "SELECT a.id FROM action a JOIN issue i ON i.id = a.issue_id "
        "WHERE a.type = %s AND i.state = 'open'", (action_type,)).fetchall()
    counts = {"checked": 0, "recovered": 0}
    for r in rows:
        counts["checked"] += 1
        a = get_action(conn, r["id"])
        try:
            news = fetch_state(a.model_dump(mode="json"))
        except Exception as e:  # noqa: BLE001 — one bad fetch must not stop the pass
            log.warning("reconcile_external: fetch_state failed for %s: %s", a.id, e)
            continue
        if news and record_external_change(
                conn, str(a.id), kind=news["kind"], state=news["state"],
                actor=actor, data=news.get("data")):
            counts["recovered"] += 1
    return counts
