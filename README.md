<div align="center">

# WatchdogDataModel

**A generalized data model for timeseries data-quality operations —
detect issues, track their life, act on them, and trust resolution only when the data proves it.**

</div>

---

Every timeseries product ends up rebuilding the same machinery: run checks on
the data, remember what was found, chase the fixes, answer "is it healthy
now?". This repo is that machinery as a **blueprint** — a database schema and
a thin Python package that any product deploys as its own copy. The product
brings its vocabulary (its series, its checks, its fix types, its board
columns); the model brings everything those things have in common.

Born from Rebase's grid-map **watchdog** (its first consumer), designed for
any timeseries quality workload: energydb operations, energy and weather
forecast QC, customer-facing health pages.

## The essence

Seven tables, one spine. Every row reaches `issue` or `series` in one hop by a
real foreign key.

```
   what we watch          when we judged            what went wrong
  ┌──────────────┐      ┌───────────────┐      ┌──────────────────────┐
  │    series    │◄─────│   check_run   │─────►│        issue         │
  │  (catalog)   │      │ (scope+cover) │      │ (any origin, staged) │
  └──────┬───────┘      └───────────────┘      └───────┬──────────────┘
         │                                             │
  ┌──────▼─────────┐    ┌───────────────┐      ┌───────▼──────┐
  │ series_snapshot│    │     check     │      │ issue_event  │  append-only diary
  │ (working copy) │    │   (catalog)   │      ├──────────────┤
  └────────────────┘    └───────────────┘      │    action    │  typed work queue
                                               └──────────────┘
```

| table | one line |
|---|---|
| `series` | catalog of monitored timeseries — [timedatamodel](https://github.com/rebase-energy/timedatamodel) metadata + free-form product labels; one row per (signal × source) |
| `check` | catalog of checks — stable string ids; the code lives in the product |
| `check_run` | one execution of checks over a **declared scope** — coverage makes silence meaningful |
| `issue` | one problem, from a check, a human, or an agent — fingerprint-deduped, incident-model recurrence |
| `issue_event` | the issue's diary — append-only, everything that ever happened to it |
| `action` | the work queue — typed remediation processes (`backfill`, `agent_investigation`, …) with mutable status and a full transition log |
| `series_snapshot` | the working copy of the data the checks actually saw — latest window per series, what UIs plot |

## The rules that make it work

1. **Machinery in the core, vocabulary in the product.** The schema never says
   "zone", "backfill", or "sweep". Labels, check ids, action types, and board
   stages are product-registered strings.
2. **Declared coverage instead of stored silence.** No "everything's fine"
   rows. A run declares its scope up front; *healthy = covered by a completed
   run + no open issue*. A source that couldn't be fetched is itself an issue.
3. **Dedup by constraint, not discipline.** At most one open issue per
   fingerprint, at most one live action per (issue, type) — enforced by the
   database, impossible to violate.
4. **Incident-model recurrence.** Open issues absorb repeat detections; a
   problem returning after resolution opens a *new* issue linked to its
   predecessor. Incident counts and time-to-resolution stay honest.
5. **Append-only diary, mutable processes.** Events are facts and never
   change; actions are state machines that freeze at their terminal status.
6. **Data truth over process truth.** A merged PR or a successful backfill
   never resolves an issue. Only a check run that covers the series and finds
   nothing does.

## One issue's life

> A scheduled run finds a gap → **issue opens**. The auto-heal rule enqueues a
> `backfill` **action** — it fails. A human requests `agent_investigation`;
> the agent diagnoses a parser bug and opens a PR — the action succeeds, the
> link lives in its outcome. The ref-watcher sees the merge → an
> `external_changed` **event**; the issue moves to *awaiting verification*.
> A targeted run re-checks the series, the gap is gone → `not_seen` →
> **resolved: recovered**. Months later the gap returns → a **new issue**,
> pointing at this one as its predecessor.

Every step is one insert or one small update, and the whole story reads back
from a single `issue_id`.

## Forecasts are first-class

Series identity and snapshot payloads use
[timedatamodel](https://github.com/rebase-energy/timedatamodel) — the same
shapes as TimeDB and energydb. An `OVERLAPPING` series carries
`knowledge_time` natively, so "forecast run 06:00 is missing" and "yesterday's
observation has a gap" are the same kind of issue on the same spine.

## Status

**Design approved, implementation upcoming.**

- 📐 Full design: [`docs/specs/2026-08-07-quality-ops-data-model-design.md`](docs/specs/2026-08-07-quality-ops-data-model-design.md)
- 🧠 Decision history & OSS research (OpenMetadata, DataHub, Soda, Elementary,
  StackStorm, Sentry Seer, …): [`BRAINSTORM.md`](BRAINSTORM.md)

Planned deliverables: SQL DDL, `watchdogdatamodel` Python package (pydantic
models + fingerprint / coverage / event / action-lifecycle helpers), and the
contract tests that make the guarantees above testable in any deployment.
