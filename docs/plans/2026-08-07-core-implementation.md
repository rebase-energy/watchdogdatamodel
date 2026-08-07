# WatchdogDataModel Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the repo deliverables of the quality-ops data model spec: SQL schema, `watchdogdatamodel` Python package, and the contract-test suite.

**Architecture:** A `src/`-layout Python package wrapping a single idempotent PostgreSQL DDL file. Pure helpers (fingerprint, scope, wire format, evidence) have no DB dependency; store modules (`store/*.py`) are thin psycopg3 functions taking an open connection — no ORM, no connection pooling, no framework. Guarantees the spec calls contract tests are enforced twice: in the store API and at the database level (partial unique indexes, CHECK constraints, triggers).

**Tech Stack:** Python ≥3.11, uv, pydantic v2, psycopg 3 (binary), timedatamodel (PyPI), pytest. Postgres ≥14 for tests (throwaway DB, dbname MUST end in `_test`).

## Global Constraints

- Spec is source of truth: `docs/specs/2026-08-07-quality-ops-data-model-design.md`. Field names/enums verbatim from spec §3.
- The spec's table `check` collides with the SQL reserved word; physical table is `check_definition`. Column names referencing it stay `check_id`. (Spec gets a note in Task 3.)
- Column `trigger` on `check_run` is always double-quoted in SQL.
- Run Python tools as `uv run python -m <tool>` (never bare `uv run pytest`).
- DB tests skip when `WDM_TEST_PG_DSN` is unset; hard-abort if its dbname does not end `_test` (prod-wipe incident 2026-08-06 in the PSD repo is the reason).
- Timestamps timezone-aware UTC everywhere; `datetime.now(timezone.utc)`.
- All commits in THIS repo (watchdogdatamodel), never the enclosing PSD repo.
- Events are emitted by store functions, never by callers directly (single write path).

## Test database bring-up (execution prerequisite)

The PSD repo's compose file has a `watchdog-pg` service (recipe documented in
PSD `tests/watchdog_db_guard.py`). From the PSD repo root:
`docker compose up -d watchdog-pg`, then find the mapped port with
`docker compose port watchdog-pg 5432`. Create a dedicated DB so this repo
never shares tables with PSD tests:
`psql "postgresql://<user>:<pw>@localhost:<port>/postgres" -c 'CREATE DATABASE wdm_test'`
(or via `docker compose exec watchdog-pg createdb -U <user> wdm_test`).
Export `WDM_TEST_PG_DSN=postgresql://<user>:<pw>@localhost:<port>/wdm_test`.

---

### Task 1: Package scaffold + fingerprint helper

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `src/watchdogdatamodel/__init__.py`,
  `src/watchdogdatamodel/fingerprint.py`, `tests/__init__.py` (empty), `tests/test_fingerprint.py`

**Interfaces:**
- Produces: `compute_fingerprint(series_key: str, check_id: str, discriminator: str | None = None) -> str` — `"<key>|<check>"` or `"<key>|<check>|<disc>"`. Raises `ValueError` on empty key/check or on `"|"` inside any part.

- [ ] **Step 1: Write scaffold files**

`pyproject.toml`:
```toml
[project]
name = "watchdogdatamodel"
version = "0.1.0"
description = "Generalized data model for timeseries data-quality operations"
requires-python = ">=3.11"
dependencies = [
    "pydantic>=2.7",
    "psycopg[binary]>=3.1",
    "timedatamodel",
]

[dependency-groups]
dev = ["pytest>=8"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/watchdogdatamodel"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`.gitignore`:
```
__pycache__/
*.pyc
.venv/
dist/
.pytest_cache/
```

`src/watchdogdatamodel/__init__.py`:
```python
"""watchdogdatamodel — generalized data model for timeseries quality ops."""

__version__ = "0.1.0"
```

- [ ] **Step 2: Write the failing test** — `tests/test_fingerprint.py`:
```python
import pytest

from watchdogdatamodel.fingerprint import compute_fingerprint


def test_series_plus_check():
    assert compute_fingerprint("se-se1-production-entsoe", "freshness") == (
        "se-se1-production-entsoe|freshness"
    )


def test_discriminator_appends():
    fp = compute_fingerprint("de-wind-fc", "missing_run", "2026-08-07T06:00:00+00:00")
    assert fp == "de-wind-fc|missing_run|2026-08-07T06:00:00+00:00"


@pytest.mark.parametrize("key,check", [("", "freshness"), ("k", ""), ("a|b", "c")])
def test_invalid_parts_rejected(key, check):
    with pytest.raises(ValueError):
        compute_fingerprint(key, check)
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run python -m pytest tests/test_fingerprint.py -v`
Expected: FAIL / import error (`fingerprint` module missing)

- [ ] **Step 4: Implement** — `src/watchdogdatamodel/fingerprint.py`:
```python
"""Issue fingerprints: the dedup identity (spec §3.4)."""


def compute_fingerprint(series_key: str, check_id: str, discriminator: str | None = None) -> str:
    """Build the fingerprint for a check-origin issue.

    ``series_key + '|' + check_id`` plus an optional product discriminator
    (e.g. a forecast run's knowledge_time on OVERLAPPING series).
    """
    parts = [series_key, check_id] + ([discriminator] if discriminator is not None else [])
    for part in parts[:2]:
        if not part:
            raise ValueError("series_key and check_id must be non-empty")
    if any("|" in p for p in parts[:2]):
        raise ValueError("'|' is the fingerprint separator and cannot appear in parts")
    return "|".join(parts)
```

- [ ] **Step 5: Run to verify pass** — same command, expected: 3 tests PASS (parametrized counts as 3).

- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: package scaffold + fingerprint helper"`

---

### Task 2: Scope matching / coverage helper

**Files:**
- Create: `src/watchdogdatamodel/scope.py`, `tests/test_scope.py`

**Interfaces:**
- Produces:
  - `validate_scope(scope: dict) -> dict` — raises `ValueError` unless
    `scope == {"series": "all" | {"labels": {...}} | {"ids": [<str>...]}, "checks": "all" | [<str>...]}`.
  - `scope_covers(scope: dict, *, series_id: str, labels: dict, check_id: str) -> bool` —
    the coverage rule's matcher. Label matching = subset equality (every
    scope label present and equal in the series labels).

- [ ] **Step 1: Write the failing test** — `tests/test_scope.py`:
```python
import pytest

from watchdogdatamodel.scope import scope_covers, validate_scope

LBL = {"zone": "SE-SE1", "data_type": "production", "source": "entsoe"}


def test_all_all_covers_everything():
    s = validate_scope({"series": "all", "checks": "all"})
    assert scope_covers(s, series_id="x", labels=LBL, check_id="freshness")


def test_label_filter_subset_match():
    s = {"series": {"labels": {"zone": "SE-SE1"}}, "checks": "all"}
    assert scope_covers(s, series_id="x", labels=LBL, check_id="freshness")
    assert not scope_covers(s, series_id="x", labels={"zone": "FI"}, check_id="freshness")


def test_id_list_and_check_list():
    s = {"series": {"ids": ["a", "b"]}, "checks": ["gross_range"]}
    assert scope_covers(s, series_id="a", labels={}, check_id="gross_range")
    assert not scope_covers(s, series_id="c", labels={}, check_id="gross_range")
    assert not scope_covers(s, series_id="a", labels={}, check_id="freshness")


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"series": "all"},
        {"series": "some", "checks": "all"},
        {"series": {"labels": {}, "ids": []}, "checks": "all"},
        {"series": "all", "checks": "none"},
    ],
)
def test_invalid_scopes_rejected(bad):
    with pytest.raises(ValueError):
        validate_scope(bad)
```

- [ ] **Step 2: Run to verify failure** — `uv run python -m pytest tests/test_scope.py -v` → import error.

