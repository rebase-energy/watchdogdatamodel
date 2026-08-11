# watchdogdatamodel

A PostgreSQL schema and a Python package for tracking the quality of
timeseries data. It covers the full loop: **detect** problems with checks,
**track** each problem from open to resolved, **remediate** through actions,
and **verify** the fix against the data itself. Since 2026-08-10 it runs in
production under Rebase's grid-map watchdog, where it detects, heals, and
verifies hundreds of data issues per day.

It is a template, not a shared service: each product deploys its own copy of
the schema. The model provides what every data-quality operation shares —
identity, deduplication, lifecycle, audit history, agent access — while each
product supplies its own vocabulary: which series to watch, which checks to
run, which action types exist, which workflow stages its board shows.
Intended adopters beyond the grid map: energydb operations, energy and
weather forecast quality control, customer-facing data-health pages.

## Install

```bash
pip install "watchdogdatamodel @ git+https://github.com/rebase-energy/watchdogdatamodel@v0.7.0"
```

(Not on PyPI yet. Requires PostgreSQL ≥ 14 and Python ≥ 3.11.)

```python
from watchdogdatamodel.store import db
conn = db.connect(dsn)
db.bootstrap(conn)        # creates the 7 tables; idempotent
```

## The model — seven tables, one spine

```
series ── catalog of watched timeseries
  └── series_snapshot   latest fetched data per series
check ─── catalog of tests
  └── check_run         one execution over a declared scope
        └── issue       one problem, open → resolved
              ├── issue_event   append-only diary
              └── action        remediation work queue
```

