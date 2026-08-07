# Watchdog data model redesign — brainstorm state

Working notes for the redesign of the watchdog's data model into a generalized,
exportable quality-ops data model. Updated as decisions land; this file exists so
the design conversation survives session loss.

## Goal

Extract the watchdog's real value — its data model — into a reusable blueprint
other Rebase products can deploy, without depending on the grid map or its
specific checks. Preserve (or improve) current watchdog functionality.

## Target use cases (beyond the grid-map watchdog)

- energydb quality ops
- Forecast quality (energy forecasts: drift, missing runs, staleness)
- Weather forecast data quality checks
- Customer-facing quality (SLAs, per-series health, incident feeds)

## Current state (measured, 2026-08-06/07)

Live watchdog DB has **9 tables** (not 13): watchdog_results, watchdog_series,
watchdog_issue_state, watchdog_issues, watchdog_agent_requests,
watchdog_backfill_jobs, watchdog_deployments, watchdog_settings, data_reports.
Core defect: three ID namespaces (issue_id, issue_key, cluster_key) that don't
match; most joins are informal zone+data_type matching. See
`docs/watchdog_schema.html` in the power-system-data repo for the verified map.

## Decisions so far

1. **Deploy shape: blueprint per product.** Each product runs its own copy of the
   schema. No multi-tenant bookkeeping in the core.
2. **This repo is the home.** The data model lives here (rebase-energy/
   watchdogdatamodel), versioned independently of power-system-data.
3. **Agent data: lifecycle + links only.** The DB records that an investigation
   happened, its status, links (GitHub issue/PR), and a short machine-readable
   outcome. Full write-ups stay in GitHub.
4. **Migration: fresh start acceptable.** Carry prod issue history over only if
   it is a cheap side-effect, not a design constraint.
5. **Issue origin: any (check / human / agent / other system).** Check-detected
   issues carry structured fields; human/agent issues carry a flexible payload.
6. **Series identity: built on timedatamodel** (rebase-energy/timedatamodel).
   The series table persists TimeSeries metadata (name, description, unit,
   timezone, frequency, data_type, timeseries_type FLAT/OVERLAPPING) plus
   free-form product labels (zone, source, model, ...). OVERLAPPING +
   valid_time/knowledge_time vocabulary gives forecast compatibility with
   energydb/TimeDB by construction.
7. **check_run generalizes the sweep**: one execution of a chosen set of checks
   over a declared scope at a moment in time. The grid-map sweep is the special
   case scope=all, checks=all. Other members: targeted recheck after heal,
   event-driven check on data arrival, backtest of a new check.
8. **No check_result table.** Only issues are stored per-problem; the run records
   coverage (what was attempted, what couldn't be checked). Healthy = covered by
   a run + no open issue. Fetch failures are themselves issues.
9. **Recurrence: incident model.** Open issues absorb repeat detections; after
   resolution, a recurrence opens a NEW issue linked to its predecessor by a
   shared fingerprint (series+check identity).

## Open questions

- **action as its own table vs folded into issue_event.** Leaning separate table
  (processes with mutable status + params + outcome vs append-only diary;
  executor work-queue query), with events referencing actions rather than
  duplicating them. User wants more discussion; researching how OpenMetadata,
  DataHub, Soda, Elementary, MobyDQ, Sentry Seer/Autofix, StackStorm and
  GitHub-native AI resolvers model this before deciding.
- Exact core table list (currently: series, check_run, issue, issue_event,
  action?) and field-level design.
- How multi-series checks (e.g. source divergence comparing two series)
  reference their subjects: primary series + related series list, TBD.
- What of the watchdog UI cache (watchdog_series) and ops tables
  (deployments, settings) — stay product-side, outside the core model.

## Candidate core model (approach 1, evolving)

- `series` — catalog of watched series (timedatamodel metadata + labels)
- `check_run` — one execution of checks over a declared scope, with coverage
- `issue` — one problem, any origin, stable ID, current status, fingerprint
- `issue_event` — append-only diary per issue
- `action` (under discussion) — typed remediation processes (backfill, agent
  investigation, refetch, notify, ...) with product-registered types