- [ ] **Step 3: Implement** — `src/watchdogdatamodel/scope.py`:
```python
"""Declared run scope and the coverage rule's matcher (spec §3.3).

healthy = covered by a completed run + no open issue. This module answers
the "covered" half: did a run's declared scope include (series, check)?
"""


def validate_scope(scope: dict) -> dict:
    if set(scope) != {"series", "checks"}:
        raise ValueError("scope must have exactly the keys 'series' and 'checks'")
    series = scope["series"]
    if series != "all":
        if not isinstance(series, dict) or len(series) != 1:
            raise ValueError("scope['series'] must be 'all', {'labels': {...}} or {'ids': [...]}")
        (kind, value), = series.items()
        if kind == "labels":
            if not isinstance(value, dict):
                raise ValueError("scope['series']['labels'] must be a dict")
        elif kind == "ids":
            if not isinstance(value, list) or not all(isinstance(i, str) for i in value):
                raise ValueError("scope['series']['ids'] must be a list of strings")
        else:
            raise ValueError(f"unknown series selector {kind!r}")
    checks = scope["checks"]
    if checks != "all" and (
        not isinstance(checks, list) or not all(isinstance(c, str) for c in checks)
    ):
        raise ValueError("scope['checks'] must be 'all' or a list of check ids")
    return scope


def scope_covers(scope: dict, *, series_id: str, labels: dict, check_id: str) -> bool:
    validate_scope(scope)
    series = scope["series"]
    if series != "all":
        (kind, value), = series.items()
        if kind == "ids" and series_id not in value:
            return False
        if kind == "labels" and any(labels.get(k) != v for k, v in value.items()):
            return False
    checks = scope["checks"]
    if checks != "all" and check_id not in checks:
        return False
    return True
```

- [ ] **Step 4: Run to verify pass** — expected: all PASS.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: scope validation and coverage matcher"`

---

### Task 3: SQL schema + bootstrap + DB test guard

**Files:**
- Create: `src/watchdogdatamodel/schema.sql`, `src/watchdogdatamodel/store/__init__.py`,
  `src/watchdogdatamodel/store/db.py`, `tests/conftest.py`, `tests/test_schema.py`
- Modify: `docs/specs/2026-08-07-quality-ops-data-model-design.md` (§3.2: physical-name note)

**Interfaces:**
- Produces:
  - `store.db.connect(dsn: str) -> psycopg.Connection` — autocommit, `dict_row` rows.
  - `store.db.bootstrap(conn) -> None` — executes packaged `schema.sql`, idempotent.
  - conftest: `conn` fixture (drops the 7 tables, re-bootstraps per test), `requires_db` skip marker.
- Consumes: nothing prior.

- [ ] **Step 1: Write DDL** — `src/watchdogdatamodel/schema.sql` (order matters: `action` before `issue_event` for the FK):
```sql
-- watchdogdatamodel core schema (spec docs/specs/2026-08-07-*.md §3). Idempotent.

CREATE TABLE IF NOT EXISTS series (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    key              text NOT NULL UNIQUE,
    name             text NOT NULL,
    description      text,
    unit             text NOT NULL DEFAULT 'dimensionless',
    timezone         text NOT NULL DEFAULT 'UTC',
    frequency        text,
    data_type        text,
    timeseries_type  text NOT NULL DEFAULT 'FLAT' CHECK (timeseries_type IN ('FLAT', 'OVERLAPPING')),
    labels           jsonb NOT NULL DEFAULT '{}',
    active           boolean NOT NULL DEFAULT true,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS series_labels_gin ON series USING gin (labels);

-- Physical name: "check" is a reserved SQL word, so the check catalog
-- (spec §3.2 table `check`) is materialized as check_definition.
CREATE TABLE IF NOT EXISTS check_definition (
    id             text PRIMARY KEY,
    name           text NOT NULL,
    description    text,
    dimension      text CHECK (dimension IS NULL OR dimension IN
                     ('completeness', 'freshness', 'validity', 'consistency', 'accuracy')),
    default_params jsonb NOT NULL DEFAULT '{}',
    enabled        boolean NOT NULL DEFAULT true,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS check_run (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    status       text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'completed', 'failed')),
    "trigger"    text NOT NULL CHECK ("trigger" IN ('scheduled', 'targeted', 'event', 'backtest', 'manual')),
    scope        jsonb NOT NULL,
    window_start timestamptz,
    window_end   timestamptz,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    stats        jsonb NOT NULL DEFAULT '{}',
    metadata     jsonb NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS issue (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    fingerprint        text NOT NULL,
    origin             text NOT NULL CHECK (origin IN ('check', 'human', 'agent', 'external')),
    series_id          uuid REFERENCES series(id),
    related_series     jsonb NOT NULL DEFAULT '[]',
    check_id           text REFERENCES check_definition(id),
    state              text NOT NULL DEFAULT 'open' CHECK (state IN ('open', 'resolved')),
    stage              text NOT NULL DEFAULT 'new',
    severity           text NOT NULL DEFAULT 'medium' CHECK (severity IN ('critical', 'high', 'medium', 'low')),
    title              text NOT NULL,
    details            jsonb NOT NULL DEFAULT '{}',
    valid_start        timestamptz,
    valid_end          timestamptz,
    knowledge_time     timestamptz,
    first_seen_at      timestamptz NOT NULL DEFAULT now(),
    last_seen_at       timestamptz NOT NULL DEFAULT now(),
    detected_by_run    uuid REFERENCES check_run(id),
    assignee           text,
    resolved_at        timestamptz,
    resolution_reason  text CHECK (resolution_reason IS NULL OR resolution_reason IN
                         ('fixed', 'recovered', 'false_positive', 'missing_at_source',
                          'wont_fix', 'superseded', 'stale')),
    resolution_comment text,
    resolved_by        text,
    predecessor_id     uuid REFERENCES issue(id),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT resolved_requires_reason CHECK (state <> 'resolved' OR resolution_reason IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS issue_one_open_per_fingerprint
    ON issue (fingerprint) WHERE state = 'open';
CREATE INDEX IF NOT EXISTS issue_series_idx ON issue (series_id);
CREATE INDEX IF NOT EXISTS issue_board_idx ON issue (state, stage);

CREATE TABLE IF NOT EXISTS action (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    issue_id     uuid NOT NULL REFERENCES issue(id),
    type         text NOT NULL,
    status       text NOT NULL DEFAULT 'queued' CHECK (status IN
                   ('queued', 'running', 'succeeded', 'failed', 'canceled')),
    transitions  jsonb NOT NULL DEFAULT '[]',
    params       jsonb NOT NULL DEFAULT '{}',
    outcome      jsonb NOT NULL DEFAULT '{}',
    requested_by text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    started_at   timestamptz,
    finished_at  timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS action_one_live_per_issue_type
    ON action (issue_id, type) WHERE status IN ('queued', 'running');
CREATE INDEX IF NOT EXISTS action_queue_idx ON action (type, created_at) WHERE status = 'queued';

CREATE OR REPLACE FUNCTION wdm_freeze_terminal_action() RETURNS trigger AS $$
BEGIN
    IF OLD.status IN ('succeeded', 'failed', 'canceled') THEN
        RAISE EXCEPTION 'action % is terminal (%) and frozen', OLD.id, OLD.status;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS action_freeze ON action;
CREATE TRIGGER action_freeze BEFORE UPDATE OR DELETE ON action
    FOR EACH ROW EXECUTE FUNCTION wdm_freeze_terminal_action();

CREATE TABLE IF NOT EXISTS issue_event (
    id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    issue_id  uuid NOT NULL REFERENCES issue(id),
    at        timestamptz NOT NULL DEFAULT now(),
    type      text NOT NULL,
    actor     text NOT NULL,
    run_id    uuid REFERENCES check_run(id),
    action_id uuid REFERENCES action(id),
    data      jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS issue_event_issue_idx ON issue_event (issue_id, at);

CREATE OR REPLACE FUNCTION wdm_forbid_event_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'issue_event is append-only';
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS issue_event_append_only ON issue_event;
CREATE TRIGGER issue_event_append_only BEFORE UPDATE OR DELETE ON issue_event
    FOR EACH ROW EXECUTE FUNCTION wdm_forbid_event_mutation();

CREATE TABLE IF NOT EXISTS series_snapshot (
    series_id    uuid PRIMARY KEY REFERENCES series(id),
    run_id       uuid REFERENCES check_run(id),
    fetched_at   timestamptz NOT NULL DEFAULT now(),
    window_start timestamptz NOT NULL,
    window_end   timestamptz NOT NULL,
    payload      jsonb NOT NULL,
    stats        jsonb NOT NULL DEFAULT '{}'
);
```

