# Quality-ops data model — design

**Date:** 2026-08-07
**Status:** approved (design); implementation not started
**Repo:** rebase-energy/watchdogdatamodel
**Companion:** `BRAINSTORM.md` (decision history and research findings)

## 1. Goal

Extract the watchdog's real value — its data model — into a generalized,
reusable blueprint for timeseries data-quality operations. Any Rebase product
deploys its own copy of the schema (blueprint-per-product, no multi-tenancy).
Target consumers beyond the grid-map watchdog: energydb quality ops, energy
forecast quality, weather forecast data quality, customer-facing quality
(SLAs, health pages, incident feeds).

The grid-map watchdog is the first consumer. Its functionality is preserved or
improved; its 9-table schema (three incompatible ID namespaces, joins by
informal zone+data_type matching) is replaced.

## 2. Design principles

1. **One spine.** Every table reaches `issue.id` or `series.id` in one hop via
   real foreign keys. No parallel ID namespaces.
2. **Machinery in the core, vocabulary in the product.** The core schema never
   contains the words "zone", "backfill", or "sweep". Products register their
   vocabulary in fields designed to hold it (labels, check ids, action types,
   stages).
3. **Data truth over process truth.** Process milestones (PR merged, job
   succeeded) never resolve an issue; only a check run that covers the series
   and finds nothing does.
4. **Declared coverage instead of stored silence.** Healthy = in scope of a
   completed run + no open issue. No "everything's fine" rows.
5. **Dedup by constraint, not discipline.** At most one open issue per
   fingerprint; at most one live action per (issue, type) — both enforced by
   partial unique indexes.
6. **Append-only diary, mutable processes.** `issue_event` rows are never
   updated. `action` rows mutate while live and freeze at terminal status.

Research grounding (surveyed 2026-08-07: OpenMetadata, DataHub, Soda,
Elementary, MobyDQ, Great Expectations, Deequ, Sentry Seer/Autofix, Sweep,
OpenHands resolver, StackStorm, Monte Carlo/Bigeye docs) is recorded in
`BRAINSTORM.md`. Notable adoptions: OpenMetadata's incident grouping and
mandatory resolution reason; DataHub's state/stage split; MobyDQ's
declare-scope-first; Soda's stable-check-identity lesson; StackStorm's
embedded status-transition log; Sentry's lifecycle timestamps and idempotent
job uniqueness.

## 3. Schema

Target database: PostgreSQL ≥ 14. All timestamps `timestamptz` (UTC). All
flexible fields `jsonb`. Primary keys `uuid` default `gen_random_uuid()`
unless noted. Table names carry no product prefix (the schema is deployed
per-product, often in its own database/schema namespace).

### 3.1 `series` — catalog of monitored timeseries

Built on timedatamodel (github.com/rebase-energy/timedatamodel), which is a
package dependency. A row persists the metadata of a timedatamodel
`TimeSeries` (its metadata-only mode exists for exactly this catalog use) plus
product labels.

| column | type | notes |
|---|---|---|
| `id` | uuid PK | |
| `key` | text UNIQUE NOT NULL | product's stable series identifier (PSD series id, energydb key, …) |
| `name` | text NOT NULL | timedatamodel `name` |
| `description` | text | |
| `unit` | text NOT NULL default `'dimensionless'` | |
| `timezone` | text NOT NULL default `'UTC'` | display hint; data is UTC |
| `frequency` | text | ISO-8601 duration (`PT1H`, `P1D`, …); null = irregular/unknown |
| `data_type` | text | timedatamodel `DataType` taxonomy (`OBSERVATION`, `FORECAST`, …) |
| `timeseries_type` | text NOT NULL default `'FLAT'` | `FLAT` or `OVERLAPPING`; `OVERLAPPING` marks forecast series with knowledge_time |
| `labels` | jsonb NOT NULL default `'{}'` | product vocabulary: `{zone, source, priority}` / `{model, param, station}` — GIN indexed |
| `active` | boolean NOT NULL default true | retired series keep history |
| `created_at`, `updated_at` | timestamptz NOT NULL | |

Round-trip guarantee: a `series` row converts losslessly to/from
`timedatamodel.TimeSeries(df=None, …)` (labels ride alongside). This is a
contract test, and it is the energydb-compatibility guarantee.

**One signal, many sources ⇒ many series rows.** When the same logical signal
is fetched from several sources (grid map: SE-SE1 production from energydb,
from the independent ENTSOE parser, from the PSD API), each (signal × source)
is its own catalog row, distinguished by the `source` label. Checks run per
source-series (freshness of one feed is independent of another); comparison
checks (`source_divergence`) are multi-series checks using `related_series`
(§3.4). Product-level groupings — the watchdog board's zone×data_type "cell"
— are label queries, not schema.

