# Adopter's guide

How a product adopts the model, in five steps. The full design is in
[`specs/2026-08-07-quality-ops-data-model-design.md`](specs/2026-08-07-quality-ops-data-model-design.md).

Two rules to internalize before writing any code:

1. **Healthy = covered by a completed run + no open issue.** Nothing stores
   "everything was fine"; a run declares its scope up front, and silence
   inside a completed run's scope means healthy. If you could not fetch a
   series, record that as an issue — never skip it silently.
2. **Finishing an action never resolves an issue.** Only a later run that
   covers the series and finds nothing should lead to resolution
   (`record_not_seen` + your policy calling `resolve(..., reason="recovered")`).

## The five steps

1. **Create a database and bootstrap the schema.** One database (or schema)
   per product — this is a template, not a shared service.
   `store.db.connect(dsn)` then `store.db.bootstrap(conn)`.
2. **Register your series** with `store.series.upsert_series`, choosing a
   stable `key` and your own `labels` vocabulary. One row per signal × source.
3. **Register your checks** with `store.checks.upsert_check`, choosing stable,
   human-picked string ids. The check code stays in your product.
4. **Run your detection cycle**: `store.runs.start_run` (declare scope) →
   fetch data, `store.series.upsert_snapshot` each series → run your checks →
   `store.issues.open_or_touch` for each detection (freeze evidence into
   `details` with `watchdogdatamodel.excerpt`) and
   `store.issues.record_not_seen` for open issues your run covered but did not
   re-detect → `store.runs.finish_run`.
5. **Register action types and run a worker loop**: enqueue with
   `store.actions.enqueue`; a worker polls `store.actions.claim_next(type,
   worker=...)`, does the work, and calls `store.actions.finish` with a
   machine-readable `outcome` (external links — PR URLs — go here).

## Complete runnable example

Requires `WDM_PG_DSN` pointing at an empty database.

```python
import os
from datetime import datetime, timedelta, timezone

from timedatamodel import TimeSeries
from watchdogdatamodel import compute_fingerprint, excerpt
from watchdogdatamodel.store import actions, checks, db, issues, runs, series

conn = db.connect(os.environ["WDM_PG_DSN"])
db.bootstrap(conn)

# 2. Register a series (your labels, your vocabulary)
s = series.upsert_series(
    conn, key="se-se1-production-entsoe", name="SE-SE1 production (ENTSO-E)",
    unit="MW", frequency="PT1H", data_type="OBSERVATION",
    labels={"zone": "SE-SE1", "data_type": "production", "source": "entsoe"},
)

# 3. Register a check
checks.upsert_check(conn, id="gap", name="Gap detection", dimension="completeness")

# 4. One detection cycle
run = runs.start_run(conn, scope={"series": "all", "checks": "all"}, trigger="scheduled")
t0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
fetched = TimeSeries.from_list(
    {"valid_time": [t0 + timedelta(hours=i) for i in range(6)],
     "value": [1.0, 2.0, None, None, 5.0, 6.0]},  # a gap at hours 2-3
    name=s.name, unit="MW",
)
series.upsert_snapshot(conn, series_id=s.id, ts=fetched, run_id=run.id)

if fetched.has_missing:  # your check logic lives in your product
    issue, opened = issues.open_or_touch(
        conn, fingerprint=compute_fingerprint(s.key, "gap"), origin="check",
        title=f"gap in {s.key}", actor=f"run:{run.id}", series_id=s.id,
        check_id="gap", run_id=run.id,
        valid_start=t0 + timedelta(hours=2), valid_end=t0 + timedelta(hours=3),
        details={"evidence": excerpt(fetched, t0, t0 + timedelta(hours=5))},
    )
runs.finish_run(conn, run.id, stats={"series_checked": 1, "issues_opened": int(opened)})

# 5. Remediate: enqueue, work, finish — then verify with a targeted run
action, _ = actions.enqueue(conn, issue.id, "backfill", requested_by="rule:auto_heal")
job = actions.claim_next(conn, "backfill", worker="worker:demo")
actions.finish(conn, job.id, status="succeeded", by="worker:demo", outcome={"rows": 2})

verify = runs.start_run(conn, scope={"series": {"ids": [str(s.id)]}, "checks": ["gap"]},
                        trigger="targeted")
issues.record_not_seen(conn, issue.id, run_id=verify.id, actor=f"run:{verify.id}")
runs.finish_run(conn, verify.id)
issues.resolve(conn, issue.id, reason="recovered", actor="rule:auto_heal")

for e in issues.list_events(conn, issue.id):
    print(f"{e.at:%H:%M:%S} {e.type:18s} {e.actor}")
```

Expected output (timestamps vary):

```
12:00:00 opened             run:<uuid>
12:00:00 action_requested   rule:auto_heal
12:00:00 action_finished    worker:demo
12:00:00 not_seen           run:<uuid>
12:00:00 resolved           rule:auto_heal
```

## Product-side concerns the core deliberately leaves to you

- The auto-resolve policy (when `not_seen` leads to `resolve`).
- Action *handlers* — the code that executes `backfill`, `agent_investigation`, …
- A ref-watcher that turns external news (PR merged) into
  `issues.add_event(..., type="external_changed", ...)`.
- UI caches, notification routing, settings, deploy stamps.
- Reporter PII for human reports: keep it in a product-side table that
  references `issue.id` (insert-only discipline; see spec §3.4).
