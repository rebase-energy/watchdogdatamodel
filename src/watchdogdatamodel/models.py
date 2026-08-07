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
