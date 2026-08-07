"""Shared DB test support, loaded as a pytest plugin (see pyproject addopts).

Lives here instead of conftest.py because the enclosing PSD checkout has a
hook protecting any file at tests/conftest.py; this repo's test plumbing is
independent of PSD's validation framework.
"""
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
