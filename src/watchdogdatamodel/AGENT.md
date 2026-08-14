# wdm — agent doctrine

## What this is

A `watchdogdatamodel` (wdm) database records data-quality problems for
timeseries: which series are being watched, what checks run against them,
what those checks found, and what has been done about it. **You read it;
you never write it.** The connection this CLI opens is read-only end to
end — a SELECT-only database role, plus a server-enforced
`default_transaction_read_only=on` session — so there is no write path to
worry about, guard against, or accidentally trigger. If you need something
changed (a fix landed, an issue reclassified, an action queued), that
happens through other tooling; this surface is for reading the record.

Drive it with `python -m watchdogdatamodel.cli <group> <command> [args]
[--json] [--limit N] [--dsn DSN]`. `--json` prints exact field values —
use it whenever you need a precise value, are diffing two calls, or are
quoting a field in a report. Without `--json` you get a compact,
human-scale rendering meant to be skimmed. `--limit` (default 20) caps how
many rows print; a capped list always says what it dropped, so a short
list is never mistaken for a complete one. `--dsn` overrides the
connection string; without it the CLI reads `WDM_READONLY_PG_DSN` then
`WATCHDOG_READONLY_PG_DSN` from the environment.

Every command that touches the database exits **2** and prints a message
containing "no wdm access" if the DSN is missing or the connection fails.
**This is not a crash to route around.** It means: investigate from the
issue body alone, and say so plainly in your report — do not present
conclusions as if you had read a record you could not actually reach.
`guide` (this document) is the one exception: it reads a file packaged
with the library and needs no database at all, so it always works.

## Command surface

| group                     | commands                                                              |
| -------------------------- | ---------------------------------------------------------------------- |
| `series`                   | `list`, `show <key>`, `context <key>`, `checks <key>`, `issues <key>`, `snapshot <key>` |
| `check` (alias `checks`)   | `list`, `show <check_id>`                                              |
| `issue` (alias `issues`)   | `list`, `show <id>`, `timeline <id>`, `lineage <id>`, `similar <id>`    |
| `run` (alias `runs`)       | `list`, `show <id>`, `covering <key> [--check ID]`                      |
| `action` (alias `actions`) | `list`                                                                  |
| `stats [--by DIM]`         | open-issue counts by `check` / `kind` / `severity` / `zone` / `source`  |
| `guide`                    | prints this document; the only command that needs no database          |

Group names accept either form (`issue show <id>` and `issues list` are
both correct — `issue`/`check`/`run`/`action` are canonical, the plural is
an alias for the same subcommands).

## The seven tables

- `series` — a watched timeseries, plus product-defined labels (zone,
  source, class, …).
- `check_definition` — what each check asserts, including its `contract`
  (the product's declared decision rule for that check — nullable; not
  every check has one).
- `check_run` — one sweep: what it declared it would cover (`scope`), when
  it ran, and how it finished.
- `issue` — one open problem per (series, check), deduplicated by
  fingerprint — at most one *open* row per fingerprint at a time.
- `issue_event` — that issue's append-only diary: opened, touched again,
  observed, resolved, reopened, reclassified, and so on.
- `action` — a fix attempt against an issue: a heal, an investigation, any
  queued/running/terminal piece of work.
- `series_snapshot` — the latest fetched window for a series, plus the
  per-check stats that window produced.

## Five rules that change conclusions

1. **`kind` is not decoration.** `kind='context'` means the finding is
   real but the fault is **upstream's** — visible for situational
   awareness, never actionable by us, never painted on the board.
   `kind='issue'` is ours to fix. Every issue-shaped row this CLI prints
   carries `kind`; read it before anything else. Mistaking one for the
   other is the single most consequential error you can make here: either
   you chase a bug that doesn't exist in our code, or you dismiss one that
   does.
2. **`details` is frozen at first detection.** It is the evidence that
   justified opening the row, not a live view of the series. The current
   picture lives in the `observation` events on the issue's diary
   (`issue timeline <id>`) — read the timeline before trusting an old
   `details` window as if it were still true.
3. **A PASS only means something if a covering run actually re-checked
   that series.** `run covering <key> [--check ID]` answers this: with no
   `--check` it answers "which run last covered this series, whatever
   checks it declared"; with `--check` it narrows to "did a run re-check
   THIS check on this series." It only scans the 200
   most-recently-finished completed runs, so a "not covered" result means
   **not covered within that window** — not "never covered." No covering
   run means your evidence about this series may be stale; it does not
   mean the series is healthy.
4. **A merged PR never closes an issue.** Only a later run that finds the
   data healthy resolves it. Landing a fix is necessary but not
   sufficient — say that resolution is pending the next covering run,
   rather than reporting the issue as closed.
5. **Recurrence opens a new row, not a reopened one.** A problem that
   returns after being resolved gets a fresh `issue` row linked to its
   predecessor via `predecessor_id`. Read `issue lineage <id>` before
   concluding "this is the first time this has happened" — it may be the
   third.

## Start here

Handed an issue id, in order:

1. `issue show <id>` — the row itself: check, series, current
   `kind`/`severity`/`stage`, and its latest observation.
2. `issue timeline <id>` — the append-only diary. Read this before
   trusting `details` (rule 2).
3. `series context <key>` — is the upstream source showing the same
   problem right now? Context findings never block on us, but they
   explain a lot of what you'll otherwise mis-diagnose as a bug.
4. `run covering <key>` — is your evidence about this series current, or
   are you reasoning from a stale sweep (rule 3)?
5. `issues list --check <check>` — is this one cell wrong, or is the
   whole fleet failing this check? Scope your fix to what you actually
   found.
6. `check show <check>` — what does this check actually assert, and what
   is its declared `contract` (decision rule, verdict routing)? Don't
   guess from the check's name.

## Output discipline

Compact rendering is the default and is meant to be skimmed, not parsed.
Reach for `--json` whenever you need an exact field value. Every list
caps at `--limit` (20 by default) and always prints what it dropped — a
short list is never a silent truncation. The same discipline applies
inside a single row: a `details` or snapshot `payload` key holding a list
longer than 20 items is never dumped inline — it prints as
`(details.<key>: N items, omitted — use --json)` so a per-point array
can't flood your context window. Ask for `--json` on that one row if you
actually need the array.
