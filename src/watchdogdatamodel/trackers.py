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
6. DIAGNOSIS IS A DELIVERABLE; ending an engagement never requires closing
   the ticket (ticket closure is a product/human decision). A findings-only
   conclusion attaches its artifact and finishes — it never just vanishes.
"""
from __future__ import annotations

import logging
from typing import Callable

from .store.actions import claim_next as _store_claim_next
from .store.actions import finish, get_action
from .store.db import tx
from .store.issues import add_event

log = logging.getLogger(__name__)

# Canonical kinds (recommendation, not enforcement): "ticket" = the tracker
# item mirroring the work; "deliverable" = an artifact the agent produced
# (PR/MR/patch/report). Generic on purpose — no tracker vocabulary.
KIND_TICKET = "ticket"
KIND_DELIVERABLE = "deliverable"

_STAMP_RE = None


def stamp(action_id) -> str:
    """The machine line to embed in anything created on the tracker.
    Correlation happens by exact UUID — never by prose keywords."""
    return f"wdm-action: {action_id}"


def find_stamp(text: str | None):
    """Extract a stamped action id from tracker text, or None."""
    global _STAMP_RE
    if _STAMP_RE is None:
        import re

        _STAMP_RE = re.compile(
            r"wdm-action:\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
            r"[0-9a-f]{4}-[0-9a-f]{12})", re.I)
    m = _STAMP_RE.search(text or "")
    return m.group(1) if m else None



def claim_next(conn, action_type: str, *, worker: str,
               max_inflight: int | None = None):
    """Executor entry point: claim the oldest queued action of this type.

    The model IS the queue — an executor polls this instead of subscribing to
    tracker events, then stamps everything it creates with :func:`stamp` on
    the claimed action's id. ``max_inflight`` enforces the concurrency budget
    rule 3 protects: the running-count check and the claim share one
    transaction, so a slot freed by :func:`finish_on_external_close` is seen
    immediately and never double-spent.

    Returns the claimed Action (now ``running``, with the worker recorded in
    its transition log), or None — nothing queued, or the budget is spent.
    """
    with tx(conn), conn.transaction():
        if max_inflight is not None:
            n = conn.execute(
                "SELECT count(*) AS n FROM action "
                "WHERE type = %s AND status = 'running'",
                (action_type,)).fetchone()["n"]
            if n >= max_inflight:
                return None
        return _store_claim_next(conn, action_type, worker=worker)


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


def add_deliverable(conn, action_id, *, ref: dict,
                    actor: str = "tracker") -> bool:
    """Rule 4 (Davide, 2026-08-10): the action is the ENGAGEMENT, deliverables
    are attachments — an agent may produce 0..n. Appends `ref` to the live
    action's outcome.deliverables (deduped by its "id" key when present) and
    records the diary event either way. Finishing is a separate decision."""
    a = get_action(conn, action_id)
    if a is None:
        return False
    existing = (a.outcome or {}).get("deliverables") or []
    rid = ref.get("id")
    if rid is not None and any(d.get("id") == rid for d in existing):
        return False
    if a.status in ("queued", "running"):
        from psycopg.types.json import Jsonb

        conn.execute(
            "UPDATE action SET outcome = jsonb_set(outcome, '{deliverables}', "
            "COALESCE(outcome->'deliverables', '[]'::jsonb) || %s::jsonb) "
            "WHERE id = %s AND status IN ('queued','running')",
            (Jsonb([ref]), action_id))
    add_event(conn, a.issue_id, type="external_changed", actor=actor,
              action_id=action_id, data={KIND_DELIVERABLE: "attached", **ref})
    return True


def deliver_findings(conn, action_id, *, ref: dict,
                     reason: str = "diagnosed", actor: str = "agent") -> bool:
    """Rule 6 (Davide, 2026-08-11): DIAGNOSIS IS A DELIVERABLE, and ending an
    engagement never requires closing the ticket. An investigation that
    concludes without a code artifact attaches its findings (a comment, a
    report — any linkable ref) and finishes succeeded. What happens to the
    external ticket afterwards is a product/human decision this helper
    deliberately cannot make — it has no close parameter.

    Returns False when the action is missing or already terminal (late or
    duplicate deliveries stay diary-only via record_external_change)."""
    a = get_action(conn, action_id)
    if a is None or a.status not in ("queued", "running"):
        return False
    add_deliverable(conn, action_id, ref={"kind": "findings", **ref}, actor=actor)
    outcome = {"result": reason}
    if ref.get("url"):
        outcome["findings_url"] = ref["url"]
    finish(conn, str(action_id), status="succeeded", by=actor, outcome=outcome)
    return True


def deliverable_arrived(conn, action_id, *, links: dict,
                        actor: str = "tracker") -> bool:
    """Policy sugar for products whose engagements end at the first
    deliverable (the grid-map watchdog today): attach + finish succeeded."""
    if not add_deliverable(conn, action_id, ref=links, actor=actor):
        return False
    a = get_action(conn, action_id)
    if a.status in ("queued", "running"):
        finish(conn, str(action_id), status="succeeded", by=actor, outcome=links)
    return True


def reconcile_external(conn, *, action_type: str,
                       fetch_state: Callable[[dict], dict | None],
                       actor: str = "rule:reconcile",
                       resolved_grace_days: int = 7) -> dict:
    """Rule 5: poll truth for in-flight work. For every action of
    `action_type` whose issue is still open, call `fetch_state(action_dict)`
    — the product's tracker glue — which returns None (no news) or
    {"kind": ..., "state": ..., "data": {...}}. Missed changes are recorded
    idempotently via record_external_change. Never raises past one item."""
    rows = conn.execute(
        "SELECT a.id FROM action a JOIN issue i ON i.id = a.issue_id "
        "WHERE a.type = %s AND (i.state = 'open' OR i.resolved_at > "
        "now() - make_interval(days => %s))",
        (action_type, resolved_grace_days)).fetchall()
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
