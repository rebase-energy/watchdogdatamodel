"""Read-only CLI over a wdm database. `python -m watchdogdatamodel.cli --help`.

One argparse subparser per group (`series`, `check`/`checks`, `issue`/`issues`,
`run`/`runs`, `action`/`actions`), plus two leaf commands (`guide`, `stats`).
Every leaf calls exactly one `query.*` function and prints it through a small
compact renderer, or as exact JSON with `--json`. `guide` is the one command
that needs no database — it prints the packaged doctrine file.
"""
from __future__ import annotations

import argparse
import json
import sys
from importlib.resources import files

import psycopg

from . import query

NO_ACCESS = (
    "no wdm access: could not open the watchdog database ({err}). Investigate "
    "from the issue body alone and SAY SO in your report — do not present "
    "conclusions as if you had read the record."
)

# Rows fetched per list-style query, independent of the display cap (--limit).
# 200 mirrors query.list_series' own default and query.run_covering's scan
# bound, so "N more (--limit)" is accurate for any realistic result set up to
# this pool; beyond it the undercount is the same documented trade-off
# run_covering already makes.
_FETCH_POOL = 200

# issue.lineage walks predecessor_id backward; capped defensively so a
# (shouldn't-happen) cycle can't hang the CLI.
_LINEAGE_DEPTH_CAP = 20


def _emit(rows, args, render):
    """Print `rows` compactly, or as exact JSON with --json. Never truncate
    silently: a capped list says what it dropped."""
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return
    if isinstance(rows, list):
        shown = rows[: args.limit]
        for r in shown:
            print(render(r))
        if len(rows) > len(shown):
            print(f"… {len(rows) - len(shown)} more (--limit)")
    elif rows is None:
        print("(nothing found)")
    else:
        print(render(rows))


def _with_conn(fn):
    """Wrap a handler needing `conn` as its first argument: opens the
    connection, turning a missing DSN or a failed connect into exit 2 with
    the NO_ACCESS message, and always closes the connection afterwards."""

    def wrapper(args):
        try:
            conn = query.connect(args.dsn)
        except (RuntimeError, psycopg.OperationalError) as e:
            print(NO_ACCESS.format(err=e), file=sys.stderr)
            return 2
        try:
            return fn(conn, args)
        finally:
            conn.close()

    return wrapper


def _parse_labels(pairs: list[str] | None) -> dict | None:
    if not pairs:
        return None
    out = {}
    for p in pairs:
        if "=" not in p:
            raise SystemExit(f"invalid --label {p!r}, expected KEY=VALUE")
        k, v = p.split("=", 1)
        out[k] = v
    return out


def _compact(obj, cap: int = 20):
    """Recursively replace any list longer than `cap` with a short marker, so
    a scope/stats/contract/event-data blob never dumps a heavy per-point
    array in compact mode. Use --json for the exact structure."""
    if isinstance(obj, list):
        if len(obj) > cap:
            return f"<{len(obj)} items omitted, use --json>"
        return [_compact(v, cap) for v in obj]
    if isinstance(obj, dict):
        return {k: _compact(v, cap) for k, v in obj.items()}
    return obj


# ── renderers ───────────────────────────────────────────────────────

def _render_series(s: dict) -> str:
    bits = [
        s.get("key"), s.get("name"), f"unit={s.get('unit')}",
        f"tz={s.get('timezone')}", f"active={s.get('active')}",
    ]
    line = " · ".join(str(b) for b in bits)
    labels = s.get("labels")
    if labels:
        line += f" · labels={labels}"
    return line


def _render_check(c: dict) -> str:
    bits = [
        c.get("id"), c.get("name"), f"dimension={c.get('dimension')}",
        f"enabled={c.get('enabled')}",
    ]
    line = " · ".join(str(b) for b in bits)
    contract = c.get("contract")
    line += f"\n  contract={_compact(contract)}" if contract else "\n  contract=(none)"
    return line


