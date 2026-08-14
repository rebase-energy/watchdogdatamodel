"""Smoke tests for the read-only CLI: one per command group, light by directive."""
import subprocess
import sys

import pytest

from tests.dbsupport import DSN, requires_db

CMDS = [
    ["guide"],
    ["checks", "list"],
    ["issues", "list", "--limit", "5"],
    ["runs", "list", "--limit", "3"],
    ["stats", "--by", "kind"],
]


def _run(args, dsn=None):
    return subprocess.run(
        [sys.executable, "-m", "watchdogdatamodel.cli", *args]
        + (["--dsn", dsn] if dsn else []),
        capture_output=True, text=True, encoding="utf-8",
    )


@requires_db
@pytest.mark.parametrize("args", CMDS, ids=lambda a: "-".join(a))
def test_command_runs(args):
    # Exit status only: an empty table is a legitimate answer for the list
    # commands, and asserting non-empty stdout would make this test depend on
    # whatever rows other test modules happened to leave behind (they truncate).
    # `guide` is the one command whose output is data-independent, so it is the
    # one that must print something.
    res = _run(args, dsn=DSN)
    assert res.returncode == 0, res.stderr
    if args == ["guide"]:
        assert res.stdout.strip()


def test_missing_dsn_is_loud_not_silent():
    res = _run(["issues", "list"], dsn="postgresql://nope@127.0.0.1:1/none_test")
    assert res.returncode == 2
    assert "no wdm access" in (res.stdout + res.stderr).lower()
