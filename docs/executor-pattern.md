# The executor pattern

How agent runtimes ("executors") investigate issues in any product built on
this data model — with interchangeable executors as a design guarantee. The
state semantics are enforced by `watchdogdatamodel.trackers`; this doc is the
pattern that ties them together. Product-specific bindings (credentials,
tracker choice, start signals) live in each product's own contract doc.

## Roles

- **The model is the queue and the memory.** An investigation is an `action`
  (`type` chosen by the product); dispatch is `claim_next` or watching for
  running actions. Context comes from the read-only CLI/library
  (`watchdogdatamodel.cli` / `query.py`, [agent-sdk.md](agent-sdk.md)) —
  running `python -m watchdogdatamodel.cli guide` is the *agent's* mandatory
  first move, not the executor's. As of v0.9.0 the executor doesn't fetch or
  render context at all; it hands the agent tools and a starting point.
- **The tracker is the stage.** A ticket carries the correlation stamp and a
  playbook pointer (see "Starting the agent"); deliverables (PRs, patches,
  reports) live there. The model stores lifecycle + links, never prose.
- **The executor is interchangeable.** Hosted workflow, bot assignment, or an
  external harness — downstream of delivery the system cannot tell them apart.

## Starting the agent

There is no work order file as of v0.9.0. The executor does not fetch or
render context on the agent's behalf — it hands the agent tools and gets out
of the way. Concretely, every executor:

1. Creates the sandbox (or claims the next queued `action` via
   `trackers.claim_next`).
2. Exports the read-only DSN into the agent's process environment as
   `WDM_READONLY_PG_DSN` (or `WATCHDOG_READONLY_PG_DSN`).
3. Tells the agent the issue id, and to run
   `python -m watchdogdatamodel.cli guide` before anything else. That's the
   whole briefing — `guide` prints the doctrine (the five rules, the "start
   here" recipe); the agent decides from there what to look up.
4. Collects the deliverable when the agent finishes.

The ticket filed for an investigation still carries, executor-agnostically:
1. `wdm-action: <uuid>` — the correlation stamp (`trackers.stamp`). Exact
   match is the ONLY primary correlation; prose keywords are fallback.
2. A pointer to the product's playbook.

Two items, not four — the context brief and the task directive aren't
filing-time artifacts anymore, because there's no pre-rendered context to put
in them. The agent builds its own picture by calling the CLI (or the
`query` library directly) against the DSN it was handed.

## Delivery obligations (every executor, every product)

1. Findings go to the tracker (a comment/report), not the database.
2. Any deliverable carries the stamp verbatim.
3. Endings without a deliverable are legitimate (diagnose-only): close the
   ticket; `trackers.finish_on_external_close` settles the action.
4. **Never resolve issues yourself.** Resolution belongs to the data — a
   clean covering `check_run` decides, not the executor's claim of success.
5. Scope: the one subject the executor pointed it at (the issue id it was
   handed) — not whatever else the agent found while poking around.

## Lifecycle mapping

| Executor/tracker event | Model effect (via trackers protocol) |
|---|---|
| ticket filed | action queued → running |
| deliverable opened (stamped) | `add_deliverable` (product may finish here — policy) |
| deliverable merged/landed | diary `external_changed`; product workflow stage |
| ticket closed completed / not_planned | action succeeded (`closed_without_deliverable`) / canceled |
| lost delivery | `reconcile_external` recovers it (webhooks = latency, polling = truth) |

## Adding a backend — the promise

A new executor needs: the product's credential bundle, a start signal, the
playbook, and this pattern. **Nothing in the data model, the trackers
protocol, or the work-order format changes per backend.** If a backend seems
to require such a change, stop: that is a design smell in the backend, not a
missing feature in the model.

Reference binding: the grid-map watchdog's `docs/watchdog-executor-contract.md`
in rebase-grid (credential bundle, GitHub App identity, backend registry).

## Learnings from the first non-Claude executor run (2026-08-10)

A harness backend (OpenCode driving Kimi K3 on a Modal endpoint) ran a real
investigation end-to-end against the grid-map deployment: brief rendered from
the read-only SDK → agent in a throwaway worktree → executor-opened draft PR.
The pattern held with zero changes to the model or the protocol — the
"adding a backend" promise above survived first contact. What the run taught:

**Capability separation is the safety model, and it is enforceable.** The
agent held no tracker credential and no database DSN; every tracker operation
(branch, push, PR) was done by the deterministic executor, whose only
deliverable-creating code path is a *draft*. Merging was not forbidden — it
was *impossible*. Harness-level permission walls (OpenCode's `deny` rules
survive headless runs) add a second wall in front of `git`/`gh` even inside
the sandbox. Executors should be built this way around any model, trusted or
not: instructions are a courtesy, capabilities are the contract.

**Work orders want to be files.** Concatenating `investigation_brief` +
`situation` into one markdown file was the agent's entire context and it
sufficed for a correct, evidence-dense verdict. Shipped in v0.6:
`ReadOnly.work_order()` rendered that bundle in one call, so every executor
stopped hand-assembling it. *(Superseded in v0.9.0: `ReadOnly` and its
composites, `work_order` included, are deleted — see "Starting the agent"
above. The lesson that survived is that an agent's whole context should
come from one cheap, complete call; the change is that the call is now
`cli guide` plus whatever the agent itself decides to fetch, not a file the
executor pre-assembles for it.)*

**The queue is the missing half.** This run was pointed at an issue id by
hand because no action row existed — which also meant the deliverable could
not carry a `wdm-action:` stamp, only informal issue provenance, so the
reconciler cannot adopt it. Shipped in v0.6: `trackers.claim_next()`
(atomically claim the oldest queued action of a kind, respecting
`max_inflight`) so harness backends can poll the model as a queue and stamp
correctly from birth.

**One call, with the economy rules in the work order.** The same day's
second run (a real fix, stamped end-to-end, adopted by the webhook) nearly
died at the finish line: after ~40 tool-heavy steps the model's wrap-up turn
stalled near its context ceiling, having burned most of its budget re-running
the product's full test suite and chasing sandbox-environment failures in
unrelated tests. The design answer is NOT to split the job into multiple
model calls — it is to make one call finish. The work order must state the
economy discipline explicitly, because agents do not conserve tokens by
default: deliverable-first (write the deliverable the moment the verdict is
confident; an unwritten deliverable after a perfect investigation is a failed
run), verify once (one live re-check, one targeted test run — never the full
suite; CI owns that), no retry loops (twice failed = write down the
hypothesis), and tiny tool output (everything printed is re-sent every
following turn).

The executor still wraps the call in a completion contract — "this file
exists" — with a bounded retry, and keeps one recovery move for a run that
stalls anyway: a fresh request carrying only the diff, the evidence, and the
required output format. That recovery produced a perfect deliverable in one
turn when it was needed. But it is the fallback, not the flow.

**Fact-check the agent with the same SDK.** The executor's read-only handle
makes verifying an agent's systemic claims (issue counts, affected zones,
lineage) a few cheap queries. Every claim in this run's report checked out —
but the check cost seconds, and a backend that verifies before delivering
turns "plausible" into "confirmed". Worth documenting as an optional
post-agent step in any binding; candidates for a helper once a second product
adopts it.
