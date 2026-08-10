# Agent guide to the read-only SDK

`watchdogdatamodel.readonly` is how agents (and scripts) read a wdm database
safely: no SQL, no writes possible. Three walls: use a SELECT-only role, the
session forces `default_transaction_read_only`, and this surface has no write
functions. Every example output below is real (captured from a production
deployment, lightly truncated with `…`).

## Connect

```python
from watchdogdatamodel.readonly import ReadOnly

ro = ReadOnly.from_env()   # WDM_READONLY_PG_DSN or WATCHDOG_READONLY_PG_DSN
```

## Composites — prompt-ready markdown for agents

Read `investigation_brief` like an investigator: `Already tried` tells you
what to rule out (a failed backfill renders its conclusion line), `Past
incidents` whether a previous fix bounced, `Related` whether it's isolated or
systemic (siblings failing too = the whole feed).

### `investigation_brief`

```python
ro.investigation_brief(issue_id)   # budget="compact"|"full"
```

```markdown
## Issue
**source_divergence** on `SI:consumption:energy_charts` (data_type=consumption, priority=2, short=si, source=energy_charts, zone=SI)
- state: open / new · severity high
- first seen 2026-08-10T09:55, last 2026-08-10T11:02
- affected data window: 2026-08-05T22:00 → 2026-08-08T07:00

## Timeline
- 2026-08-10T09:55 opened · run:08b7e1aa-0387-4ea8-89e9-ea3538358cfd
- detected again ×3 (2026-08-10T10:15 → last)

## Already tried
Nothing yet.

## Past incidents
None — first occurrence.

## Related
- SI:production.unknown:energy_charts · source_divergence (high)
- SI:production.coal:energy_charts · source_divergence (high)
- SI:production.gas:energy_charts · source_divergence (high)
- SI:production.solar:energy_charts · source_divergence (high)
- SI:production.wind:energy_charts · source_divergence (high)
- … more omitted

## Data
- window 2026-08-03T11:00 → 2026-08-10T07:00, fetched 2026-08-10T11:02
- points 165, nulls 0
…
```

### `history`

```python
ro.history(issue_id)
```

```markdown
## Issue
**source_divergence** on `SI:consumption:energy_charts` (data_type=consumption, priority=2, short=si, source=energy_charts, zone=SI)
- state: open / new · severity high
- first seen 2026-08-10T09:55, last 2026-08-10T11:02
- affected data window: 2026-08-05T22:00 → 2026-08-08T07:00

## Timeline
- 2026-08-10T09:55 opened · run:08b7e1aa-0387-4ea8-89e9-ea3538358cfd
- detected again ×3 (2026-08-10T10:15 → last)

## Past incidents
None.
…
```

### `work_order` (v0.6)

```python
ro.work_order(issue_id)   # budget="compact"|"full"
```

The complete file-ready work order for one investigation:
`investigation_brief` + `---` + `situation`, in one call. Executors write it
into the agent's sandbox as its entire context (see
[executor-pattern.md](executor-pattern.md)); the output is exactly the two
composites above concatenated, so no separate example here.

### `situation`

```python
ro.situation(labels={"zone": "SI"})   # or check_id="timing_gaps"
```

```markdown
## Open issues (10)

### source_divergence (10)
- SI:consumption:energy_charts — high, last seen 2026-08-10T11:02
- SI:production.unknown:energy_charts — high, last seen 2026-08-10T11:01
- SI:production.coal:energy_charts — high, last seen 2026-08-10T11:01
- SI:production.gas:energy_charts — high, last seen 2026-08-10T11:01
- SI:production.solar:energy_charts — high, last seen 2026-08-10T11:01
- … 5 more omitted

## Recent runs
- completed · scheduled · started 2026-08-10T10:56
- completed · scheduled · started 2026-08-10T10:26
- completed · scheduled · started 2026-08-10T10:08
…
```

### `summary`

```python
ro.summary()
```

```markdown
## Watchdog summary
Open issues by check × severity:
- thin_range: 105 (medium)
- source_divergence: 68 (high)
- timing_gaps: 20 (high)
- gross_range: 19 (high)
- stale_vs_upstream: 6 (high)
- null_run: 1 (high)
By stage: wip=1, new=218
Last run: completed (scheduled) started 2026-08-10T10:56
```

## Raw readers — JSON-able dicts for code

### `list_issues`

```python
ro.list_issues(state="open", check_id="source_divergence", labels=None, limit=100)
```

```json
[
  {
    "id": "d55c9d0d-a41d-43c7-a0f9-26000f85c7eb",
    "fingerprint": "SI:consumption:energy_charts|source_divergence",
    "origin": "check",
    "series_id": "dc61a385-6470-433a-8b54-697f715f6637",
    "related_series": [],
    "check_id": "source_divergence",
    "state": "open",
    "stage": "new",
    "severity": "high",
    "title": "SI:consumption:energy_charts: source_divergence FAIL",
    "details": {
      "eps": "max(0.01, 1e-4\u00b7|value|)",
      "tier": 1,
      "n_bad": 26,
      "bucket": "value",
      "n_fail": 26,
…
```

### `get_issue`

```python
ro.get_issue(issue_id)   # includes events + actions
```