- [ ] **Step 2: Write db module** — `src/watchdogdatamodel/store/__init__.py` (empty) and `src/watchdogdatamodel/store/db.py`:
```python
"""Connection + schema bootstrap."""
from importlib.resources import files

import psycopg
from psycopg.rows import dict_row


def connect(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, autocommit=True, row_factory=dict_row)


def bootstrap(conn: psycopg.Connection) -> None:
    """Create all tables/indexes/triggers. Idempotent by construction."""
    ddl = (files("watchdogdatamodel") / "schema.sql").read_text()
    conn.execute(ddl)
```
Note: hatchling includes non-`.py` files inside the package dir by default; verify `schema.sql` ships (it does with `packages = ["src/watchdogdatamodel"]`).

- [ ] **Step 3: Write conftest with the throwaway guard** — `tests/conftest.py`:
```python
import os

import pytest

DSN = os.environ.get("WDM_TEST_PG_DSN")

if DSN:
    from psycopg.conninfo import conninfo_to_dict

    _dbname = conninfo_to_dict(DSN).get("dbname") or ""
    if not str(_dbname).endswith("_test"):
        raise SystemExit(
            "REFUSING to run: WDM_TEST_PG_DSN must point at a throwaway database "
            f"whose name ends in '_test' (got {_dbname!r}). Store tests DROP tables."
        )

requires_db = pytest.mark.skipif(not DSN, reason="WDM_TEST_PG_DSN not set")

TABLES = [
    "series_snapshot",
    "issue_event",
    "action",
    "issue",
    "check_run",
    "check_definition",
    "series",
]


@pytest.fixture()
def conn():
    from watchdogdatamodel.store import db as dbmod

    with dbmod.connect(DSN) as c:
        for t in TABLES:
            c.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
        dbmod.bootstrap(c)
        yield c
```

- [ ] **Step 4: Write the failing test** — `tests/test_schema.py`:
```python
from tests.conftest import TABLES, requires_db

pytestmark = requires_db


def test_bootstrap_creates_all_tables(conn):
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
    ).fetchall()
    names = {r["table_name"] for r in rows}
    assert set(TABLES) <= names


def test_bootstrap_is_idempotent(conn):
    from watchdogdatamodel.store.db import bootstrap

    bootstrap(conn)  # second run on an already-bootstrapped DB must not raise
```

- [ ] **Step 5: Run** — `uv run python -m pytest tests/test_schema.py -v` with the DSN exported. Expected: PASS (2). Without DSN: SKIP (2).
- [ ] **Step 6: Add spec note** — append to spec §3.2, after the table: *"Implementation note: `check` is a reserved SQL word; the physical table is named `check_definition`. All `check_id` column names are unchanged."*
- [ ] **Step 7: Commit** — `git add -A && git commit -m "feat: core schema DDL, bootstrap, throwaway-DB test guard"`

---

### Task 4: Row models

**Files:**
- Create: `src/watchdogdatamodel/models.py`, `tests/test_models.py`

**Interfaces:**
- Produces (pydantic v2, `model_config = ConfigDict(extra="ignore")`, all constructed via `Model(**row)` from `dict_row` results):
  - `Series(id: UUID, key: str, name: str, description: str | None, unit: str, timezone: str, frequency: str | None, data_type: str | None, timeseries_type: str, labels: dict, active: bool, created_at: datetime, updated_at: datetime)`
  - `CheckDef(id: str, name: str, description: str | None, dimension: str | None, default_params: dict, enabled: bool, created_at: datetime, updated_at: datetime)`
  - `CheckRun(id: UUID, status: str, trigger: str, scope: dict, window_start: datetime | None, window_end: datetime | None, started_at: datetime, finished_at: datetime | None, stats: dict, metadata: dict)`
  - `Issue(id: UUID, fingerprint: str, origin: str, series_id: UUID | None, related_series: list, check_id: str | None, state: str, stage: str, severity: str, title: str, details: dict, valid_start: datetime | None, valid_end: datetime | None, knowledge_time: datetime | None, first_seen_at: datetime, last_seen_at: datetime, detected_by_run: UUID | None, assignee: str | None, resolved_at: datetime | None, resolution_reason: str | None, resolution_comment: str | None, resolved_by: str | None, predecessor_id: UUID | None, created_at: datetime, updated_at: datetime)`
  - `IssueEvent(id: int, issue_id: UUID, at: datetime, type: str, actor: str, run_id: UUID | None, action_id: UUID | None, data: dict)`
  - `Action(id: UUID, issue_id: UUID, type: str, status: str, transitions: list, params: dict, outcome: dict, requested_by: str, created_at: datetime, started_at: datetime | None, finished_at: datetime | None)`
  - `Snapshot(series_id: UUID, run_id: UUID | None, fetched_at: datetime, window_start: datetime, window_end: datetime, payload: dict, stats: dict)`
  - Module constants: `RESOLUTION_REASONS`, `TERMINAL_ACTION_STATUSES = frozenset({"succeeded", "failed", "canceled"})`, `CORE_EVENT_TYPES`.

- [ ] **Step 1: Write the failing test** — `tests/test_models.py`:
```python
from datetime import datetime, timezone
from uuid import uuid4

from watchdogdatamodel.models import Action, Issue, Series, TERMINAL_ACTION_STATUSES

NOW = datetime.now(timezone.utc)


def test_series_from_row_dict():
    s = Series(
        id=uuid4(), key="k", name="n", description=None, unit="MW", timezone="UTC",
        frequency="PT1H", data_type="OBSERVATION", timeseries_type="FLAT",
        labels={"zone": "SE-SE1"}, active=True, created_at=NOW, updated_at=NOW,
    )
    assert s.labels["zone"] == "SE-SE1"


def test_issue_and_action_minimal():
    i = Issue(
        id=uuid4(), fingerprint="k|freshness", origin="check", series_id=None,
        related_series=[], check_id=None, state="open", stage="new", severity="medium",
        title="t", details={}, valid_start=None, valid_end=None, knowledge_time=None,
        first_seen_at=NOW, last_seen_at=NOW, detected_by_run=None, assignee=None,
        resolved_at=None, resolution_reason=None, resolution_comment=None,
        resolved_by=None, predecessor_id=None, created_at=NOW, updated_at=NOW,
    )
    a = Action(
        id=uuid4(), issue_id=i.id, type="backfill", status="queued", transitions=[],
        params={}, outcome={}, requested_by="user:davide", created_at=NOW,
        started_at=None, finished_at=None,
    )
    assert a.status not in TERMINAL_ACTION_STATUSES
```