**`series`** — the catalog of watched timeseries. Each row stores
[timedatamodel](https://github.com/rebase-energy/timedatamodel) metadata
(name, unit, frequency, timezone) plus free-form product labels such as
`{zone: SE-SE1, source: entsoe}`. When the same signal is fetched from
several sources, each signal × source pair is its own row, because each feed
can break independently.

**`check`** — the catalog of tests that can run (freshness, value range,
agreement between sources…). Each check has a stable, human-chosen string id;
the code that performs the check lives in the product, not in this database.

**`check_run`** — one execution of checks over a scope declared before
running. Passing results are not stored: a series counts as healthy when a
completed run covered it and no issue is open for it. A run that fails to
fetch a series records that failure as an issue, so an absent result is never
ambiguous.

**`issue`** — one problem, opened by a check, a human, or an agent. Every
issue carries a fingerprint (typically series + check); a partial unique
index allows at most one open issue per fingerprint, so repeat detections
update the existing issue instead of duplicating it. A problem that returns
after resolution becomes a new issue linked to its predecessor. State is
`open`/`resolved`; a separate free-text stage holds the product's workflow
step. Resolving requires a reason (`fixed`, `recovered`, `false_positive`, …).

**`issue_event`** — an append-only diary of everything that happens to an
issue: opened, detected again, stage changed, fix attempted, external tracker
news, resolved. Each row records the time and the actor.

**`action`** — a typed work queue of remediation attempts, always attached to
an issue. Products register their own types (the watchdog ships `backfill`
and `agent_investigation`); status moves queued → running → succeeded or
failed, with a full transition log on the row. Finishing an action never
resolves the issue — only a clean covering check run does.

**`series_snapshot`** — the most recently fetched window of values per
series, overwritten on every fetch. A working copy for checks and UIs, not an
archive; when an issue opens, the affected slice is frozen into the issue as
evidence.

## Guarantees, enforced by construction

These are database constraints, triggers, and session settings — not
conventions — and each has a contract test:

- at most **one open issue per fingerprint** (partial unique index)
- at most **one live action per issue and type** (partial unique index)
- the diary is **append-only** (trigger rejects UPDATE/DELETE)
- **terminal actions are frozen** (trigger rejects further mutation)
- **resolution requires a reason** (CHECK constraint)
- the store is **thread-safe on a shared connection** (per-connection
  transaction lock; 64-thread contract test)
- the read-only SDK **cannot write** (SELECT-only role recommended, plus a
  server-enforced read-only session, plus a surface with no write functions)

## Reading the database — the SDK and the agent layer

`watchdogdatamodel.readonly` is how agents and scripts read a deployment
safely — no SQL, no writes possible:

```python
from watchdogdatamodel.readonly import ReadOnly

ro = ReadOnly.from_env()                      # WDM_READONLY_PG_DSN
ro.list_issues(check_id="freshness")          # dicts for code
print(ro.investigation_brief(issue_id))       # prompt-ready markdown for agents:
                                              # issue + timeline + past fixes with
                                              # conclusions + lineage + related
```

The composites (`investigation_brief`, `history`, `situation`, `summary`)
render token-budgeted markdown with stable section headers — an investigating
agent's whole context in one call. Real rendered examples for every function:
[docs/agent-sdk.md](docs/agent-sdk.md).

## External trackers and agent executors

Products usually mirror investigations onto an external tracker (GitHub,
Jira, Linear…). `watchdogdatamodel.trackers` enforces the protocol that keeps
the two systems honest — every rule of which was a production bug before it
was code: diary-first facts, duplicate-delivery dedup, external close frees
the concurrency slot, deliverables attach without finishing the engagement,
and poll-based reconciliation for lost webhook deliveries. Correlation is by
UUID stamp (`trackers.stamp` / `find_stamp`), never by parsing prose.

On top of it, [docs/executor-pattern.md](docs/executor-pattern.md) defines
how agent runtimes plug in interchangeably — hosted workflow, bot, or your
own harness — with the model itself as the work queue. Adding a backend
never changes the model, the protocol, or the work-order format.

## Example lifecycle

A scheduled run finds a gap in a Swedish production series. No open issue
matches the fingerprint, so one opens, with the broken window frozen into it
as evidence. A `backfill` action runs and fails — the source has nothing for
that window. The next run sees the gap again and stamps the existing issue
rather than opening a second one. An `agent_investigation` action follows:
the agent reads the issue's diary through the SDK, finds a parser bug, opens
a stamped pull request. The merge lands in the diary, but the issue stays
open until a targeted run re-checks the series and finds the gap gone; it
then resolves with reason `recovered`. When the gap returns months later, a
new issue opens with a predecessor link back to this one.

## timedatamodel

Series metadata, snapshots, and evidence use
[timedatamodel](https://github.com/rebase-energy/timedatamodel), Rebase's
shared package for describing timeseries: metadata plus data shapes,
including versioned (overlapping) forecast data where each value has both a
valid time and a knowledge time. Forecasts are a native shape here — forecast
issues need nothing special — and any system that already speaks
timedatamodel (TimeDB, energydb) is compatible by construction.

## Documentation map

| Doc | Contents |
|---|---|
| [docs/adopters-guide.md](docs/adopters-guide.md) | adopt the model in five steps, with a runnable example |
| [docs/agent-sdk.md](docs/agent-sdk.md) | the read-only SDK, real output examples for every function |
| [docs/executor-pattern.md](docs/executor-pattern.md) | interchangeable agent executors |
| [docs/specs/2026-08-07-quality-ops-data-model-design.md](docs/specs/2026-08-07-quality-ops-data-model-design.md) | the full design: every table, field, rule |
| [docs/specs/2026-08-10-agent-readonly-layer-design.md](docs/specs/2026-08-10-agent-readonly-layer-design.md) | the agent layer design |
| [docs/presentation.html](docs/presentation.html) | a 6-slide visual introduction |
| [BRAINSTORM.md](BRAINSTORM.md) | decision history + a survey of how OpenMetadata, DataHub, Soda, Sentry solve the same problems |

## Versions

| | |
|---|---|
| v0.1 | core schema, stores, contract tests; thread-safe store (0.1.2) |
| v0.2 | read-only SDK (`ReadOnly`) |
| v0.3 | agent layer: `investigation_brief`, `history`, `situation`, `summary`; pooled-DSN support (0.3.1) |
| v0.4 | trackers protocol (five rules, contract-tested) |
| v0.5 | protocol v2: canonical kinds, UUID stamping, attach-not-finish deliverables, reconcile grace window |
| v0.6 | executor support: `trackers.claim_next` (queue-claim with `max_inflight`), `ReadOnly.work_order` |
| v0.7 | rule 6: `trackers.deliver_findings` — diagnosis is a deliverable; engagement end never closes the ticket |

## Tests

```bash
uv run python -m pytest
```

DB-backed tests need `WDM_TEST_PG_DSN` pointing at a **throwaway** database
whose name ends in `_test` — they DROP and recreate the model's tables.
