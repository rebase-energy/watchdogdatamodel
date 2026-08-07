from datetime import timedelta

from watchdogdatamodel.evidence import excerpt
from watchdogdatamodel.wire import load_timeseries

from tests.test_wire import T0, hourly


def test_excerpt_restricts_window():
    e = excerpt(hourly(n=24), T0 + timedelta(hours=5), T0 + timedelta(hours=7))
    ts = load_timeseries(e)
    assert ts.num_rows == 3
    assert ts.to_list()["valid_time"][0] == T0 + timedelta(hours=5)
