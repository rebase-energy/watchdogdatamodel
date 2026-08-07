# watchdogdatamodel

A PostgreSQL schema and a small Python package for tracking the quality of
timeseries data. It covers the full loop: detect problems with checks, track
each problem from open to resolved, remediate through actions, and verify the
fix against the data itself.

The model was extracted from the grid map's internal watchdog tool and
generalized. It is a template, not a shared service: each product deploys its
own copy of the schema. The grid-map watchdog is the first consumer; energydb
operations, forecast quality control, weather data quality, and
customer-facing data-health pages are intended to follow. The schema provides
what all of these share — identity, deduplication, lifecycle, audit history —
while each product supplies its own vocabulary: which series to watch, which
checks to run, which action types exist, and which workflow stages its board
shows.

## Tables

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
timedatamodel metadata (name, unit, frequency, timezone) plus free-form
product labels such as `{zone: SE-SE1, source: entsoe}`. When the same signal
is fetched from several sources, each signal × source pair is its own row,
because each feed can break independently.

**`check`** — the catalog of tests that can run, such as freshness, value
range, or agreement between sources. Each check has a stable, human-chosen
string id; the code that performs the check lives in the product, not in this
database.

**`check_run`** — one execution of checks over a scope (which series, which
checks) declared before running. Passing results are not stored: a series
counts as healthy when a completed run covered it and no issue is open for it.
A run that fails to fetch a series records that failure as an issue, so an
absent result is never ambiguous.

**`issue`** — one problem, opened by a check, a human, or an agent. Every
issue carries a fingerprint, a short text identity (typically series + check);
a partial unique index allows at most one open issue per fingerprint, so a
repeat detection updates the existing issue instead of creating a duplicate.
A problem that returns after resolution becomes a new issue linked to its
predecessor. State is `open` or `resolved`; a separate free-text stage field
holds the product's own workflow step. Resolving requires a reason (`fixed`,
`recovered`, `false_positive`, ...).

**`issue_event`** — an append-only diary of everything that happens to an
issue: opened, detected again, stage changed, action finished, resolved. Each
row records the time and the actor.

**`action`** — a typed work queue of remediation attempts, always attached to
an issue. Products register their own types (the watchdog ships `backfill` and
`agent_investigation`); status moves queued → running → succeeded or failed,
and at most one live action per issue and type is allowed. Finishing an action
never resolves the issue — only a clean covering check run does.

**`series_snapshot`** — the most recently fetched window of values for each
series, overwritten on every fetch. It is a working copy for checks and UIs,
not an archive; when an issue opens, the affected slice of data is copied into
the issue as evidence and frozen there.

## Example lifecycle

A scheduled run finds a gap in a Swedish production series. No open issue
matches the fingerprint, so one opens, with the broken window frozen into it
as evidence. A `backfill` action runs and fails — the source has nothing for
that window. The next run sees the gap again and stamps the existing issue
rather than opening a second one. An `agent_investigation` action succeeds:
the agent finds a parser bug and opens a pull request, recorded in the
action's outcome. The merge is noted in the issue's diary, but the issue stays
open until a targeted run re-checks the series and finds the gap gone; the
issue then resolves with reason `recovered`. When the gap returns months
later, a new issue opens with a predecessor link back to this one.

## timedatamodel

Series metadata, snapshots, and evidence use
[timedatamodel](https://github.com/rebase-energy/timedatamodel), Rebase's
shared package for describing timeseries: metadata plus data shapes, including
versioned (overlapping) forecast data in which each value has both a valid
time and a knowledge time. Because forecasts are a native shape, forecast
issues need nothing special in this model, and any system that already speaks
timedatamodel — TimeDB, energydb — is compatible by construction.

## Status

Core implemented: the SQL schema (`src/watchdogdatamodel/schema.sql`), the
Python package (models plus helpers for fingerprints, coverage, events, and
the action lifecycle), and a contract-test suite that verifies the model's
guarantees against a real PostgreSQL. Adoption by the grid-map watchdog is the
next phase.

- Getting started: [docs/adopters-guide.md](docs/adopters-guide.md)
- Full design: [docs/specs/2026-08-07-quality-ops-data-model-design.md](docs/specs/2026-08-07-quality-ops-data-model-design.md)
- Decision history and prior-art survey: [BRAINSTORM.md](BRAINSTORM.md)

Tests: `uv run python -m pytest` — DB-backed tests need `WDM_TEST_PG_DSN`
pointing at a throwaway database whose name ends in `_test` (they DROP and
recreate the model's tables).