- [ ] **Step 2: Run to verify failure**, then **Step 3: Implement** `src/watchdogdatamodel/models.py`:
```python
"""Pydantic mirrors of the schema rows (spec §3)."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

RESOLUTION_REASONS = frozenset(
    {"fixed", "recovered", "false_positive", "missing_at_source", "wont_fix", "superseded", "stale"}
)
TERMINAL_ACTION_STATUSES = frozenset({"succeeded", "failed", "canceled"})
CORE_EVENT_TYPES = frozenset(
    {
        "opened", "detected_again", "not_seen", "stage_changed", "resolved",
        "reopened", "action_requested", "action_finished", "external_changed", "comment",
    }
)


class _Row(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Series(_Row):
    id: UUID
    key: str
    name: str
    description: str | None = None
    unit: str
    timezone: str
    frequency: str | None = None
    data_type: str | None = None
    timeseries_type: str
    labels: dict
    active: bool
    created_at: datetime
    updated_at: datetime


class CheckDef(_Row):
    id: str
    name: str
    description: str | None = None
    dimension: str | None = None
    default_params: dict
    enabled: bool
    created_at: datetime
    updated_at: datetime


class CheckRun(_Row):
    id: UUID
    status: str
    trigger: str
    scope: dict
    window_start: datetime | None = None
    window_end: datetime | None = None
    started_at: datetime
    finished_at: datetime | None = None
    stats: dict
    metadata: dict


class Issue(_Row):
    id: UUID
    fingerprint: str
    origin: str
    series_id: UUID | None = None
    related_series: list
    check_id: str | None = None
    state: str
    stage: str
    severity: str
    title: str
    details: dict
    valid_start: datetime | None = None
    valid_end: datetime | None = None
    knowledge_time: datetime | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    detected_by_run: UUID | None = None
    assignee: str | None = None
    resolved_at: datetime | None = None
    resolution_reason: str | None = None
    resolution_comment: str | None = None
    resolved_by: str | None = None
    predecessor_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class IssueEvent(_Row):
    id: int
    issue_id: UUID
    at: datetime
    type: str
    actor: str
    run_id: UUID | None = None
    action_id: UUID | None = None
    data: dict


class Action(_Row):
    id: UUID
    issue_id: UUID
    type: str
    status: str
    transitions: list
    params: dict
    outcome: dict
    requested_by: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class Snapshot(_Row):
    series_id: UUID
    run_id: UUID | None = None
    fetched_at: datetime
    window_start: datetime
    window_end: datetime
    payload: dict
    stats: dict
```

- [ ] **Step 4: Run to verify pass**, **Step 5: Commit** — `git commit -m "feat: pydantic row models"`

---

### Task 5: timedatamodel wire format + evidence excerpt

**Files:**
- Create: `src/watchdogdatamodel/wire.py`, `src/watchdogdatamodel/evidence.py`,
  `tests/test_wire.py`, `tests/test_evidence.py`

**Interfaces:**
- Produces:
  - `wire.dump_timeseries(ts: timedatamodel.TimeSeries) -> dict` — JSON-safe:
    `{"shape": "SIMPLE"|"VERSIONED"|..., "meta": {name, unit, timezone, frequency, data_type, timeseries_type}, "columns": {col: [...]}}`, timestamps as ISO strings. Raises `ValueError` on metadata-only series.
  - `wire.load_timeseries(payload: dict) -> TimeSeries` — exact inverse.
  - `evidence.excerpt(ts: TimeSeries, start: datetime, end: datetime) -> dict` —
    wire payload restricted to `start <= valid_time <= end`.

- [ ] **Step 1: Write the failing tests** — `tests/test_wire.py`:
```python
from datetime import datetime, timedelta, timezone

import polars as pl
from timedatamodel import DataType, Frequency, TimeSeries

from watchdogdatamodel.wire import dump_timeseries, load_timeseries

T0 = datetime(2026, 8, 1, tzinfo=timezone.utc)


def hourly(n=4, knowledge=False):
    cols = {
        "valid_time": [T0 + timedelta(hours=i) for i in range(n)],
        "value": [float(i) for i in range(n)],
    }
    if knowledge:
        cols = {"knowledge_time": [T0] * n, **cols}
    return TimeSeries.from_list(
        cols, name="wind", unit="MW", frequency=Frequency.PT1H, data_type=DataType.OBSERVATION
    )


def test_simple_round_trip():
    ts = hourly()
    payload = dump_timeseries(ts)
    assert payload["shape"] == "SIMPLE"
    assert isinstance(payload["columns"]["valid_time"][0], str)
    back = load_timeseries(payload)
    assert back.shape == ts.shape and back.unit == "MW" and back.num_rows == 4
    assert back.to_list() == ts.to_list()


def test_versioned_round_trip():
    payload = dump_timeseries(hourly(knowledge=True))
    assert payload["shape"] == "VERSIONED"
    assert load_timeseries(payload).to_list()["knowledge_time"][0] == T0
```
`tests/test_evidence.py`:
```python
from datetime import timedelta

from watchdogdatamodel.evidence import excerpt
from watchdogdatamodel.wire import load_timeseries

from tests.test_wire import T0, hourly


def test_excerpt_restricts_window():
    e = excerpt(hourly(n=24), T0 + timedelta(hours=5), T0 + timedelta(hours=7))
    ts = load_timeseries(e)
    assert ts.num_rows == 3
    assert ts.to_list()["valid_time"][0] == T0 + timedelta(hours=5)
```

- [ ] **Step 2: Run to verify failure**, then **Step 3: Implement** — `src/watchdogdatamodel/wire.py`:
```python
"""(De)serialize timedatamodel TimeSeries to JSON-safe dicts for jsonb storage."""
from datetime import datetime

from timedatamodel import DataType, Frequency, TimeSeries
from timedatamodel.enums import TimeSeriesType

_TIME_COLS = ("valid_time", "knowledge_time", "change_time")


def dump_timeseries(ts: TimeSeries) -> dict:
    if not ts.has_df:
        raise ValueError("cannot dump a metadata-only TimeSeries")
    columns = {}
    for col, values in ts.to_list().items():
        if col in _TIME_COLS:
            columns[col] = [v.isoformat() if v is not None else None for v in values]
        else:
            columns[col] = values
    return {
        "shape": ts.shape.value,
        "meta": {
            "name": ts.name,
            "unit": ts.unit,
            "timezone": ts.timezone,
            "frequency": str(ts.frequency) if ts.frequency else None,
            "data_type": ts.data_type.value if ts.data_type else None,
            "timeseries_type": ts.timeseries_type.value,
        },
        "columns": columns,
    }


def load_timeseries(payload: dict) -> TimeSeries:
    columns = {}
    for col, values in payload["columns"].items():
        if col in _TIME_COLS:
            columns[col] = [datetime.fromisoformat(v) if v is not None else None for v in values]
        else:
            columns[col] = values
    meta = payload["meta"]
    return TimeSeries.from_list(
        columns,
        name=meta["name"],
        unit=meta["unit"],
        timezone=meta["timezone"],
        frequency=Frequency(meta["frequency"]) if meta["frequency"] else None,
        data_type=DataType(meta["data_type"]) if meta["data_type"] else None,
        timeseries_type=TimeSeriesType(meta["timeseries_type"]),
    )
```
`src/watchdogdatamodel/evidence.py`:
```python
"""Freeze the affected slice of data onto an issue at detection (spec §3.4)."""
from datetime import datetime

import polars as pl
from timedatamodel import TimeSeries

from .wire import dump_timeseries


def excerpt(ts: TimeSeries, start: datetime, end: datetime) -> dict:
    """Wire payload of ``ts`` restricted to start <= valid_time <= end."""
    df = ts.to_polars().filter(
        (pl.col("valid_time") >= start) & (pl.col("valid_time") <= end)
    )
    clipped = TimeSeries.from_polars(
        df,
        name=ts.name,
        description=ts.description,
        unit=ts.unit,
        timezone=ts.timezone,
        frequency=ts.frequency,
        data_type=ts.data_type,
        timeseries_type=ts.timeseries_type,
    )
    return dump_timeseries(clipped)
```

- [ ] **Step 4: Run to verify pass** (`uv run python -m pytest tests/test_wire.py tests/test_evidence.py -v`). If `Frequency`/`TimeSeriesType` import paths differ from timedatamodel's published version, adjust imports to match the installed package (check with `uv run python -c "import timedatamodel as t; print(t.__all__)"`), keeping behavior identical.
- [ ] **Step 5: Commit** — `git commit -m "feat: TimeSeries wire format + evidence excerpting"`

---

