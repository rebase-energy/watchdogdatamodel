<div align="center">

# WatchdogDataModel

**A database design for keeping timeseries data healthy: find problems in the
data, keep track of them, fix them, and only call a problem solved when the
data itself says so.**

</div>

---

## The problem this solves

Imagine a product that collects timeseries data — electricity production per
country, prices, weather forecasts — from many external sources. Things go
wrong all the time: a source stops publishing, a feed develops a gap, two
sources disagree about the same quantity, a parser silently starts returning
wrong numbers.

Someone, or something, has to:

1. **notice** these problems,
2. **remember** them — which ones are new, which are known, which were fixed,
3. **do something** about them — refetch the data, or ask a person or an AI
   agent to investigate,
4. and **confirm** the fix actually worked.

At Rebase we built this once, for the grid map, as an internal tool called the
**watchdog**. It worked, but its database grew organically and was tied to
grid-map concepts (zones, sweeps, backfills). Meanwhile other products —
energydb operations, forecast quality monitoring, customer-facing data health
pages — need the exact same machinery.

This repository is that machinery, redesigned from scratch as a reusable
model. It contains a database schema (PostgreSQL), a small Python library to
work with it, and documentation. It is a **template, not a service**: each
product creates its own copy of this database. There is no shared central
system.

The product supplies the specifics — *which* series to watch, *which* checks
to run, *what* kinds of fixes exist, *what* columns its board shows. The model
supplies everything those specifics have in common: identity, history,
deduplication, lifecycle, and the link between problems and fixes.

## The model, explained from scratch

The database has seven tables. Here they are in the order a piece of data
would meet them.

### 1. `series` — the list of things being watched

A *series* is one timeseries you care about: "electricity production in
northern Sweden, as reported by the ENTSO-E feed". Each series is one row,
holding its metadata — name, unit, expected frequency (hourly, daily…),
timezone — plus a set of free-form **labels**.

Labels are how each product speaks its own language without changing the
schema. The grid map labels its series with things like
`{zone: SE-SE1, data_type: production, source: entsoe}`; a weather product
might use `{model: ecmwf, parameter: wind_speed, station: arlanda}`. The
schema itself has no opinion — a label is just a name and a value, and you can
search by any combination.

One important convention: if the same real-world quantity is fetched from
several sources (the grid map fetches Swedish production from energydb, from
its own ENTSO-E parser, *and* from the public API), each of those is a
**separate series row**, distinguished by its `source` label. That's because
each feed can break independently — the health of one says nothing about the
health of another.

