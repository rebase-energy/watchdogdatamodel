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