### Task 6: Series + snapshot store

**Files:**
- Create: `src/watchdogdatamodel/store/series.py`, `tests/test_store_series.py`

**Interfaces:**
- Consumes: `store.db` fixture pattern, `models.Series/Snapshot`, `wire`.
- Produces:
  - `upsert_series(conn, *, key, name, description=None, unit="dimensionless", timezone="UTC", frequency=None, data_type=None, timeseries_type="FLAT", labels=None, active=True) -> Series` — insert or update-by-key.
  - `get_series(conn, key: str) -> Series | None`
  - `list_series(conn, labels: dict | None = None, active: bool | None = True) -> list[Series]` — labels = jsonb containment (`labels @> %s`).
  - `series_to_timeseries(s: Series) -> TimeSeries` (metadata-only) and `series_fields_from_timeseries(ts: TimeSeries) -> dict` (kwargs for `upsert_series`, minus key/labels).
  - `upsert_snapshot(conn, *, series_id, ts: TimeSeries, run_id=None, stats=None) -> Snapshot` — window bounds derived from the payload's valid_time min/max; replaces any existing row (latest-only).
  - `get_snapshot(conn, series_id) -> Snapshot | None`; `snapshot_timeseries(snap: Snapshot) -> TimeSeries`.

- [ ] **Step 1: Write the failing tests** — `tests/test_store_series.py`:
```python
from tests.conftest import requires_db
from tests.test_wire import hourly

pytestmark = requires_db


def _mk(conn, key="se-se1-production-entsoe"):
    from watchdogdatamodel.store.series import upsert_series

    return upsert_series(
        conn, key=key, name="SE-SE1 production (entsoe)", unit="MW", frequency="PT1H",
        data_type="OBSERVATION", labels={"zone": "SE-SE1", "source": "entsoe"},
    )


def test_upsert_insert_then_update(conn):
    from watchdogdatamodel.store.series import get_series, upsert_series

    s1 = _mk(conn)
    s2 = upsert_series(conn, key=s1.key, name="renamed", unit="MW")
    assert s2.id == s1.id and get_series(conn, s1.key).name == "renamed"


def test_list_by_label_containment(conn):
    from watchdogdatamodel.store.series import list_series

    _mk(conn)
    _mk(conn, key="fi-production-fingrid")
    hits = list_series(conn, labels={"zone": "SE-SE1"})
    assert [s.key for s in hits] == ["se-se1-production-entsoe"]


def test_timedatamodel_round_trip(conn):
    from watchdogdatamodel.store.series import series_to_timeseries

    ts = series_to_timeseries(_mk(conn))
    assert not ts.has_df
    assert (ts.name, ts.unit, str(ts.frequency)) == ("SE-SE1 production (entsoe)", "MW", "PT1H")


def test_snapshot_latest_only_and_round_trip(conn):
    from watchdogdatamodel.store.series import (
        get_snapshot, snapshot_timeseries, upsert_snapshot,
    )

    s = _mk(conn)
    upsert_snapshot(conn, series_id=s.id, ts=hourly(n=4))
    upsert_snapshot(conn, series_id=s.id, ts=hourly(n=6))
    n = conn.execute("SELECT count(*) AS c FROM series_snapshot").fetchone()["c"]
    assert n == 1
    snap = get_snapshot(conn, s.id)
    assert snapshot_timeseries(snap).num_rows == 6
```

- [ ] **Step 2: Run to verify failure**, then **Step 3: Implement** — `src/watchdogdatamodel/store/series.py`:
```python
"""Series catalog + latest-only snapshots (spec §3.1, §3.7)."""
from psycopg.types.json import Jsonb
from timedatamodel import DataType, Frequency, TimeSeries
from timedatamodel.enums import TimeSeriesType

from ..models import Series, Snapshot
from ..wire import dump_timeseries, load_timeseries


def upsert_series(conn, *, key, name, description=None, unit="dimensionless",
                  timezone="UTC", frequency=None, data_type=None,
                  timeseries_type="FLAT", labels=None, active=True) -> Series:
    row = conn.execute(
        """
        INSERT INTO series (key, name, description, unit, timezone, frequency,
                            data_type, timeseries_type, labels, active)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (key) DO UPDATE SET
            name = EXCLUDED.name, description = EXCLUDED.description,
            unit = EXCLUDED.unit, timezone = EXCLUDED.timezone,
            frequency = EXCLUDED.frequency, data_type = EXCLUDED.data_type,
            timeseries_type = EXCLUDED.timeseries_type, labels = EXCLUDED.labels,
            active = EXCLUDED.active, updated_at = now()
        RETURNING *
        """,
        (key, name, description, unit, timezone, frequency, data_type,
         timeseries_type, Jsonb(labels or {}), active),
    ).fetchone()
    return Series(**row)


def get_series(conn, key: str) -> Series | None:
    row = conn.execute("SELECT * FROM series WHERE key = %s", (key,)).fetchone()
    return Series(**row) if row else None


def list_series(conn, labels: dict | None = None, active: bool | None = True) -> list[Series]:
    q, params = "SELECT * FROM series WHERE true", []
    if labels:
        q += " AND labels @> %s"
        params.append(Jsonb(labels))
    if active is not None:
        q += " AND active = %s"
        params.append(active)
    q += " ORDER BY key"
    return [Series(**r) for r in conn.execute(q, params).fetchall()]


def series_to_timeseries(s: Series) -> TimeSeries:
    return TimeSeries(
        None, name=s.name, description=s.description, unit=s.unit, timezone=s.timezone,
        frequency=Frequency(s.frequency) if s.frequency else None,
        data_type=DataType(s.data_type) if s.data_type else None,
        timeseries_type=TimeSeriesType(s.timeseries_type),
    )


def series_fields_from_timeseries(ts: TimeSeries) -> dict:
    return {
        "name": ts.name, "description": ts.description, "unit": ts.unit,
        "timezone": ts.timezone,
        "frequency": str(ts.frequency) if ts.frequency else None,
        "data_type": ts.data_type.value if ts.data_type else None,
        "timeseries_type": ts.timeseries_type.value,
    }


def upsert_snapshot(conn, *, series_id, ts: TimeSeries, run_id=None, stats=None) -> Snapshot:
    payload = dump_timeseries(ts)
    valid = [v for v in ts.to_list()["valid_time"] if v is not None]
    if not valid:
        raise ValueError("snapshot payload has no valid_time values")
    row = conn.execute(
        """
        INSERT INTO series_snapshot (series_id, run_id, fetched_at, window_start, window_end, payload, stats)
        VALUES (%s, %s, now(), %s, %s, %s, %s)
        ON CONFLICT (series_id) DO UPDATE SET
            run_id = EXCLUDED.run_id, fetched_at = now(),
            window_start = EXCLUDED.window_start, window_end = EXCLUDED.window_end,
            payload = EXCLUDED.payload, stats = EXCLUDED.stats
        RETURNING *
        """,
        (series_id, run_id, min(valid), max(valid), Jsonb(payload),
         Jsonb(stats or {"points": ts.num_rows, "nulls": int(ts.has_missing)})),
    ).fetchone()
    return Snapshot(**row)


def get_snapshot(conn, series_id) -> Snapshot | None:
    row = conn.execute(
        "SELECT * FROM series_snapshot WHERE series_id = %s", (series_id,)
    ).fetchone()
    return Snapshot(**row) if row else None


def snapshot_timeseries(snap: Snapshot):
    return load_timeseries(snap.payload)
```

- [ ] **Step 4: Run to verify pass**, **Step 5: Commit** — `git commit -m "feat: series catalog and latest-only snapshot store"`

---

### Task 7: Check + run store

**Files:**
- Create: `src/watchdogdatamodel/store/checks.py`, `src/watchdogdatamodel/store/runs.py`, `tests/test_store_runs.py`

