# The executor pattern

How agent runtimes ("executors") investigate issues in any product built on
this data model — with interchangeable executors as a design guarantee. The
state semantics are enforced by `watchdogdatamodel.trackers`; this doc is the
pattern that ties them together. Product-specific bindings (credentials,
tracker choice, start signals) live in each product's own contract doc.

## Roles

- **The model is the queue and the memory.** An investigation is an `action`
  (`type` chosen by the product); dispatch is `claim_next` or watching for
  running actions. Context is the read-only SDK (`readonly.ReadOnly` —
  `investigation_brief` is the executor's mandatory first move).
- **The tracker is the stage.** A ticket carries the work order; deliverables
  (PRs, patches, reports) live there. The model stores lifecycle + links,
  never prose.
- **The executor is interchangeable.** Hosted workflow, bot assignment, or an
  external harness — downstream of delivery the system cannot tell them apart.

## The work order

The ticket filed for an investigation must contain, executor-agnostically:
1. `wdm-action: <uuid>` — the correlation stamp (`trackers.stamp`). Exact
   match is the ONLY primary correlation; prose keywords are fallback.
2. The context brief (e.g. `investigation_brief` output) — evidence at filing.
3. A task directive shaped by what the model already knows (verdict, history).
4. A pointer to the product's playbook.

## Delivery obligations (every executor, every product)

1. Findings go to the tracker (a comment/report), not the database.
2. Any deliverable carries the stamp verbatim.
3. Endings without a deliverable are legitimate (diagnose-only): close the
   ticket; `trackers.finish_on_external_close` settles the action.
4. **Never resolve issues yourself.** Resolution belongs to the data — a
   clean covering `check_run` decides, not the executor's claim of success.
5. Scope: the one subject in the work order.

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
