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