**Interfaces:**
- Produces:
  - `checks.upsert_check(conn, *, id, name, description=None, dimension=None, default_params=None, enabled=True) -> CheckDef`
  - `checks.list_checks(conn, enabled: bool | None = True) -> list[CheckDef]`
  - `runs.start_run(conn, *, scope, trigger, window_start=None, window_end=None, metadata=None) -> CheckRun` — validates scope via `scope.validate_scope`.
  - `runs.finish_run(conn, run_id, *, status="completed", stats=None) -> CheckRun` — sets `finished_at`; refuses if not `running`.
  - `runs.run_covers(run: CheckRun, *, series: Series, check_id: str) -> bool` — completed runs only; delegates to `scope_covers`.

- [ ] **Step 1: Write the failing tests** — `tests/test_store_runs.py`:
```python
import pytest

from tests.conftest import requires_db

pytestmark = requires_db


def test_check_upsert(conn):
    from watchdogdatamodel.store.checks import list_checks, upsert_check

    upsert_check(conn, id="freshness", name="Freshness", dimension="freshness")
    upsert_check(conn, id="freshness", name="Freshness v2", dimension="freshness")
    checks = list_checks(conn)
    assert [(c.id, c.name) for c in checks] == [("freshness", "Freshness v2")]


def test_run_lifecycle_and_coverage(conn):
    from watchdogdatamodel.store.runs import finish_run, run_covers, start_run
    from watchdogdatamodel.store.series import upsert_series

    s = upsert_series(conn, key="k1", name="n", labels={"zone": "SE-SE1"})
    run = start_run(conn, scope={"series": "all", "checks": "all"}, trigger="scheduled")
    assert run.status == "running"
    assert not run_covers(run, series=s, check_id="freshness")  # not completed yet
    run = finish_run(conn, run.id, stats={"series_checked": 1})
    assert run.status == "completed" and run.finished_at is not None
    assert run_covers(run, series=s, check_id="freshness")
    with pytest.raises(ValueError):
        finish_run(conn, run.id)  # already finished


def test_bad_scope_rejected(conn):
    from watchdogdatamodel.store.runs import start_run

    with pytest.raises(ValueError):
        start_run(conn, scope={"series": "some"}, trigger="manual")
```

- [ ] **Step 2: Run to verify failure**, then **Step 3: Implement** — `src/watchdogdatamodel/store/checks.py`:
```python
"""Check catalog (spec §3.2; physical table check_definition)."""
from psycopg.types.json import Jsonb

from ..models import CheckDef


def upsert_check(conn, *, id, name, description=None, dimension=None,
                 default_params=None, enabled=True) -> CheckDef:
    row = conn.execute(
        """
        INSERT INTO check_definition (id, name, description, dimension, default_params, enabled)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name, description = EXCLUDED.description,
            dimension = EXCLUDED.dimension, default_params = EXCLUDED.default_params,
            enabled = EXCLUDED.enabled, updated_at = now()
        RETURNING *
        """,
        (id, name, description, dimension, Jsonb(default_params or {}), enabled),
    ).fetchone()
    return CheckDef(**row)


def list_checks(conn, enabled: bool | None = True) -> list[CheckDef]:
    q, params = "SELECT * FROM check_definition", []
    if enabled is not None:
        q += " WHERE enabled = %s"
        params.append(enabled)
    q += " ORDER BY id"
    return [CheckDef(**r) for r in conn.execute(q, params).fetchall()]
```
`src/watchdogdatamodel/store/runs.py`:
```python
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
    return scope_covers(run.scope, series_id=str(series.id), labels=series.labels, check_id=check_id)
```

- [ ] **Step 4: Run to verify pass**, **Step 5: Commit** — `git commit -m "feat: check catalog and run lifecycle with coverage"`

---

### Task 8: Issue store (dedup, events, lifecycle)

**Files:**
- Create: `src/watchdogdatamodel/store/issues.py`, `tests/test_store_issues.py`