def _render_issue(i: dict) -> str:
    # Required fields per doctrine: check_id · kind · severity · state/stage
    # · first→last seen · verdict — kind is always shown; mistaking
    # kind='context' (upstream, not ours) for kind='issue' is the single
    # most consequential misreading of this model.
    # severity is driven by the latest `observation` diary event, not the
    # frozen row (spec: watchdog-rethink-design.md §5.2 — severity, kind and
    # heal windows read the latest observation; kind itself is a real column
    # mutated only by reclassify(), so it stays read straight off the row).
    events = i.get("events") or []
    observations = [e for e in events if e.get("type") == "observation"]
    latest_obs = (observations[-1].get("data") or {}) if observations else {}
    severity = latest_obs.get("severity") or i.get("severity")

    span = f"{i.get('first_seen_at')}→{i.get('last_seen_at')}"
    verdict = i.get("resolution_reason") or "open"
    lines = [
        f"{i.get('id')} · {i.get('check_id')} · kind={i.get('kind')} · "
        f"severity={severity} · {i.get('state')}/{i.get('stage')} · "
        f"{span} · verdict={verdict}"
    ]
    series_key = i.get("series_key")
    lines.append(f"  series={series_key} title={i.get('title')}" if series_key
                 else f"  title={i.get('title')}")

    if observations:
        verdict_summary = latest_obs.get("verdict_summary")
        if verdict_summary:
            counts = ", ".join(f"{k}={v}" for k, v in verdict_summary.items())
            lines.append(f"  verdict_summary: {counts}")
        human_summary = latest_obs.get("human_summary")
        if human_summary:
            lines.append(f"  human_summary: {human_summary}")

    for k, v in (i.get("details") or {}).items():
        if isinstance(v, list) and len(v) > 20:
            lines.append(f"  (details.{k}: {len(v)} items, omitted — use --json)")

    return "\n".join(lines)


def _render_run(r: dict) -> str:
    return (
        f"{r.get('id')} · {r.get('status')} · trigger={r.get('trigger')} · "
        f"scope={_compact(r.get('scope') or {})} · "
        f"started={r.get('started_at')} finished={r.get('finished_at')}"
    )


def _render_action(a: dict) -> str:
    return (
        f"{a.get('id')} · issue={a.get('issue_id')} · type={a.get('type')} · "
        f"status={a.get('status')} · requested_by={a.get('requested_by')} · "
        f"created={a.get('created_at')} finished={a.get('finished_at')}"
    )


def _render_event(e: dict) -> str:
    line = f"{e.get('at')} · {e.get('type')} · actor={e.get('actor')}"
    data = e.get("data")
    if data:
        line += f" · {_compact(data)}"
    return line


def _render_similar(r: dict) -> str:
    return (
        f"{r.get('series_key')} · {r.get('check_id')} · kind={r.get('kind')} · "
        f"severity={r.get('severity')} · last_seen={r.get('last_seen_at')}"
    )


def _render_stats_row(r: dict) -> str:
    return f"{r.get('group_value')} · kind={r.get('kind')} · n={r.get('n')}"


def _render_series_checks(d: dict) -> str:
    lines = [
        f"window={d.get('window_start')}→{d.get('window_end')} "
        f"fetched_at={d.get('fetched_at')}"
    ]
    stats = d.get("stats") or {}
    if not stats:
        lines.append("  (no stats recorded)")
    for k, v in stats.items():
        lines.append(f"  {k}: {_compact(v)}")
    return "\n".join(lines)


def _render_snapshot(sn: dict) -> str:
    lines = [
        f"series_id={sn.get('series_id')} run_id={sn.get('run_id')}",
        f"  window={sn.get('window_start')}→{sn.get('window_end')} "
        f"fetched_at={sn.get('fetched_at')}",
    ]
    # payload is the fetched window itself — exactly the shape that can hold
    # a heavy per-point array, so it gets the same omission treatment as
    # issue.details rather than a blanket _compact (the key name matters:
    # the omission line names the field that was skipped).
    for k, v in (sn.get("payload") or {}).items():
        if isinstance(v, list) and len(v) > 20:
            lines.append(f"  (payload.{k}: {len(v)} items, omitted — use --json)")
        else:
            lines.append(f"  payload.{k}={_compact(v)}")
    stats = sn.get("stats") or {}
    if stats:
        lines.append(f"  stats={_compact(stats)}")
    return "\n".join(lines)