The metadata format follows [timedatamodel](https://github.com/rebase-energy/timedatamodel),
Rebase's shared vocabulary for describing timeseries (more on that below).

### 2. `check` — the list of tests that can run

A *check* is one kind of test: "is the data fresh?", "are values inside a
plausible range?", "do two sources agree?". Each check is a row with a stable,
human-chosen id (`freshness`, `gross_range`, `source_divergence`), a
description, and default parameters.

The code that *performs* a check lives in the product, not in this database.
The row exists so that everything else — results, problems, statistics — can
refer to a check unambiguously, today and in two years. (The ids are chosen
by people, deliberately: tools that derive check identity from the check's
configuration lose all history the moment someone edits a threshold. We
learned this from other projects' mistakes.)

### 3. `check_run` — one round of checking

Every time checks execute — the nightly full scan, a quick re-test of a single
series after a fix, an on-demand run — one `check_run` row records it: when it
started and finished, whether it completed, what triggered it, and — most
importantly — its **scope**: which series and which checks it *intended* to
cover, declared before running.

Why declare scope? Because this model does **not** store "everything was
fine" results (a design choice that keeps the database small — the old
watchdog's biggest table was thousands of rows of "still OK"). But if you only
store problems, silence is ambiguous: does "no problem recorded for this
series" mean *healthy*, or *never looked*? The declared scope removes the
ambiguity:

> **A series is healthy if a completed run covered it and no problem is open
> for it.**

If a run *couldn't* examine a series — the source was unreachable — that is
not silently skipped; failing to fetch is itself a data-quality problem, and
it is recorded as one (see the next table).

### 4. `issue` — one problem, from discovery to resolution

An *issue* is one problem in the data: "this series has a gap", "these two
sources diverge", "a user reported wrong values in Spain". Issues are the
heart of the model. A few things make them work:

**Issues can come from anywhere.** Most are opened by checks, but humans
(a report form in the UI) and AI agents can open them too. The `origin` field
says who. Check-opened issues carry structured fields (which series, which
check, which time range of the data is affected); human- and agent-opened
issues get a free-form details field, because their content varies by product.

**No duplicates, by construction.** Every issue carries a *fingerprint* — a
short text identity, typically "series + check" (for example
`SE-SE1-production-entsoe|freshness`). A database constraint permits **at most
one open issue per fingerprint**. When the nightly run sees the same gap it
saw yesterday, it cannot create a second issue; the existing one is updated
("seen again, still here"). Duplicate tracking is impossible rather than
discouraged.

**A returning problem is a new issue, linked to the old one.** When an issue
is resolved and the same problem reappears next month, the model opens a
*fresh* issue pointing at its predecessor — it does not reopen the old one.
This mirrors how incident tools work, and for a reason: a gap in January
(source outage) and a gap in June (parser bug) are different problems with
different causes and fixes. Chaining them by reference keeps the full history
of a series one query away, while keeping every incident's story, cause, and
duration separate — which is exactly what you need to answer "how many
incidents did we have?" and "how fast do we resolve them?".

**Two levels of status.** The core model tracks only `open` or `resolved` —
that's all its logic needs. Alongside it, a free-text `stage` field holds the
product's own workflow step ("triage", "healing", "awaiting verification") —
the columns of a kanban board. Products define whatever stages they like; the
schema doesn't change.

**Resolving requires a reason.** `fixed`, `recovered` (came back healthy on
its own), `false_positive`, `wont_fix`… Six months later, this field is how
you discover which checks are trustworthy and which just make noise.

### 5. `issue_event` — the diary of each issue

Everything that ever happens to an issue is one row here, written once and
never modified: *opened*, *detected again*, *stage changed*, *a fix was
requested*, *a fix finished*, *a pull request was merged*, *no longer
detected*, *resolved*. Each entry records when, what, and who — a person, a
check run, an automation rule, or an AI agent.

This is the audit trail. The issue row answers "where do things stand now?";
its events answer "how did we get here?". Two different questions, two
different shapes of data — that's why they are two tables.

### 6. `action` — work being done to fix things

An *action* is a concrete attempt to fix an issue: refetch a time window from
the source, or hand the issue to an AI agent that investigates and opens a
pull request, or notify someone. Each action is a row with a **type**, its
input parameters, a status that moves `queued → running → succeeded/failed`,
and an outcome.

Like labels and stages, action types belong to the product: this repo's
schema has no idea what a "backfill" is. The grid map registers `backfill` and
`agent_investigation`; a forecast product might register `rerun_model`. What
the model provides is what every type needs anyway: a queue (workers ask "any
queued actions of my type?"), a status history (every transition is stamped
into the row), protection against double work (the database refuses a second
running action of the same type on the same issue), and the connection to the
issue's diary.

One principle deserves emphasis, because it's easy to get wrong:

> **Finishing an action never resolves an issue. Only the data can do that.**

Example: an AI agent investigates a gap, finds a parser bug, opens a pull
request. The action is now *succeeded* — the agent delivered. The PR link is
stored in the action's outcome. Later, when the PR is merged, that news lands
in the issue's diary. But the issue stays open until a check run examines the
series again and finds the gap actually gone. Merged code that doesn't fix the
data — it happens more often than anyone likes — never gets to claim victory.

### 7. `series_snapshot` — the data itself, most recent copy

Checks need data to check, and people looking at an issue need to *see* the
data. This table holds, for each series, the most recently fetched window of
values — replaced on every fetch, never accumulated. It is a working copy for
operations and for the UI, deliberately not an archive: the authoritative
history lives in the product's real data stores (TimeDB, energydb). Keeping
full history here would slowly turn a quality-tracking database into a second
timeseries database, and we refuse.

But a working copy that keeps moving forward raises a question: when someone
opens a three-day-old issue, the snapshot no longer shows what the problem
looked like. So at the moment an issue is opened, the affected slice of data
is copied — small, just the broken window — into the issue itself, frozen
forever. The snapshot shows *now*; the issue shows *then*.

## How it fits together

```
   series ─────────── what we watch (catalog, with product labels)
     │
     ├── series_snapshot   the latest fetched data for each series
     │
   check ────────────── how we judge (catalog of tests)
     │
   check_run ────────── one round of checking, with declared scope
     │
     └──► issue ─────── one problem, open → resolved, deduplicated
             │
             ├── issue_event   append-only diary: everything that happened
             │
             └── action        queued/running/succeeded work to fix it
```

A worked example, start to finish:

> The nightly run covers all series. It finds a gap in a Swedish production
> feed; no open issue matches that fingerprint, so an issue opens, with the
> broken window frozen into it as evidence. An automation rule queues a
> `backfill` action; it runs and fails — the source has nothing to give.
> The next night's run sees the gap again: the same issue is stamped "seen
> again", no duplicate created. A human moves it to an agent: an
> `agent_investigation` action runs, the agent finds a parser bug and opens a
> pull request; the action succeeds with the PR link in its outcome. The PR
> merges — the diary records it, the issue moves to the "awaiting
> verification" stage. A targeted run re-checks just that series: gap gone.
> The diary records "no longer detected" and the issue resolves with reason
> `recovered`. Four months later the gap comes back: a new issue opens,
> pointing at this one as its predecessor.

Seven tables, and the entire story above reads back from one issue id.

## About timedatamodel

[timedatamodel](https://github.com/rebase-energy/timedatamodel) is Rebase's
open-source package for describing timeseries: a series' metadata (name, unit,
frequency, timezone) and its data shapes — including *versioned* data, where
every forecast run re-predicts the same future hours and each value therefore
has both a "valid at" and a "known at" time.

This model uses timedatamodel for the `series` catalog and for snapshot
payloads. The practical consequence: forecast data needs nothing special here.
"The 06:00 forecast run is missing" and "yesterday's observations have a gap"
are the same kind of issue in the same tables — and any system that already
speaks timedatamodel (TimeDB, energydb) is compatible with this model by
construction.

## Status

**Design approved; implementation starting.**

| document | contents |
|---|---|
| [`docs/specs/2026-08-07-quality-ops-data-model-design.md`](docs/specs/2026-08-07-quality-ops-data-model-design.md) | the full design: every table, every field, every rule |
| [`BRAINSTORM.md`](BRAINSTORM.md) | how we got here: the decisions, and a survey of how OpenMetadata, DataHub, Soda, Elementary, StackStorm, and Sentry's AI agent solve the same problems |

Planned deliverables: the SQL schema, a Python package (models + helpers for
fingerprints, coverage, events, and the action lifecycle), and a contract-test
suite that verifies the guarantees described above in any deployment.
