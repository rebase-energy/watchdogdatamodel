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