# ── handlers: no DB (guide) ───────────────────────────────────────────

def _cmd_guide(args) -> int:
    text = files("watchdogdatamodel").joinpath("AGENT.md").read_text(encoding="utf-8")
    print(text)
    return 0


# ── handlers: series ───────────────────────────────────────────────

@_with_conn
def _cmd_series_list(conn, args) -> int:
    rows = query.list_series(conn, labels=_parse_labels(args.label),
                              active=not args.inactive, limit=_FETCH_POOL)
    _emit(rows, args, _render_series)
    return 0


@_with_conn
def _cmd_series_show(conn, args) -> int:
    _emit(query.get_series(conn, args.key), args, _render_series)
    return 0


@_with_conn
def _cmd_series_context(conn, args) -> int:
    _emit(query.series_context(conn, args.key), args, _render_issue)
    return 0


@_with_conn
def _cmd_series_checks(conn, args) -> int:
    _emit(query.series_checks(conn, args.key), args, _render_series_checks)
    return 0


@_with_conn
def _cmd_series_issues(conn, args) -> int:
    _emit(query.series_issues(conn, args.key), args, _render_issue)
    return 0


@_with_conn
def _cmd_series_snapshot(conn, args) -> int:
    _emit(query.get_snapshot(conn, args.key), args, _render_snapshot)
    return 0


# ── handlers: check / checks ────────────────────────────────────────

@_with_conn
def _cmd_checks_list(conn, args) -> int:
    _emit(query.list_checks(conn), args, _render_check)
    return 0


@_with_conn
def _cmd_checks_show(conn, args) -> int:
    _emit(query.get_check(conn, args.check_id), args, _render_check)
    return 0


# ── handlers: issue / issues ────────────────────────────────────────

@_with_conn
def _cmd_issues_list(conn, args) -> int:
    rows = query.list_issues(
        conn, state=args.state, check_id=args.check_id,
        labels=_parse_labels(args.label), kind=args.kind, limit=_FETCH_POOL)
    _emit(rows, args, _render_issue)
    return 0


@_with_conn
def _cmd_issues_show(conn, args) -> int:
    _emit(query.get_issue(conn, args.issue_id), args, _render_issue)
    return 0


@_with_conn
def _cmd_issues_timeline(conn, args) -> int:
    _emit(query.list_events(conn, args.issue_id, limit=_FETCH_POOL), args, _render_event)
    return 0


@_with_conn
def _cmd_issues_lineage(conn, args) -> int:
    # No dedicated query.* function for this: compose get_issue by walking
    # predecessor_id backward. Depth-capped and cycle-guarded defensively —
    # the schema shouldn't produce a cycle, but this must never hang either
    # way.
    chain: list[dict] = []
    seen: set[str] = set()
    current_id = args.issue_id
    while current_id and current_id not in seen and len(chain) < _LINEAGE_DEPTH_CAP:
        seen.add(current_id)
        issue = query.get_issue(conn, current_id)
        if issue is None:
            break
        chain.append(issue)
        current_id = issue.get("predecessor_id")
    if not chain:
        # Unknown issue id: same "(nothing found)" / null as `issues show`
        # on a miss, via _emit's None handling — not an empty-list silence.
        _emit(None, args, _render_issue)
        return 0
    _emit(chain, args, _render_issue)
    if not args.json and len(chain) == _LINEAGE_DEPTH_CAP and current_id:
        print(f"… lineage walk capped at {_LINEAGE_DEPTH_CAP} hops")
    return 0


@_with_conn
def _cmd_issues_similar(conn, args) -> int:
    _emit(query.issues_similar(conn, args.issue_id, limit=_FETCH_POOL), args, _render_similar)
    return 0


# ── handlers: run / runs ────────────────────────────────────────────

@_with_conn
def _cmd_runs_list(conn, args) -> int:
    _emit(query.list_runs(conn, limit=_FETCH_POOL), args, _render_run)
    return 0


@_with_conn
def _cmd_runs_show(conn, args) -> int:
    _emit(query.get_run(conn, args.run_id), args, _render_run)
    return 0


