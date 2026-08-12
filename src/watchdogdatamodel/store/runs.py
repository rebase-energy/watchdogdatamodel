"""Check runs: declared scope, completion, coverage (spec §3.3)."""
from psycopg.types.json import Jsonb

from ..models import CheckRun, Series
from ..scope import scope_covers, validate_scope


def start_run(conn, *, scope, trigger, window_start=None, window_end=None,
              metadata=None) -> CheckRun:
    validate_scope(scope)
    row = conn.execute(
        """
        INSERT INTO check_run (status, "trigger", scope, window_start, window_end, metadata)
        VALUES ('running', %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (trigger, Jsonb(scope), window_start, window_end, Jsonb(metadata or {})),
    ).fetchone()
    return CheckRun(**row)


def finish_run(conn, run_id, *, status="completed", stats=None) -> CheckRun:
    if status not in ("completed", "failed"):
        raise ValueError(f"finish status must be completed|failed, got {status!r}")
    row = conn.execute(
        """
        UPDATE check_run SET status = %s, finished_at = now(), stats = %s
        WHERE id = %s AND status = 'running'
        RETURNING *
        """,
        (status, Jsonb(stats or {}), run_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"run {run_id} is not running (already finished or missing)")
    return CheckRun(**row)


def run_covers(run: CheckRun, *, series: Series, check_id: str) -> bool:
    """The coverage rule: only completed runs cover anything."""
    if run.status != "completed":
        return False
    return scope_covers(run.scope, series_id=str(series.id), labels=series.labels,
                        check_id=check_id, series_key=series.key)
