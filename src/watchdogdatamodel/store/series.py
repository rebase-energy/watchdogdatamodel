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