@_with_conn
def _cmd_runs_covering(conn, args) -> int:
    row = query.run_covering(conn, args.key, check_id=args.check_id)
    if args.json:
        print(json.dumps(row, indent=2, default=str))
        return 0
    if row is None:
        scope_desc = f"check {args.check_id!r}" if args.check_id else "any of its declared checks"
        print(
            "(not covered: no completed run in the 200 most-recently-finished "
            f"completed runs covers this series for {scope_desc}. This means "
            "'not covered within that window' — NOT 'never covered'; older "
            "completed runs are not scanned.)"
        )
        return 0
    print(_render_run(row))
    return 0


# ── handlers: action / actions ──────────────────────────────────────

@_with_conn
def _cmd_actions_list(conn, args) -> int:
    rows = query.list_actions(conn, issue_id=args.issue_id, type=args.type_,
                               status=args.status, limit=_FETCH_POOL)
    _emit(rows, args, _render_action)
    return 0


# ── handlers: stats ─────────────────────────────────────────────────

@_with_conn
def _cmd_stats(conn, args) -> int:
    _emit(query.stats(conn, by=args.by), args, _render_stats_row)
    return 0


# ── argument parser ─────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    global_ = argparse.ArgumentParser(add_help=False)
    global_.add_argument(
        "--json", action="store_true",
        help="Print exact JSON instead of a compact rendering.")
    global_.add_argument(
        "--limit", type=int, default=20,
        help="Cap how many rows print (default 20). A capped list always "
             "reports how many rows it dropped — never a silent truncation.")
    global_.add_argument(
        "--dsn", default=None,
        help="Read-only connection string. Defaults to $WDM_READONLY_PG_DSN "
             "then $WATCHDOG_READONLY_PG_DSN.")

    parser = argparse.ArgumentParser(
        prog="python -m watchdogdatamodel.cli",
        description="Read-only CLI over a wdm database.")
    groups = parser.add_subparsers(dest="group", required=True)

    guide = groups.add_parser(
        "guide", parents=[global_],
        help="Print the packaged AGENT.md doctrine. Needs no database.")
    guide.set_defaults(handler=_cmd_guide)

    stats = groups.add_parser(
        "stats", parents=[global_], help="Open-issue counts grouped by one dimension.")
    stats.add_argument("--by", choices=["check", "kind", "severity", "zone", "source"],
                        default="check", help="Grouping dimension (default: check).")
    stats.set_defaults(handler=_cmd_stats)

    # series
    series = groups.add_parser("series", help="A watched timeseries and its per-series views.")
    series_sub = series.add_subparsers(dest="cmd", required=True)

    p = series_sub.add_parser("list", parents=[global_], help="List watched series.")
    p.add_argument("--label", action="append", metavar="KEY=VALUE",
                    help="Filter by label (repeatable; AND-combined).")
    p.add_argument("--inactive", action="store_true",
                    help="Show inactive (retired) series instead of active ones.")
    p.set_defaults(handler=_cmd_series_list)

    p = series_sub.add_parser("show", parents=[global_], help="One series by its natural key.")
    p.add_argument("key")
    p.set_defaults(handler=_cmd_series_show)

    p = series_sub.add_parser(
        "context", parents=[global_],
        help="Open context-lane findings for one series: real, upstream-caused, "
             "never actionable by us.")
    p.add_argument("key")
    p.set_defaults(handler=_cmd_series_context)

    p = series_sub.add_parser(
        "checks", parents=[global_],
        help="Latest per-check outcome for one series, from its snapshot's stats.")
    p.add_argument("key")
    p.set_defaults(handler=_cmd_series_checks)

    p = series_sub.add_parser(
        "issues", parents=[global_],
        help="Every open issue on one series, both kinds (kind shown per row).")
    p.add_argument("key")
    p.set_defaults(handler=_cmd_series_issues)

    p = series_sub.add_parser(
        "snapshot", parents=[global_],
        help="Latest fetched window for one series. Heavy per-point payload "
             "arrays are never dumped compactly; use --json to get them.")
    p.add_argument("key")
    p.set_defaults(handler=_cmd_series_snapshot)

    # check / checks
    check = groups.add_parser(
        "check", aliases=["checks"], help="Check catalog: what each check asserts.")
    check_sub = check.add_subparsers(dest="cmd", required=True)

    p = check_sub.add_parser("list", parents=[global_], help="List all checks in the catalog.")
    p.set_defaults(handler=_cmd_checks_list)

    p = check_sub.add_parser(
        "show", parents=[global_],
        help="One check's definition, including its declared contract if any.")
    p.add_argument("check_id")
    p.set_defaults(handler=_cmd_checks_show)

    # issue / issues
    issue = groups.add_parser(
        "issue", aliases=["issues"],
        help="Open (or resolved) problems, one per series+check.")
    issue_sub = issue.add_subparsers(dest="cmd", required=True)

    p = issue_sub.add_parser(
        "list", parents=[global_],
        help="List issues. kind is always shown: 'issue' is ours (actionable); "
             "'context' is upstream's and never actionable by us.")
    p.add_argument("--state", choices=["open", "resolved"], default="open")
    p.add_argument("--check", dest="check_id", metavar="CHECK_ID")
    p.add_argument("--kind", choices=["issue", "context"])
    p.add_argument("--label", action="append", metavar="KEY=VALUE",
                    help="Filter by the series' label (repeatable; AND-combined).")
    p.set_defaults(handler=_cmd_issues_list)

    p = issue_sub.add_parser(
        "show", parents=[global_], help="One issue: the row plus its full diary and actions.")
    p.add_argument("issue_id")
    p.set_defaults(handler=_cmd_issues_show)

    p = issue_sub.add_parser(
        "timeline", parents=[global_],
        help="This issue's append-only diary (issue_event), oldest first. Read "
             "this before trusting the frozen `details` on the row itself.")
    p.add_argument("issue_id")
    p.set_defaults(handler=_cmd_issues_timeline)

    p = issue_sub.add_parser(
        "lineage", parents=[global_],
        help="Walk predecessor_id back through this issue's past incidents "
             "(recurrence opens a new row, not a reopened one) — read this "
             "before concluding 'first time'.")
    p.add_argument("issue_id")
    p.set_defaults(handler=_cmd_issues_lineage)

    p = issue_sub.add_parser(
        "similar", parents=[global_],
        help="Other open issues sharing this issue's series or check — is "
             "this isolated or systemic?")
    p.add_argument("issue_id")
    p.set_defaults(handler=_cmd_issues_similar)

    # run / runs
    run = groups.add_parser("run", aliases=["runs"], help="Check sweeps.")
    run_sub = run.add_subparsers(dest="cmd", required=True)

    p = run_sub.add_parser("list", parents=[global_], help="Most recent runs, newest first.")
    p.set_defaults(handler=_cmd_runs_list)

    p = run_sub.add_parser("show", parents=[global_], help="One run by id.")
    p.add_argument("run_id")
    p.set_defaults(handler=_cmd_runs_show)

    p = run_sub.add_parser(
        "covering", parents=[global_],
        help="Did a run cover this series? With no --check: which run last "
             "covered this series, whatever checks it declared. With --check: "
             "narrower — did a run re-check THIS check on this series. Scans "
             "only the 200 most-recently-finished completed runs, so 'not "
             "covered' means not covered within that window, not 'never "
             "covered'.")
    p.add_argument("key")
    p.add_argument("--check", dest="check_id", metavar="CHECK_ID",
                    help="Narrow to whether this specific check was re-run on the series.")
    p.set_defaults(handler=_cmd_runs_covering)

    # action / actions
    action = groups.add_parser(
        "action", aliases=["actions"], help="Fix attempts: heals, investigations.")
    action_sub = action.add_subparsers(dest="cmd", required=True)

    p = action_sub.add_parser("list", parents=[global_], help="List actions, optionally filtered.")
    p.add_argument("--issue", dest="issue_id", metavar="ISSUE_ID")
    p.add_argument("--type", dest="type_", metavar="TYPE")
    p.add_argument("--status", choices=["queued", "running", "succeeded", "failed", "canceled"])
    p.set_defaults(handler=_cmd_actions_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
