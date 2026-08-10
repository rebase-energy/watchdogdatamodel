# Agent layer for the read-only SDK — design

**Date:** 2026-08-10 · **Status:** approved · **Ships in:** v0.3.0

## Goal
Let investigating agents get cross-table context in one call, as prompt-ready
markdown — without SQL, without writes, without changing the data model.

## Decisions (Davide, 2026-08-10)
- `for_agent: bool = False` on every read function; `True` returns a markdown
  string, `False` the raw JSON-able dicts. No envelopes.
- Composites: `investigation_brief`, `history`, `situation`, `summary`.
- Architecture: renderers woven into `ReadOnly` (one class, one import);
  renderers are pure functions over the dict shapes.
- **Strictly read-only, zero schema change.** The layer only SELECTs (same
  session wall: `default_transaction_read_only=on`).

## Rendering rules
1. Stable section headers: `## Issue`, `## Timeline`, `## Already tried`,
   `## Past incidents`, `## Related`, `## Data`.
2. Compression, never silent truncation: consecutive `detected_again` events
   collapse to `detected again ×N (first → last)`; caps always print
   `… N more omitted`; action logs show the TAIL (outcomes end there).
3. `budget="compact"|"full"` on composites (compact ≈ 1–2k tokens).
4. Generic vocabulary only: labels, check ids, action types come from the DB;
   the SDK contains no product words.
5. Lineage lines carry outcome: `resolved (reason) on DATE — fix verified` /
   `fix_failed`, from resolution fields + verify diary comments. Depth cap 10.

## Function contracts
- `investigation_brief(issue_id, budget)` → Issue + Timeline(compressed) +
  Already tried (actions w/ result + log tail) + Past incidents (lineage) +
  Related (same-series open issues; same-check issues sharing any label,
  capped) + Data (snapshot stats, window; never the payload).
- `history(issue_id)` → full budgeted Timeline + lineage walked to root.
- `situation(labels=None, check_id=None)` → open issues grouped by check
  (top offenders by last_seen), last runs, recurrence leaders.
- `summary()` → counts only: open by check × severity, by stage, last run
  status/duration. ≤ ~15 lines.
- All existing list/get functions: `for_agent=True` renders the same data as
  a compact markdown table/list.

## Non-goals
Writes of any kind, schema changes, payload dumps, analytics beyond counts,
pagination cursors (limits + omission markers suffice for v1).
