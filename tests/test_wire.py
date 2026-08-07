from datetime import datetime, timedelta, timezone

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
