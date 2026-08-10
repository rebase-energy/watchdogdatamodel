# Agent guide to the read-only SDK

`watchdogdatamodel.readonly` is how agents (and scripts) read a wdm database
safely: no SQL, no writes possible. Three walls: use a SELECT-only role,
the session forces `default_transaction_read_only`, and this surface has no
write functions.

## Connect

```python
from watchdogdatamodel.readonly import ReadOnly

ro = ReadOnly.from_env()   # WDM_READONLY_PG_DSN or WATCHDOG_READONLY_PG_DSN
# or: ro = ReadOnly("postgresql://readonly_role:...@host/db")
```

## The agent's first move: one call, the whole story

```python
print(ro.investigation_brief(issue_id))          # budget="compact" (default)
print(ro.investigation_brief(issue_id, budget="full"))
```

Real output (production data):

```markdown
## Issue
**source_divergence** on `SI:consumption:energy_charts` (data_type=consumption,
priority=2, source=energy_charts, zone=SI)
- state: open / new · severity high
- first seen 2026-08-10T09:55, last 2026-08-10T11:02
- affected data window: 2026-08-05T22:00 → 2026-08-08T07:00

## Timeline
- 2026-08-10T09:55 opened · run:08b7e1aa-…
- detected again ×3 (2026-08-10T10:15 → last)

## Already tried
Nothing yet.

## Past incidents
None — first occurrence.

## Related
- SI:production.unknown:energy_charts · source_divergence (high)
- SI:production.coal:energy_charts · source_divergence (high)
- … more omitted

## Data
- window 2026-08-03T11:00 → 2026-08-10T07:00, fetched 2026-08-10T11:02
- points 165, nulls 0
```

Read it like an investigator: `Already tried` tells you what to rule out
(a failed backfill renders with its conclusion line — e.g. "provider data
matches stored" rules out re-ingestion). `Past incidents` tells you whether a
previous fix bounced (`fix fix_failed`). `Related` tells you isolated vs
systemic — above, five sibling series diverge too: it's the whole feed.

## The other composites

```python
print(ro.history(issue_id))     # full timeline + recurrence chain to the root
print(ro.situation(labels={"zone": "SI"}))   # open issues grouped by check
print(ro.situation(check_id="timing_gaps")) # one check across everything
print(ro.summary())             # ~15 lines of counts — supervisor ping
```

## Raw data (for code, not prompts)

Every function returns JSON-able dicts by default:

```python
issues = ro.list_issues(state="open", check_id="gross_range", labels={"zone": "PT"})
issue  = ro.get_issue(issues[0]["id"])       # includes events + actions
runs   = ro.list_runs(limit=5)
snap   = ro.get_snapshot("PT:production.wind:energy_charts")
acts   = ro.list_actions(type="backfill", status="failed")
```

## Wiring credentials to a CI agent

1. Create/reuse a SELECT-only role:
   `GRANT SELECT ON series, series_snapshot, check_definition, check_run,
    issue, issue_event, action TO <role>;`
2. Store its DSN as a secret; expose it to the agent's process env as
   `WATCHDOG_READONLY_PG_DSN` (for claude-code-action: via `settings.env`).
3. The agent calls `ReadOnly.from_env()` — nothing else to configure.