### 3.2 `check` — catalog of checks

| column | type | notes |
|---|---|---|
| `id` | text PK | stable, explicit, product-chosen (`gross_range`, `freshness`, `source_divergence`). Never derived from configuration (Soda's lesson: content-derived identity orphans history on edit) |
| `name` | text NOT NULL | |
| `description` | text | |
| `dimension` | text | standard quality vocabulary: `completeness`, `freshness`, `validity`, `consistency`, `accuracy`; nullable |
| `default_params` | jsonb NOT NULL default `'{}'` | |
| `enabled` | boolean NOT NULL default true | |
| `created_at`, `updated_at` | timestamptz NOT NULL | |

Check *code* lives in the product. This row is identity + self-description, so
issues can FK it and coverage can name it.

Implementation note: `check` is a reserved SQL word; the physical table is
named `check_definition`. All `check_id` column names are unchanged.

### 3.3 `check_run` — one execution of checks over a declared scope

| column | type | notes |
|---|---|---|
| `id` | uuid PK | |
| `status` | text NOT NULL | `running` → `completed` \| `failed`. A run that dies mid-way is visible |
| `trigger` | text NOT NULL | `scheduled`, `targeted`, `event`, `backtest`, `manual` |
| `scope` | jsonb NOT NULL | declared BEFORE execution: `{"series": "all" \| {"labels": {...}} \| {"ids": [...]}, "checks": "all" \| ["freshness", ...]}` |
| `window_start`, `window_end` | timestamptz | the DATA time examined (valid_time window), distinct from run time |
| `started_at` | timestamptz NOT NULL | |
| `finished_at` | timestamptz | |
| `stats` | jsonb NOT NULL default `'{}'` | counts: series in scope, checked, skipped, issues opened / re-detected / not-seen |
| `metadata` | jsonb NOT NULL default `'{}'` | code version / deploy SHA, durations, fetch notes |

**Coverage rule.** A series×check is *covered* by a run iff it matches the
run's scope and the run completed. If the run could not examine a series
(fetch failure, source unreachable), that failure is itself an issue (e.g.
check id `unreachable`), so silence remains meaningful. Healthy at time T =
covered by a completed run at T + no open issue. The package ships a helper
implementing scope-matching so all consumers evaluate coverage identically.

The grid-map "sweep" is the special case `trigger=scheduled, scope=all/all`.
Post-heal verification is `trigger=targeted` with a one-series scope.

### 3.4 `issue` — the problem record

| column | type | notes |
|---|---|---|
| `id` | uuid PK | |
| `fingerprint` | text NOT NULL | dedup identity. Check issues: `series.key + '|' + check.id` (+ optional product discriminator, e.g. a forecast run's knowledge_time for per-run issues on OVERLAPPING series). Human/agent issues: product-chosen (e.g. report id) |
| `origin` | text NOT NULL | `check`, `human`, `agent`, `external` |
| `series_id` | uuid FK → series, nullable | null allowed for human/agent issues not bound to a series |
| `related_series` | jsonb NOT NULL default `'[]'` | additional series for multi-series checks (source divergence): `[{"series_id": ..., "role": "reference"}]` |
| `check_id` | text FK → check, nullable | null for non-check origins |
| `state` | text NOT NULL | **`open` \| `resolved`** — the only states core logic reads |
| `stage` | text NOT NULL | product-defined kanban column (`new`, `triage`, `healing`, `awaiting_verification`, …). Free strings; core never interprets |
| `severity` | text NOT NULL default `'medium'` | `critical`, `high`, `medium`, `low` |
| `title` | text NOT NULL | |
| `details` | jsonb NOT NULL default `'{}'` | structured for check issues (observed values, thresholds, frozen `evidence` — see below); free-form for human/agent (the "flexible edges" decision) |
| `valid_start`, `valid_end` | timestamptz | affected DATA window; real columns so range queries work |
| `knowledge_time` | timestamptz | for OVERLAPPING series: which forecast run is affected |
| `first_seen_at`, `last_seen_at` | timestamptz NOT NULL | recurrence-while-open, denormalized for boards |
| `detected_by_run` | uuid FK → check_run, nullable | first detecting run; null for human/agent origin |
| `assignee` | text | nullable, product semantics |
| `resolved_at` | timestamptz | |
| `resolution_reason` | text | REQUIRED when state=resolved: `fixed`, `recovered` (auto, data came back clean), `false_positive`, `missing_at_source`, `wont_fix`, `superseded`, `stale` |
| `resolution_comment` | text | |
| `resolved_by` | text | actor string, see §3.5 |
| `predecessor_id` | uuid FK → issue, nullable | incident lineage across recurrences |
| `created_at`, `updated_at` | timestamptz NOT NULL | |

Constraint: `CREATE UNIQUE INDEX ON issue (fingerprint) WHERE state = 'open'`.

**Incident semantics.**
- While an issue is open, repeat detections attach to it (`detected_again`
  event, `last_seen_at` bump). The unique index makes duplicates impossible.
- A detection whose fingerprint matches only *resolved* issues opens a **new**
  issue with `predecessor_id` → the most recently resolved match.
- Automatic recurrence never reopens. Manual reopen (`resolved` → `open`,
  `reopened` event) exists for "the resolution was a mistake", human-initiated
  only.
- Check-origin issues auto-resolve: when a completed run covers the issue's
  series×check and reports no detection, the core records a `not_seen` event;
  the product's policy then resolves (watchdog default: resolve immediately,
  reason `recovered`). Human/agent-origin issues never auto-resolve.

**Evidence freezes at detection.** Snapshots (§3.7) roll forward, so "latest"
stops showing what a problem looked like. When a check opens (or re-detects)
an issue, it stores the affected window only — a bounded excerpt in
timedatamodel wire format — under `details.evidence`. Frozen with the issue,
never updated by later fetches (the failed-rows-sample pattern from
OpenMetadata/Soda: keep detail only where something went wrong).

**PII.** Issues are PII-free. Human reports' reporter name/email stay in a
product-side companion table with today's insert-only discipline
(`data_reports` precedent: public write route, no read route, insert-only DB
role). The companion row references `issue.id`.

### 3.5 `issue_event` — append-only diary

| column | type | notes |
|---|---|---|
| `id` | bigint identity PK | |
| `issue_id` | uuid FK → issue NOT NULL | |
| `at` | timestamptz NOT NULL | |
| `type` | text NOT NULL | core vocabulary below; products may extend with namespaced types (`psd.…`) |
| `actor` | text NOT NULL | `run:<uuid>`, `user:<name>`, `agent:<name>`, `rule:<name>`, `system` |
| `run_id` | uuid FK → check_run, nullable | set on run-caused events |
| `action_id` | uuid FK → action, nullable | reference, never duplication of action content |
| `data` | jsonb NOT NULL default `'{}'` | small payloads only (e.g. `{"from": "triage", "to": "healing"}`) |

Core event types: `opened`, `detected_again`, `not_seen`, `stage_changed`,
`resolved`, `reopened`, `action_requested`, `action_finished`,
`external_changed`, `comment`.

Rows are never updated or deleted (contract-tested). Events are the table of
contents; substance lives on the referenced action/run/issue.

### 3.6 `action` — the work queue

| column | type | notes |
|---|---|---|
| `id` | uuid PK | |
| `issue_id` | uuid FK → issue NOT NULL | actions always remediate an issue |
| `type` | text NOT NULL | product-registered: watchdog ships `backfill`, `agent_investigation`, `notify`; other products add `refetch`, `rerun_model`, … Core never interprets |
| `status` | text NOT NULL | `queued` → `running` → `succeeded` \| `failed` \| `canceled` |
| `transitions` | jsonb NOT NULL default `'[]'` | append-only `{status, at, by}` list, written with every status change (StackStorm pattern): one-row current-state read + full history |
| `params` | jsonb NOT NULL default `'{}'` | type-specific input (backfill window, source, …) |
| `outcome` | jsonb NOT NULL default `'{}'` | machine-readable result + external refs, e.g. `{"root_cause": "...", "pr": {"provider": "github", "repo": "...", "number": 312, "url": "..."}}` — the lifecycle+links decision: full agent write-ups stay in GitHub |
| `requested_by` | text NOT NULL | actor string (§3.5) |
| `created_at` | timestamptz NOT NULL | |
| `started_at`, `finished_at` | timestamptz | lifecycle timestamps as real columns (Sentry pattern) so duration analytics are plain SQL |

Constraints and rules:
- `CREATE UNIQUE INDEX ON action (issue_id, type) WHERE status IN ('queued','running')`
  — idempotent enqueueing; a second backfill for the same issue is impossible
  while one is live.
- Terminal rows (`succeeded`/`failed`/`canceled`) freeze: the store layer
  refuses further updates.
- Executors poll `WHERE status = 'queued' AND type = <mine>` (indexed).
- An action ends when its **deliverable** is delivered (agent investigation:
  diagnosis + PR opened ⇒ `succeeded`). External world-state afterwards (PR
  merged/closed) is NOT action state: a product-side ref-watcher (poll or
  webhook — the model doesn't care) writes `external_changed` events on the
  issue. Resolution still only comes from a covering clean run (§3.4).

### 3.7 `series_snapshot` — the working copy of the data

The values the checks actually saw, and what product UIs plot. Explicitly a
working copy, not a system of record: full history lives in TimeDB / energydb
/ the product's sources. One row per series, overwritten on each fetch.

| column | type | notes |
|---|---|---|
| `series_id` | uuid PK, FK → series | one snapshot per series (UPSERT) |
| `run_id` | uuid FK → check_run, nullable | which run fetched it |
| `fetched_at` | timestamptz NOT NULL | |
| `window_start`, `window_end` | timestamptz NOT NULL | valid_time extent of the payload |
| `payload` | jsonb NOT NULL | the data in timedatamodel wire format (`to_list()` columns dict) — OVERLAPPING series carry `knowledge_time` natively |
| `stats` | jsonb NOT NULL default `'{}'` | point count, null count, min/max — cheap board summaries without parsing payload |

Sizing: operational windows only (today's watchdog: ~1k series, a few KB–tens
of KB each). No snapshot history — that is deliberate; keeping per-run
snapshot history is the slippery slope to a second timeseries database.
Point-in-time forensics come from issue evidence (§3.4), which freezes the
affected window at detection.

## 4. Lifecycle walkthrough (reference narrative)

Sweep run #456 (scheduled, scope all/all) finds a gap in SE-SE1 production →
no open issue matches fingerprint → issue opens (open/new), event `opened`.
Auto-heal rule enqueues action #9 (`backfill`), event `action_requested`.
Next sweep still sees the gap → `detected_again`, `last_seen_at` bumps; no
second issue (constraint). Action #9 fails → `action_finished`, stage →
`needs_investigation`. Human requests action #10 (`agent_investigation`);
agent diagnoses parser bug, opens PR #312 → action `succeeded`, outcome
carries the link, stage → `awaiting_fix`. Ref-watcher sees the merge →
`external_changed {pr: merged}`, stage → `awaiting_verification`. Targeted
run covers the series, gap gone → `not_seen` → state `resolved`, reason
`recovered`. Months later the gap returns → new issue, `predecessor_id` →
this one.

## 5. Mapping from today's watchdog schema

| today (9 tables) | becomes |
|---|---|
| `watchdog_results` | dropped — `check_run` coverage + issues |
| `watchdog_issues` + `watchdog_issue_state` | `issue` + `issue_event` |
| `watchdog_backfill_jobs` + `watchdog_agent_requests` | `action` (`backfill`, `agent_investigation`) |
| `data_reports` | `issue` (origin `human`) + product-side reporter-contact table (PII) |
| `watchdog_series` (chart cache) | `series_snapshot` — same latest-only behavior, now joined to the spine by a real key |
| `watchdog_deployments`, `watchdog_settings` | stay product-side ops tables, unchanged |

Migration stance (user decision): fresh start is acceptable; migrate prod
issue history only if it proves a cheap side-effect. Sweep-derived state
repopulates itself on the first run against the new schema.

## 6. Repo deliverables

1. **Schema** — SQL DDL (tables, indexes, the two partial unique constraints),
   idempotent bootstrap script.
2. **Python package** (`watchdogdatamodel`) — pydantic models mirroring the
   tables; `timedatamodel` dependency; helpers: fingerprint computation, scope
   matching / coverage evaluation, event emission, action lifecycle
   (enqueue / claim / transition / freeze), series ↔ `TimeSeries` round-trip,
   snapshot upsert + payload (de)serialization, evidence excerpting.
3. **Contract tests** any deployment can run — the blueprint's guarantees:
   - `issue_event` is append-only (no UPDATE/DELETE path in the store API).
   - one open issue per fingerprint; second open attempt attaches instead.
   - one live action per (issue, type); duplicate enqueue is a no-op returning
     the live action.
   - terminal actions refuse mutation.
   - series ↔ timedatamodel round-trip is lossless; snapshot payloads parse
     back into `TimeSeries` objects of the declared shape.
   - snapshots are latest-only (a second write for the same series replaces,
     never accumulates); issue evidence is immutable once written.
   - resolution requires a reason.
   - coverage helper: covered ⇒ silence means healthy; out-of-scope ⇒ silence
     means nothing.
4. **Docs** — this spec; an adopter's guide ("register your series, checks,
   action types, stages").

Non-goals for the core (explicitly out): UI caches, notification routing,
settings/feature flags, deploy stamps, reporter PII, metric-history storage
(future extension — Deequ-style measured-value time series for drift checks;
recomputable from TimeDB until then), multi-tenancy.

## 7. Rollout (high level; detailed plan is the next phase)

1. Implement schema + package + contract tests in this repo.
2. Stand the schema up on a throwaway dev database (existing
   `watchdog_bf_test` docker-compose pattern) and adapt the PSD watchdog's
   sweep/heal/board code behind its store layer to write/read the new model
   on dev.
3. Run dev in parallel against real sweeps; compare board behavior with prod.
4. Prod cutover: fresh schema, first sweep repopulates; issue-history
   migration only if trivially scriptable. Davide runs all deploys.
