# Agent guide to reading a wdm database

Two ways in, both read-only end to end: a **CLI** for agents and humans at a
terminal, and a **library** (`watchdogdatamodel.query`) for code. Both sit
behind the same walls — a SELECT-only role is recommended, the session forces
`default_transaction_read_only=on`, and neither surface exposes a write
function at all. There is no SQL to write and no mutation possible.

**As of v0.9.0, this is the whole agent surface.** The old
`watchdogdatamodel.readonly.ReadOnly` class — pre-rendered markdown
"composites" chosen by a script — is gone. An investigating agent now gets
tools and doctrine, and decides for itself what's relevant. See "What
replaced `ReadOnly`" below if you're carrying muscle memory from v0.8.

## Agents use the CLI

Point an agent at `python -m watchdogdatamodel.cli guide` first. It prints
the packaged `AGENT.md` — the command surface, the seven tables, the five
rules that change conclusions ("`kind` is not decoration", "a PASS only
means something if a covering run actually re-checked that series", …), and
a "Start here" recipe for a bare issue id. It needs no database connection,
so it always works even when the DSN doesn't.

```bash
uv run python -m watchdogdatamodel.cli guide
```

```
# wdm — agent doctrine

## What this is

A `watchdogdatamodel` (wdm) database records data-quality problems for
timeseries: which series are being watched, what checks run against them,
what those checks found, and what has been done about it. **You read it;
you never write it.** …
```

(Full text: `src/watchdogdatamodel/AGENT.md` — it ships inside the package,
so `importlib.resources` finds it regardless of install location.)

From there the agent drives itself, e.g. for one issue:

```bash
uv run python -m watchdogdatamodel.cli issue show <id>
uv run python -m watchdogdatamodel.cli issue timeline <id>
uv run python -m watchdogdatamodel.cli series context <series-key>
uv run python -m watchdogdatamodel.cli run covering <series-key>
uv run python -m watchdogdatamodel.cli issues list --check <check-id>
```

Every command accepts `--json` for exact field values, `--limit N` (default
20, and a capped list always says what it dropped), and `--dsn` to override
the connection string (default: `$WDM_READONLY_PG_DSN` then
`$WATCHDOG_READONLY_PG_DSN`). A missing or unreachable DSN exits **2** with a
message containing `no wdm access` — that's the signal to investigate from
the issue body alone and say so, not a crash to route around.

Two real examples, captured against a local clone of production data
(`--dsn` pointed at a read replica; the CLI never mutates it):

```bash
$ uv run python -m watchdogdatamodel.cli issue list --limit 3
654515be-519c-474f-a51e-c469bd12aef2 · thin_range · kind=context · severity=medium · open/new · 2026-08-12T06:45:00.727729+00:00→2026-08-13T12:49:28.267935+00:00
  series=GB:production.solar:neso title=GB:production.solar:neso: thin_range WARN
b7251af5-5c1c-4524-aaa1-0bc03de4ea5b · thin_range · kind=context · severity=medium · open/new · 2026-08-13T12:35:23.840630+00:00→2026-08-13T12:49:05.754898+00:00
  series=GB:consumption:neso title=GB:consumption:neso: thin_range WARN
1fdaadc7-675b-4acf-a703-d1224c40219c · timing_gaps · kind=issue · severity=high · open/wip · 2026-08-10T10:14:54.407204+00:00→2026-08-13T12:49:05.660002+00:00
  series=GB:consumption:neso title=GB:consumption:neso: timing_gaps FAIL
… 6 more (--limit)
```

```bash
$ uv run python -m watchdogdatamodel.cli issue show 1fdaadc7-675b-4acf-a703-d1224c40219c
1fdaadc7-675b-4acf-a703-d1224c40219c · timing_gaps · kind=issue · severity=high · open/wip · 2026-08-10T10:14:54.407204+00:00→2026-08-13T12:49:05.660002+00:00
  series=GB:consumption:neso title=GB:consumption:neso: timing_gaps FAIL
  verdict_summary: local_only=8, unverified=0, not_applicable=0, upstream_confirmed=0
```

`severity` there reads the latest `observation` diary event, not the frozen
row column (the touch path never updates that column — see "Deliberate
decisions" below); `kind=issue` means this one is ours to fix, `kind=context`
above means it's real but upstream's problem, never actionable by us.

A human running this at a terminal can use the installed `wdm` console
script (`wdm issue show <id>`) instead of the module form — it's a
convenience alias for people, not something an agent script should rely on;
every example in this doc and in `AGENT.md` uses
`uv run python -m watchdogdatamodel.cli` because that form works everywhere,
including inside a sandbox with no console-script shims on `PATH`.

## The library — `watchdogdatamodel.query`

Same walls, called from Python instead of a subprocess. Every function
takes a connection first and returns JSON-able dicts (or lists of them) —
no markdown, no rendering, just data.

```python
from watchdogdatamodel import query

conn = query.connect()   # WDM_READONLY_PG_DSN, then WATCHDOG_READONLY_PG_DSN
```

Three real calls against the same local clone, output captured verbatim
(long lists lightly truncated with `…`):

```python
>>> query.list_issues(conn, state="open", check_id="timing_gaps", limit=1)
```
```json
[
  {
    "id": "1fdaadc7-675b-4acf-a703-d1224c40219c",
    "fingerprint": "GB:consumption:neso|timing_gaps",
    "origin": "check",
    "series_id": "ec0d5c9b-f6c7-4a3b-89c8-b3e9f1913f09",
    "related_series": [],
    "check_id": "timing_gaps",
    "state": "open",
    "stage": "wip",
    "severity": "high",
    "title": "GB:consumption:neso: timing_gaps FAIL",
    "details": {
      "tier": null,
      "bucket": "infra",
      "n_fail": 6,
      "n_gaps": 3,
      "n_warn": 0,
      "status": "FAIL",
      "verdicts": [
        {
          "note": "neso has a value here (22581.0)",
          "verdict": "local_only",
          "timestamp": "2026-08-06T07:30:00+00:00",
          "upstream_value": 22581.0
        }
        …
      ]
    }
    …
  }
]
```

```python
>>> query.stats(conn, by="kind")
```
```json
[
  {"group_value": "issue", "kind": "issue", "n": 5},
  {"group_value": "context", "kind": "context", "n": 4}
]
```

```python
>>> query.series_context(conn, "GB:production.solar:neso")
```
```json
[
  {
    "id": "654515be-519c-474f-a51e-c469bd12aef2",
    "fingerprint": "GB:production.solar:neso|thin_range",
    "origin": "check",
    "series_id": "2dc81f9f-f29c-4cf7-a881-f574ece89c19",
    "related_series": [],
    "check_id": "thin_range",
    "state": "open",
    "stage": "new",
    "severity": "medium",
    "title": "GB:production.solar:neso: thin_range WARN",
    "details": { "band": { "lower": [1028.61, 1688.31, …] … } }
    …
  }
]
```

`series_context` is the upstream-context lane: `kind='context'` findings for
one series, the same rows the board and kanban deliberately never paint.
Other v0.9 additions on `query`: `series_checks` (latest per-check outcome
from a series' snapshot stats), `series_issues` (every open issue on a
series, both kinds), `run_covering` (did a completed run's declared scope
actually re-check this series?), `issues_similar` (other open issues sharing
this one's series or check), and `stats` (open-issue counts grouped by
check/kind/severity/zone/source).

## What replaced `ReadOnly`

`watchdogdatamodel.readonly` and its `ReadOnly` class are deleted in
v0.9.0 — along with the markdown composites it rendered
(`investigation_brief`, `work_order`, `situation`, `summary`, `history`).
Nothing pre-selects what an agent sees anymore; it fetches what it judges
relevant, the same way a human investigator would.

| v0.8 composite (gone)   | v0.9 replacement                                                                 |
| ------------------------ | --------------------------------------------------------------------------------- |
| `investigation_brief`    | `issue show <id>` + `issue timeline <id>` + `action list --issue <id>` ("already tried") — add `issue lineage <id>` for past incidents and `issue similar <id>` for "is this systemic" |
| `history`                 | `issue timeline <id>` (full diary) + `issue lineage <id>` (walked to the root)     |
| `work_order`              | no single call — the executor no longer renders a file; the agent runs `guide` then whatever of the above it needs (see [executor-pattern.md](executor-pattern.md)) |
| `situation`               | `stats [--by DIM]` + `issues list [--check ID] [--label K=V]`                     |
| `summary`                 | `stats --by check` and `stats --by severity`                                      |

The rationale, not just the mapping: a script-chosen brief could only ever
guess at relevance, and the product's own playbook used to tell the agent
"you don't have DB access" while its environment held a perfectly good
read-only DSN. Tools + doctrine (`guide`) let the agent decide.

## Deliberate decisions worth knowing about

- **`run_covering` scans only the 200 most-recently-finished completed
  runs.** `None` means "not covered within that window" — there is no
  fallback query for older runs, by design (speed over completeness). See
  the docstring on `query.run_covering` and the CLI's `run covering --help`.
- **`scope_covers(check_id=None)` means "ignore the check dimension"**,
  matching this codebase's existing None-means-no-filter convention. Every
  caller in the store and CLI passes an explicit `check_id`, so this only
  matters if you call `scope_covers` directly.
- **`issue show`'s severity reads the latest `observation` diary event**,
  not the frozen `severity` column on the row — the touch path never
  updates that column, so the column alone would silently go stale.
- **The CLI's `verdict` field is `details.verdict`** (the product's
  classification, e.g. `db_stale`, `parser_mismatch`, `escalate`), shown
  separately from `resolution_reason` (printed as `resolved=…`, and only
  ever set on a closed row — it answers "how did this end", not "what is
  it").

## Wiring credentials to an agent's environment

1. Create/reuse a SELECT-only role:
   `GRANT SELECT ON series, series_snapshot, check_definition, check_run,
    issue, issue_event, action TO <role>;`
2. Store its DSN as a secret; expose it to the agent's process env as
   `WDM_READONLY_PG_DSN` (or `WATCHDOG_READONLY_PG_DSN` — `query.connect()`
   and the CLI both check the first, then fall back to the second).
3. The agent runs `python -m watchdogdatamodel.cli guide`, or calls
   `query.connect()` — nothing else to configure.