```json
{
  "id": "d55c9d0d-a41d-43c7-a0f9-26000f85c7eb",
  "fingerprint": "SI:consumption:energy_charts|source_divergence",
  "state": "open",
  "stage": "new",
  "severity": "high",
  "first_seen_at": "2026-08-10T09:55:43.305155+00:00",
  "last_seen_at": "2026-08-10T11:02:05.220999+00:00",
  "series_key": "SI:consumption:energy_charts",
  "events": [
    {
      "id": 387,
      "issue_id": "d55c9d0d-a41d-43c7-a0f9-26000f85c7eb",
      "at": "2026-08-10T09:55:43.305155+00:00",
      "type": "opened",
      "actor": "run:08b7e1aa-0387-4ea8-89e9-ea3538358cfd",
      "run_id": "08b7e1aa-0387-4ea8-89e9-ea3538358cfd",
      "action_id": null,
      "data": {}
    },
    {
      "id": 1262,
      "issue
…
```

### `list_events`

```python
ro.list_events(issue_id)
```

```json
[
  {
    "id": 387,
    "issue_id": "d55c9d0d-a41d-43c7-a0f9-26000f85c7eb",
    "at": "2026-08-10T09:55:43.305155+00:00",
    "type": "opened",
    "actor": "run:08b7e1aa-0387-4ea8-89e9-ea3538358cfd",
    "run_id": "08b7e1aa-0387-4ea8-89e9-ea3538358cfd",
    "action_id": null,
    "data": {}
  },
  {
    "id": 1262,
    "issue_id": "d55c9d0d-a41d-43c7-a0f9-26000f85c7eb",
    "at": "2026-08-10T10:15:46.160913+00:00",
    "type": "detected_again",
    "actor": "run:0309870d-144f-4d71-9918-3fdd5a1abe14",
    "run_id": "0309870d-144f-4d71-9918-3fd
…
```

### `list_actions`

```python
ro.list_actions(type="backfill", status="failed")
```

```json
[
  {
    "id": "107d63bc-a1af-45f5-b8a3-6a47cc93aafe",
    "issue_id": "b4f0bcbb-9867-4a75-b098-4f9edb8f66e6",
    "type": "backfill",
    "status": "failed",
    "transitions": [
      {
        "at": "2026-08-10T11:08:54.010736+00:00",
        "by": "rule:auto_heal",
        "status": "queued"
      },
      {
        "at": "2026-08-10T11:08:54.048128+00:00",
        "by": "worker:wdm-heal",
        "status": "running"
      },
      {
        "at": "2026-08-10T11:09:30.606139+00:00",
…
```

### `list_series`

```python
ro.list_series(labels={"zone": "PT"})
```

```json
[
  {
    "id": "da7122d3-68a8-4ed4-9cf7-f5891436f376",
    "key": "PT:consumption:energy_balance",
    "name": "PT consumption (energy_balance)",
    "description": null,
    "unit": "MW",
    "timezone": "UTC",
    "frequency": null,
    "data_type": "OBSERVATION",
    "timeseries_type": "FLAT",
    "labels": {
      "zone": "PT",
      "short": "pt",
      "source": "energy_balance",
      "priority": 3,
      "data_type": "consumption"
    },
    "active": true,
    "created_at": "2026-08-10
…
```

### `get_series`

```python
ro.get_series("PT:consumption:energy_charts")
```

```json
{
  "id": "dc61a385-6470-433a-8b54-697f715f6637",
  "key": "SI:consumption:energy_charts",
  "name": "SI consumption (energy_charts)",
  "description": null,
  "unit": "MW",
  "timezone": "UTC",
  "frequency": null,
  "data_type": "OBSERVATION",
  "timeseries_type": "FLAT",
  "labels": {
    "zone": "SI",
    "short": "si",
    "source": "energy_charts",
    "priority": 2,
    "data_type": "consumption"
  },
  "active": true,
  "created_at": "202
…
```

### `get_snapshot`

```python
ro.get_snapshot(series_key)   # payload omitted here for brevity
```

```json
{
  "series_id": "dc61a385-6470-433a-8b54-697f715f6637",
  "run_id": "882b6655-f994-4575-88b9-f728e3e23534",
  "fetched_at": "2026-08-10T11:02:05.149203+00:00",
  "window_start": "2026-08-03T11:00:00+00:00",
  "window_end": "2026-08-10T07:00:00+00:00",
  "stats": {
    "chart": {
      "t": [
        1785754800000,
        1785758400000,
        1785762000000,
        1785765600000,
        178576
…
```

### `list_checks`

```python
ro.list_checks()
```

```json
[
  {
    "id": "currency_match",
    "name": "Currency Match",
    "description": "Reported price currency matches the expected one.",
    "dimension": "validity",
    "default_params": {},
    "enabled": true,
    "created_at": "2026-08-10T09:45:31.634386+00:00",
    "updated_at": "2026-08-10T10:56:31.332812+00:00"
  },
  {
    "id": "duplicate_timestamps",
    "name": "Duplicate Timestamps",
```

### `list_runs`

```python
ro.list_runs(limit=5)
```

```json
[
  {
    "id": "882b6655-f994-4575-88b9-f728e3e23534",
    "status": "completed",
    "trigger": "scheduled",
    "scope": {
      "checks": "all",
      "series": "all"
    },
    "window_start": null,
    "window_end": null,
    "started_at": "2026-08-10T10:56:53.511815+00:00",
    "finished_at": "2026-08-10T11:02:10.452750+00:00",
    "stats": {
      "cells": 1643,
      "duration_s": 339.4
    },
    "metadata": {
      "app": "grid-map-wat
…
```

## Wiring credentials to a CI agent

1. Create/reuse a SELECT-only role:
   `GRANT SELECT ON series, series_snapshot, check_definition, check_run,
    issue, issue_event, action TO <role>;`
2. Store its DSN as a secret; expose it to the agent's process env as
   `WATCHDOG_READONLY_PG_DSN` (for claude-code-action: via `settings.env`).
3. The agent calls `ReadOnly.from_env()` — nothing else to configure.