**Interfaces:**
- Consumes: `models.Issue/IssueEvent`, `RESOLUTION_REASONS`.
- Produces:
  - `open_or_touch(conn, *, fingerprint, origin, title, actor, severity="medium", stage="new", series_id=None, related_series=None, check_id=None, run_id=None, details=None, valid_start=None, valid_end=None, knowledge_time=None) -> tuple[Issue, bool]` — `(issue, opened)`; touch bumps `last_seen_at` + `detected_again` event and NEVER rewrites `details` (evidence immutability); open links `predecessor_id` to the newest resolved issue with the same fingerprint and writes `opened`.
  - `get_issue(conn, issue_id) -> Issue | None`
  - `record_not_seen(conn, issue_id, *, run_id, actor) -> None` — `not_seen` event only (resolution is the caller's policy).
  - `resolve(conn, issue_id, *, reason, actor, comment=None) -> Issue` — raises `ValueError` on unknown reason or non-open issue; `resolved` event.
  - `reopen(conn, issue_id, *, actor, comment=None) -> Issue` — human-initiated only per spec; `reopened` event.
  - `set_stage(conn, issue_id, *, stage, actor) -> Issue` — `stage_changed` event with `{"from": ..., "to": ...}`.
  - `add_event(conn, issue_id, *, type, actor, run_id=None, action_id=None, data=None) -> IssueEvent` (also used by the action store).
  - `list_events(conn, issue_id) -> list[IssueEvent]` (ascending `at, id`).

- [ ] **Step 1: Write the failing tests** — `tests/test_store_issues.py`:
```python
import psycopg
import pytest

from tests.conftest import requires_db

pytestmark = requires_db

FP = "k1|freshness"


def _open(conn, **kw):
    from watchdogdatamodel.store.issues import open_or_touch

    args = dict(
        fingerprint=FP, origin="check", title="stale data", actor="run:test",
        details={"evidence": {"frozen": True}},
    )
    args.update(kw)
    return open_or_touch(conn, **args)


def test_open_then_touch_dedups_and_keeps_evidence(conn):
    from watchdogdatamodel.store.issues import list_events

    issue, opened = _open(conn)
    assert opened and issue.state == "open"
    touched, opened2 = _open(conn, details={"evidence": {"frozen": False}})
    assert not opened2 and touched.id == issue.id
    assert touched.details["evidence"] == {"frozen": True}  # evidence immutable
    assert touched.last_seen_at >= issue.last_seen_at
    assert [e.type for e in list_events(conn, issue.id)] == ["opened", "detected_again"]


def test_db_constraint_blocks_second_open_row(conn):
    _open(conn)
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO issue (fingerprint, origin, title) VALUES (%s, 'check', 't')", (FP,)
        )


def test_resolution_requires_reason_and_links_recurrence(conn):
    from watchdogdatamodel.store.issues import resolve

    issue, _ = _open(conn)
    with pytest.raises(ValueError):
        resolve(conn, issue.id, reason="because", actor="user:davide")
    resolved = resolve(conn, issue.id, reason="recovered", actor="rule:auto")
    assert resolved.state == "resolved" and resolved.resolved_at is not None
    again, opened = _open(conn)
    assert opened and again.id != issue.id and again.predecessor_id == issue.id


def test_events_append_only_at_db_level(conn):
    from watchdogdatamodel.store.issues import list_events

    issue, _ = _open(conn)
    ev = list_events(conn, issue.id)[0]
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE issue_event SET actor = 'x' WHERE id = %s", (ev.id,))
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM issue_event WHERE id = %s", (ev.id,))


def test_not_seen_stage_and_reopen(conn):
    from watchdogdatamodel.store.issues import (
        list_events, record_not_seen, reopen, resolve, set_stage,
    )

    issue, _ = _open(conn)
    set_stage(conn, issue.id, stage="healing", actor="user:davide")
    record_not_seen(conn, issue.id, run_id=None, actor="run:test")
    resolve(conn, issue.id, reason="recovered", actor="rule:auto")
    back = reopen(conn, issue.id, actor="user:davide", comment="not actually fixed")
    assert back.state == "open"
    types = [e.type for e in list_events(conn, issue.id)]
    assert types == ["opened", "stage_changed", "not_seen", "resolved", "reopened"]
```

- [ ] **Step 2: Run to verify failure**, then **Step 3: Implement** — `src/watchdogdatamodel/store/issues.py`:
```python
"""Issues: fingerprint-deduped problems + their append-only diary (spec §3.4, §3.5)."""
import psycopg
from psycopg.types.json import Jsonb

from ..models import RESOLUTION_REASONS, Issue, IssueEvent


def add_event(conn, issue_id, *, type, actor, run_id=None, action_id=None, data=None) -> IssueEvent:
    row = conn.execute(
        """
        INSERT INTO issue_event (issue_id, type, actor, run_id, action_id, data)
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING *
        """,
        (issue_id, type, actor, run_id, action_id, Jsonb(data or {})),
    ).fetchone()
    return IssueEvent(**row)


def list_events(conn, issue_id) -> list[IssueEvent]:
    rows = conn.execute(
        "SELECT * FROM issue_event WHERE issue_id = %s ORDER BY at, id", (issue_id,)
    ).fetchall()
    return [IssueEvent(**r) for r in rows]


def get_issue(conn, issue_id) -> Issue | None:
    row = conn.execute("SELECT * FROM issue WHERE id = %s", (issue_id,)).fetchone()
    return Issue(**row) if row else None


def open_or_touch(conn, *, fingerprint, origin, title, actor, severity="medium",
                  stage="new", series_id=None, related_series=None, check_id=None,
                  run_id=None, details=None, valid_start=None, valid_end=None,
                  knowledge_time=None) -> tuple[Issue, bool]:
    with conn.transaction():
        row = conn.execute(
            "SELECT id FROM issue WHERE fingerprint = %s AND state = 'open' FOR UPDATE",
            (fingerprint,),
        ).fetchone()
        if row:
            updated = conn.execute(
                """
                UPDATE issue SET last_seen_at = now(), updated_at = now()
                WHERE id = %s RETURNING *
                """,
                (row["id"],),
            ).fetchone()
            add_event(conn, row["id"], type="detected_again", actor=actor, run_id=run_id)
            return Issue(**updated), False

        pred = conn.execute(
            """
            SELECT id FROM issue WHERE fingerprint = %s AND state = 'resolved'
            ORDER BY resolved_at DESC LIMIT 1
            """,
            (fingerprint,),
        ).fetchone()
        try:
            created = conn.execute(
                """
                INSERT INTO issue (fingerprint, origin, series_id, related_series, check_id,
                                   stage, severity, title, details, valid_start, valid_end,
                                   knowledge_time, detected_by_run, predecessor_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (fingerprint, origin, series_id, Jsonb(related_series or []), check_id,
                 stage, severity, title, Jsonb(details or {}), valid_start, valid_end,
                 knowledge_time, run_id, pred["id"] if pred else None),
            ).fetchone()
        except psycopg.errors.UniqueViolation:
            # Lost a race with a concurrent opener: fall through to touch.
            pass
        else:
            add_event(conn, created["id"], type="opened", actor=actor, run_id=run_id)
            return Issue(**created), True
    return open_or_touch(
        conn, fingerprint=fingerprint, origin=origin, title=title, actor=actor,
        severity=severity, stage=stage, series_id=series_id,
        related_series=related_series, check_id=check_id, run_id=run_id,
        details=details, valid_start=valid_start, valid_end=valid_end,
        knowledge_time=knowledge_time,
    )


def record_not_seen(conn, issue_id, *, run_id, actor) -> None:
    add_event(conn, issue_id, type="not_seen", actor=actor, run_id=run_id)


def resolve(conn, issue_id, *, reason, actor, comment=None) -> Issue:
    if reason not in RESOLUTION_REASONS:
        raise ValueError(f"unknown resolution reason {reason!r}; allowed: {sorted(RESOLUTION_REASONS)}")
    with conn.transaction():
        row = conn.execute(
            """
            UPDATE issue SET state = 'resolved', resolved_at = now(),
                   resolution_reason = %s, resolution_comment = %s, resolved_by = %s,
                   updated_at = now()
            WHERE id = %s AND state = 'open' RETURNING *
            """,
            (reason, comment, actor, issue_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"issue {issue_id} is not open")
        add_event(conn, issue_id, type="resolved", actor=actor,
                  data={"reason": reason, "comment": comment})
    return Issue(**row)


def reopen(conn, issue_id, *, actor, comment=None) -> Issue:
    with conn.transaction():
        row = conn.execute(
            """
            UPDATE issue SET state = 'open', resolved_at = NULL, resolution_reason = NULL,
                   resolution_comment = NULL, resolved_by = NULL, updated_at = now()
            WHERE id = %s AND state = 'resolved' RETURNING *
            """,
            (issue_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"issue {issue_id} is not resolved")
        add_event(conn, issue_id, type="reopened", actor=actor, data={"comment": comment})
    return Issue(**row)


def set_stage(conn, issue_id, *, stage, actor) -> Issue:
    with conn.transaction():
        row = conn.execute(
            """
            UPDATE issue SET stage = %s, updated_at = now()
            WHERE id = %s RETURNING *,
                  (SELECT stage FROM issue WHERE id = %s) AS _old
            """,
            (stage, issue_id, issue_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"issue {issue_id} not found")
        add_event(conn, issue_id, type="stage_changed", actor=actor,
                  data={"from": row["_old"], "to": stage})
    return Issue(**row)
```
Note: the `_old` subselect sees the pre-UPDATE stage only because the subquery
runs on the table's snapshot before the row lock releases; if this proves
flaky under test, replace with a plain SELECT-then-UPDATE inside the same
transaction (same behavior, one extra statement).

- [ ] **Step 4: Run to verify pass** — `uv run python -m pytest tests/test_store_issues.py -v`. If the `_old` trick returns the new value on the installed Postgres, apply the SELECT-then-UPDATE variant and re-run.
- [ ] **Step 5: Commit** — `git commit -m "feat: issue store with fingerprint dedup, lifecycle, append-only diary"`

---

### Task 9: Action store

**Files:**
- Create: `src/watchdogdatamodel/store/actions.py`, `tests/test_store_actions.py`

**Interfaces:**
- Consumes: `issues.add_event`, `models.Action`, `TERMINAL_ACTION_STATUSES`.
- Produces:
  - `enqueue(conn, issue_id, type, *, requested_by, params=None) -> tuple[Action, bool]` — `(action, created)`; duplicate live (queued/running) action → returns the existing one, `created=False`, no event.
  - `claim_next(conn, type, *, worker) -> Action | None` — oldest queued of that type, `FOR UPDATE SKIP LOCKED`; sets running + `started_at`, appends transition.
  - `finish(conn, action_id, *, status, by, outcome=None) -> Action` — terminal statuses only; sets `finished_at`, merges outcome, appends transition, emits `action_finished`.
  - `cancel(conn, action_id, *, by) -> Action` — `finish` shorthand with `status="canceled"`.
  - `get_action(conn, action_id) -> Action | None`

- [ ] **Step 1: Write the failing tests** — `tests/test_store_actions.py`:
```python
import psycopg
import pytest

from tests.conftest import requires_db

pytestmark = requires_db


def _issue(conn):
    from watchdogdatamodel.store.issues import open_or_touch

    issue, _ = open_or_touch(
        conn, fingerprint="k|gap", origin="check", title="gap", actor="run:test"
    )
    return issue


def test_enqueue_is_idempotent_per_issue_and_type(conn):
    from watchdogdatamodel.store.actions import enqueue
    from watchdogdatamodel.store.issues import list_events

    issue = _issue(conn)
    a1, created1 = enqueue(conn, issue.id, "backfill", requested_by="rule:auto")
    a2, created2 = enqueue(conn, issue.id, "backfill", requested_by="rule:auto")
    assert created1 and not created2 and a1.id == a2.id
    reqs = [e for e in list_events(conn, issue.id) if e.type == "action_requested"]
    assert len(reqs) == 1 and reqs[0].action_id == a1.id


def test_claim_transition_finish_freeze(conn):
    from watchdogdatamodel.store.actions import claim_next, enqueue, finish
    from watchdogdatamodel.store.issues import list_events

    issue = _issue(conn)
    a, _ = enqueue(conn, issue.id, "backfill", requested_by="rule:auto")
    assert claim_next(conn, "notify", worker="w1") is None
    running = claim_next(conn, "backfill", worker="w1")
    assert running.id == a.id and running.status == "running" and running.started_at
    done = finish(conn, a.id, status="succeeded", by="w1", outcome={"rows": 42})
    assert done.status == "succeeded" and done.finished_at and done.outcome == {"rows": 42}
    assert [t["status"] for t in done.transitions] == ["running", "succeeded"]
    with pytest.raises(ValueError):
        finish(conn, a.id, status="failed", by="w1")  # already terminal (API)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE action SET status = 'queued' WHERE id = %s", (a.id,))  # DB freeze
    finishes = [e for e in list_events(conn, issue.id) if e.type == "action_finished"]
    assert len(finishes) == 1 and finishes[0].data["status"] == "succeeded"


def test_after_terminal_a_new_action_can_be_enqueued(conn):
    from watchdogdatamodel.store.actions import enqueue, finish

    issue = _issue(conn)
    a, _ = enqueue(conn, issue.id, "backfill", requested_by="rule:auto")
    finish(conn, a.id, status="failed", by="w1")
    b, created = enqueue(conn, issue.id, "backfill", requested_by="user:davide")
    assert created and b.id != a.id
```

- [ ] **Step 2: Run to verify failure**, then **Step 3: Implement** — `src/watchdogdatamodel/store/actions.py`:
```python
"""Actions: the typed remediation work queue (spec §3.6)."""
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

from ..models import TERMINAL_ACTION_STATUSES, Action
from .issues import add_event


def _transition(status: str, by: str) -> dict:
    return {"status": status, "at": datetime.now(timezone.utc).isoformat(), "by": by}


def get_action(conn, action_id) -> Action | None:
    row = conn.execute("SELECT * FROM action WHERE id = %s", (action_id,)).fetchone()
    return Action(**row) if row else None


def enqueue(conn, issue_id, type, *, requested_by, params=None) -> tuple[Action, bool]:
    with conn.transaction():
        row = conn.execute(
            """
            INSERT INTO action (issue_id, type, params, requested_by, transitions)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (issue_id, type) WHERE status IN ('queued', 'running')
            DO NOTHING
            RETURNING *
            """,
            (issue_id, type, Jsonb(params or {}), requested_by,
             Jsonb([_transition("queued", requested_by)])),
        ).fetchone()
        if row is not None:
            add_event(conn, issue_id, type="action_requested", actor=requested_by,
                      action_id=row["id"], data={"action_type": type})
            return Action(**row), True
    existing = conn.execute(
        """
        SELECT * FROM action
        WHERE issue_id = %s AND type = %s AND status IN ('queued', 'running')
        """,
        (issue_id, type),
    ).fetchone()
    return Action(**existing), False


def claim_next(conn, type, *, worker) -> Action | None:
    row = conn.execute(
        """
        UPDATE action SET status = 'running', started_at = now(),
               transitions = transitions || %s::jsonb
        WHERE id = (
            SELECT id FROM action WHERE status = 'queued' AND type = %s
            ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED
        )
        RETURNING *
        """,
        (Jsonb([_transition("running", worker)]), type),
    ).fetchone()
    return Action(**row) if row else None


def finish(conn, action_id, *, status, by, outcome=None) -> Action:
    if status not in TERMINAL_ACTION_STATUSES:
        raise ValueError(f"finish status must be one of {sorted(TERMINAL_ACTION_STATUSES)}")
    with conn.transaction():
        row = conn.execute(
            """
            UPDATE action SET status = %s, finished_at = now(),
                   outcome = outcome || %s::jsonb,
                   transitions = transitions || %s::jsonb
            WHERE id = %s AND status IN ('queued', 'running')
            RETURNING *
            """,
            (status, Jsonb(outcome or {}), Jsonb([_transition(status, by)]), action_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"action {action_id} is terminal or missing")
        add_event(conn, row["issue_id"], type="action_finished", actor=by,
                  action_id=action_id, data={"action_type": row["type"], "status": status})
    return Action(**row)


def cancel(conn, action_id, *, by) -> Action:
    return finish(conn, action_id, status="canceled", by=by)
```

- [ ] **Step 4: Run to verify pass**, **Step 5: Commit** — `git commit -m "feat: action work queue with idempotent enqueue and terminal freeze"`

---

### Task 10: Public API surface + adopter's guide + full suite

**Files:**
- Modify: `src/watchdogdatamodel/__init__.py` (re-exports), `README.md` (status line)
- Create: `docs/adopters-guide.md`, `tests/test_public_api.py`

**Interfaces:**
- Produces: flat public API — `from watchdogdatamodel import compute_fingerprint, scope_covers, validate_scope, dump_timeseries, load_timeseries, excerpt` plus `models` and `store` submodules.

- [ ] **Step 1: Write the failing test** — `tests/test_public_api.py`:
```python
def test_flat_public_api():
    import watchdogdatamodel as w

    for name in (
        "compute_fingerprint", "scope_covers", "validate_scope",
        "dump_timeseries", "load_timeseries", "excerpt", "models", "store",
    ):
        assert hasattr(w, name), name
```

- [ ] **Step 2: Implement re-exports** — `src/watchdogdatamodel/__init__.py`:
```python
"""watchdogdatamodel — generalized data model for timeseries quality ops."""
from . import models, store
from .evidence import excerpt
from .fingerprint import compute_fingerprint
from .scope import scope_covers, validate_scope
from .wire import dump_timeseries, load_timeseries

__version__ = "0.1.0"

__all__ = [
    "models", "store", "excerpt", "compute_fingerprint",
    "scope_covers", "validate_scope", "dump_timeseries", "load_timeseries",
]
```
Note: `store/__init__.py` must import its submodules for `store.series` etc. to be reachable:
```python
from . import actions, checks, db, issues, runs, series  # noqa: F401
```

- [ ] **Step 3: Write the adopter's guide** — `docs/adopters-guide.md`: how a product adopts the model in five steps (create DB + `bootstrap`; `upsert_series` for every watched series with your labels; `upsert_check` for your checks; per detection cycle `start_run`→`open_or_touch`/`record_not_seen`→`finish_run`; register action types and run a worker loop on `claim_next`/`finish`). Include a complete ~40-line runnable example script using an in-repo fake check. Cover the two rules adopters must internalize: healthy = covered + no open issue; finishing an action never resolves an issue.

- [ ] **Step 4: Update README status** — change "Design approved; implementation starting." to "Core implemented (schema, package, contract tests); PSD watchdog adoption is the next phase." and add a pointer to the adopter's guide.

- [ ] **Step 5: Full suite** — `uv run python -m pytest -v` with DSN exported. Expected: all tests pass, none skipped.
- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: public API, adopter's guide, README status"`

---

## Self-review notes

- Spec coverage: §3.1→Task 6, §3.2→Task 7, §3.3→Task 7, §3.4/3.5→Task 8, §3.6→Task 9, §3.7→Task 6, §6 package/helpers→Tasks 1–10, §6 contract tests→Tasks 3, 6, 8, 9 (all seven guarantees have explicit tests). §6 docs→Task 10. §7 rollout beyond the repo is out of scope here (next plan).
- Naming: `check_definition` (physical) / `CheckDef` (model) / `check_id` (columns) used consistently across Tasks 3–9. `"trigger"` quoted in all SQL.
- Known risk: timedatamodel's published API may differ slightly from the repo snapshot (enum import paths). Task 5 Step 4 covers the adjustment procedure.
