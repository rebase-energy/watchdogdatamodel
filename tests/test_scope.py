import pytest

from watchdogdatamodel.scope import scope_covers, validate_scope

LBL = {"zone": "SE-SE1", "data_type": "production", "source": "entsoe"}


def test_all_all_covers_everything():
    s = validate_scope({"series": "all", "checks": "all"})
    assert scope_covers(s, series_id="x", labels=LBL, check_id="freshness")


def test_label_filter_subset_match():
    s = {"series": {"labels": {"zone": "SE-SE1"}}, "checks": "all"}
    assert scope_covers(s, series_id="x", labels=LBL, check_id="freshness")
    assert not scope_covers(s, series_id="x", labels={"zone": "FI"}, check_id="freshness")


def test_label_list_means_membership():
    s = {"series": {"labels": {"zone": ["SE-SE1", "FI"]}}, "checks": "all"}
    assert scope_covers(s, series_id="x", labels=LBL, check_id="freshness")
    assert not scope_covers(s, series_id="x", labels={"zone": "DE-LU"}, check_id="freshness")


def test_id_list_and_check_list():
    s = {"series": {"ids": ["a", "b"]}, "checks": ["gross_range"]}
    assert scope_covers(s, series_id="a", labels={}, check_id="gross_range")
    assert not scope_covers(s, series_id="c", labels={}, check_id="gross_range")
    assert not scope_covers(s, series_id="a", labels={}, check_id="freshness")


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"series": "all"},
        {"series": "some", "checks": "all"},
        {"series": {"labels": {}, "ids": []}, "checks": "all"},
        {"series": "all", "checks": "none"},
    ],
)
def test_invalid_scopes_rejected(bad):
    with pytest.raises(ValueError):
        validate_scope(bad)
