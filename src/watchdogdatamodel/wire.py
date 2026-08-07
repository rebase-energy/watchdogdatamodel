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
