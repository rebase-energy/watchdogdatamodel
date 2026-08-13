from tests.dbsupport import requires_db
from watchdogdatamodel.store.checks import list_checks, upsert_check


@requires_db
def test_contract_round_trips(conn):
    upsert_check(
        conn, id="timing_gaps", name="Timing Gaps",
        description="one-liner",
        default_params={"applies_to": {"raw": "issue"}},
        contract={"asserts": "no gap > 45 min", "routing": {"grace_hours": 0}},
    )
    got = [c for c in list_checks(conn) if c.id == "timing_gaps"][0]
    assert got.contract["asserts"] == "no gap > 45 min"
    assert got.contract["routing"]["grace_hours"] == 0
    assert got.default_params == {"applies_to": {"raw": "issue"}}


@requires_db
def test_contract_defaults_to_null(conn):
    upsert_check(conn, id="unreachable", name="Unreachable")
    got = [c for c in list_checks(conn) if c.id == "unreachable"][0]
    assert got.contract is None

    # Verify the on-disk representation is actually SQL NULL, not a jsonb 'null'.
    # psycopg deserializes both back to Python None, so the above assertion alone
    # cannot detect if the column holds true NULL or a JSON null.
    row = conn.execute(
        "SELECT contract IS NULL as is_null FROM check_definition WHERE id = %s", ("unreachable",)
    ).fetchone()
    assert row["is_null"] is True, "contract must be SQL NULL, not a jsonb 'null'"
